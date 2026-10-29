"""单机工作台调度器。任务不属于 HTTP/WebSocket 连接。"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
from typing import Any
from uuid import uuid4

from guild_manager_bench.api.routes_llm_debug import _debug_agent
from guild_manager_bench.api.api_profiles import ApiProfiles
from guild_manager_bench.api.workbench_events import apply_event, set_phase
from guild_manager_bench.api.workbench_models import CreateBatch, RunSpec, SchedulerSettings
from guild_manager_bench.api.workbench_lock import FileLease, archive_lease
from guild_manager_bench.api.workbench_watch import WatchJournal
from guild_manager_bench.api.history_deletion import ArchiveBusyError, history_directory, stage_history_deletion
from guild_manager_bench.api.monster_display import monster_stages
from guild_manager_bench.game.presets import DataPreset
from guild_manager_bench.bench.llm.recovery import RecoveryConfirmationRequired, inherited_config, inspect_recovery, read_recovery_archive, recoverable_turns

ACTIVE = {"queued", "starting", "running", "stopping"}

# 公开摘要只携带运行元数据。局面通过选中任务的 watch 接口按需读取，
# 不把内部状态或未来新增的大字段自动带入全局轮询。
JOB_SUMMARY_FIELDS = frozenset({
    "id", "name", "batch_id", "batch_name", "status", "phase", "phase_started_at",
    "created_at", "updated_at", "started_at", "ended_at", "config", "effective_config",
    "data", "archive_id", "turn", "max_turns", "last_score", "scores", "calls", "tools",
    "usage", "model_duration_ms", "retries", "tool_failures", "error", "notice", "cursor",
    "checkpoint_at", "checkpoint_error", "budget", "recent_actions",
})


def job_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if key in JOB_SUMMARY_FIELDS}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def read_events(path: Path, after: int, limit: int = 200) -> dict[str, Any]:
    """字节游标只提交完整行，允许读取正在追加的日志。"""
    records: list[dict[str, Any]] = []
    cursor = after
    if not path.exists():
        return {"events": records, "cursor": cursor, "has_more": False}
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        if after > size:
            raise ValueError("事件游标超出日志范围")
        stream.seek(after)
        if after:
            stream.seek(after - 1)
            if stream.read(1) != b"\n":
                raise ValueError("事件游标必须指向完整记录边界")
        while len(records) < limit:
            line = stream.readline()
            if not line or not line.endswith(b"\n"):
                break
            record = json.loads(line)
            cursor = stream.tell()
            records.append({**record, "cursor": cursor})
    return {"events": records, "cursor": cursor, "has_more": cursor < size and bool(records)}


class Workbench:
    def __init__(self, root: Path, archive_root: Path, presets: dict[str, DataPreset], *, stop_timeout: float = 8) -> None:
        self.root = root
        self.archive_root = archive_root
        self.presets = presets
        self.stop_timeout = stop_timeout
        self.jobs: dict[str, dict[str, Any]] = {}
        self.settings = SchedulerSettings()
        self.api_profiles = ApiProfiles(root)
        self._payloads: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._stopping_since: dict[str, float] = {}
        self._pump: asyncio.Task | None = None
        self.failure: str | None = None
        self._ownership = None
        self._watch: dict[str, WatchJournal] = {}

    async def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._acquire()
        try:
            settings = self.root / "settings.json"
            if settings.exists():
                self.settings = SchedulerSettings.model_validate_json(settings.read_text(encoding="utf-8"))
            for path in sorted(self.root.glob("*/state.json")):
                job = json.loads(path.read_text(encoding="utf-8"))
                self.jobs[job["id"]] = job
                # 服务重启不恢复凭证、不自动重复请求。保留检查点供显式续跑。
                if job["status"] in ACTIVE:
                    self._consume(job, drain=True)
                    job.update(status="interrupted", ended_at=timestamp(), error="工作台服务已重启，请确认检查点后续跑")
                    set_phase(job, "idle", timestamp())
                    self._save(job)
            self._pump = asyncio.create_task(self._run())
        except BaseException:
            self._release()
            raise

    async def close(self) -> None:
        for job in list(self.jobs.values()):
            if job["status"] in ACTIVE:
                self.stop(job["id"])
        deadline = asyncio.get_running_loop().time() + self.stop_timeout + 3
        while self._processes and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(.1)
        for process in list(self._processes.values()):
            await asyncio.to_thread(self._kill_tree, process)
        pump_error = None
        if self._pump:
            self._pump.cancel()
            try:
                await self._pump
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                pump_error = exc
        for job_id, process in list(self._processes.items()):
            await asyncio.to_thread(process.wait)
            self._finish(self.jobs[job_id], process)
        self._release()
        if pump_error is not None:
            raise pump_error

    def _acquire(self) -> None:
        lease = FileLease(self.root / "owner.lock")
        try:
            lease.__enter__()
        except OSError as exc:
            raise RuntimeError("该工作台目录已由其他服务管理，请使用单个服务进程") from exc
        self._ownership = lease

    def _release(self) -> None:
        if self._ownership is not None:
            self._ownership.__exit__()
            self._ownership = None

    def snapshot(self) -> dict[str, Any]:
        return {"jobs": [job_summary(job) for job in self.jobs.values()], "settings": self.settings.model_dump(),
                "presets": list(self.presets), "scheduler_error": self.failure}

    def configure(self, settings: SchedulerSettings) -> dict[str, int]:
        result = settings.model_dump()
        write_json(self.root / "settings.json", result)
        self.settings = settings
        return result

    def get(self, job_id: str) -> dict[str, Any]:
        return self.jobs[job_id]

    def events(self, job_id: str, after: int, limit: int) -> dict[str, Any]:
        self.get(job_id)
        return read_events(self.root / job_id / "events.jsonl", after, limit)

    def watch(self, job_id: str, attempt: int | None = None) -> dict[str, Any]:
        self.get(job_id)
        if job_id not in self._watch:
            self._watch[job_id] = WatchJournal(self.root / job_id / "events.jsonl")
        return {**self._watch[job_id].read(attempt), "monster_stages": self.monster_stages(job_id)}

    def monster_stages(self, job_id: str) -> dict[str, str]:
        job = self.get(job_id)
        return monster_stages(self.presets.get(job["config"]["preset"]))

    def recovery_info(self, run_id: str) -> dict[str, Any]:
        directory = self._archive(run_id)
        try:
            with archive_lease(directory):
                replay, _ = read_recovery_archive(directory)
                preset = replay.get("data", {}).get("preset")
                if preset not in self.presets:
                    raise ValueError("存档没有可用的原 preset，不能恢复")
                plan = inspect_recovery(directory, self.presets[preset].data_dir, 1)
        except OSError as exc:
            raise ValueError("存档仍被工作进程占用或暂不可读，请等待原进程退出") from exc
        agent = replay.get("agent", {})
        safe = ("model", "base_url", "reasoning_effort", "effort", "thinking", "timeout", "temperature", "max_tokens", "max_output_tokens")
        return {"run_id": run_id, "status": replay.get("status"), "preset": preset,
                "agent": {"type": agent.get("type"), "config": {key: agent.get("config", {})[key] for key in safe if key in agent.get("config", {})}},
                "config": inherited_config(replay), "max_rerun_turn": plan.max_rerun_turn,
                "next_turn": recoverable_turns(replay)}

    def _recovery_plans(self, request: CreateBatch):
        locked = {identity for job in list(self.jobs.values()) if job["status"] in ACTIVE
                  for identity in (job["archive_id"], job["config"].get("resume_run_id"), job["config"].get("rerun_run_id")) if identity}
        plans = {}
        for index, spec in enumerate(request.runs):
            if spec.preset not in self.presets:
                raise ValueError(f"未知 preset：{spec.preset}")
            source = spec.resume_run_id or spec.rerun_run_id
            if not source:
                continue
            if source in locked:
                raise ValueError("这份存档已经有运行或排队中的任务")
            directory = self._archive(source)
            try:
                with archive_lease(directory):
                    plans[index] = inspect_recovery(directory, self.presets[spec.preset].data_dir, spec.rerun_from_turn)
            except OSError as exc:
                raise ValueError("存档仍被工作进程占用或暂不可读，请等待原进程退出") from exc
            locked.add(source)
        return plans

    def check_recoveries(self, request: CreateBatch) -> dict[str, Any]:
        return {"recoveries": [plan.public(index) for index, plan in self._recovery_plans(request).items()]}

    def create_batch(self, request: CreateBatch) -> dict[str, Any]:
        if self.failure:
            raise ValueError("调度器不可用，请检查服务日志后重启")
        if sum(job["status"] in ACTIVE for job in self.jobs.values()) + len(request.runs) > 128:
            raise ValueError("待处理任务最多 128 个，请等待现有任务完成")
        plans = self._recovery_plans(request)
        required = []
        for index, plan in plans.items():
            try:
                plan.authorize(request.runs[index].version_confirmation)
            except RecoveryConfirmationRequired:
                required.append(plan.public(index))
        if required:
            raise RecoveryConfirmationRequired(required)
        prepared = []
        for index, spec in enumerate(request.runs):
            if spec.preset not in self.presets:
                raise ValueError(f"未知 preset：{spec.preset}")
            payload = spec.model_dump(exclude={"api_key"})
            if spec.api_key:
                payload["api_key"] = spec.api_key.get_secret_value()
            if spec.profile_id:
                try:
                    connection = self.api_profiles.resolve(spec.profile_id)
                except KeyError as exc:
                    raise ValueError("API 配置已不存在，请重新选择配置或切换为自定义连接") from exc
                payload.update(provider=connection['provider'], base_url=connection['base_url'], api_key=connection['api_key'],
                               model=spec.model or connection['model'], api_profile_name=connection['api_profile_name'])
            if index in plans:
                payload.update(inherited_config(plans[index].replay))
                payload["recovery_token"] = plans[index].token
            agent = _debug_agent(payload)
            effective = agent.config
            # 环境变量同样经过安全 URL 检查，不能把带认证的地址写入任务元数据。
            base_url = RunSpec.safe_url(effective.base_url)
            payload.update(model=effective.model, base_url=base_url, api_key=effective.api_key)
            for key in ("timeout", "temperature", "thinking"):
                if hasattr(effective, key):
                    payload[key] = getattr(effective, key)
            payload["reasoning_effort"] = getattr(effective, "reasoning_effort", getattr(effective, "effort", None))
            payload["max_tokens"] = getattr(effective, "max_tokens", getattr(effective, "max_output_tokens", None))
            public = {k: v for k, v in payload.items() if k != "api_key"}
            public["credential_present"] = bool(effective.api_key)
            prepared.append((spec, payload, public))
        batch_id = uuid4().hex
        result = []
        for spec, payload, public in prepared:
            job_id = uuid4().hex
            now = timestamp()
            job = {
                "id": job_id, "name": spec.name or public["model"], "batch_id": batch_id, "batch_name": request.name,
                "status": "queued", "phase": "queued", "phase_started_at": now,
                "created_at": now, "updated_at": now, "started_at": None, "ended_at": None,
                "config": public, "effective_config": {}, "data": {}, "archive_id": spec.resume_run_id,
                "turn": None, "max_turns": None, "last_score": None, "scores": [], "calls": 0, "tools": 0,
                "usage": {"input_tokens": None, "output_tokens": None, "reported": 0, "responses": 0},
                "model_duration_ms": None,
                "retries": 0, "tool_failures": 0, "recent_actions": [], "observation": None,
                "budget": None, "error": None, "notice": None, "checkpoint_at": None, "checkpoint_error": None,
                "cursor": 0, "outcome": None,
            }
            (self.root / job_id).mkdir()
            self._save(job)
            result.append(job)
        # 所有任务元数据落盘后才发布队列，写盘失败不能留下意外收费的半个批次。
        for job, (_, payload, _) in zip(result, prepared):
            self.jobs[job["id"]] = job
            self._payloads[job["id"]] = payload
        return {"batch_id": batch_id, "jobs": result}

    def stop(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] == "queued":
            job.update(status="interrupted", ended_at=timestamp(), error="启动前已取消")
            set_phase(job, "idle", timestamp())
            self._payloads.pop(job_id)
        elif job["status"] in {"starting", "running"}:
            job["status"] = "stopping"
            self._stopping_since[job_id] = asyncio.get_running_loop().time()
            process = self._processes[job_id]
            try:
                process.stdin.write('{"type":"stop"}\n')
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                # 进程可能已经退出，由调度循环回收后确认终态。
                pass
        self._save(job)
        return job

    def delete_job(self, job_id: str) -> dict[str, Any]:
        selected = self.get(job_id)
        if selected["status"] in ACTIVE or job_id in self._processes:
            raise ArchiveBusyError("请先停止任务，等待进程退出后再删除")
        if selected["archive_id"]:
            return self.delete_archive(selected["archive_id"])
        return self._delete_history(None, [selected])

    def delete_archive(self, run_id: str) -> dict[str, Any]:
        archive = history_directory(self.archive_root, run_id)
        def references_archive(identity: str | None) -> bool:
            return identity is not None and (self.archive_root / identity).resolve() == archive

        for job in self.jobs.values():
            references = (job["archive_id"], job["config"].get("resume_run_id"), job["config"].get("rerun_run_id"))
            if any(references_archive(identity) for identity in references) and (job["status"] in ACTIVE or job["id"] in self._processes):
                raise ArchiveBusyError("存档正被排队或执行中的任务使用，请先停止任务")
        related = [job for job in self.jobs.values() if references_archive(job["archive_id"])]
        if not related and not (archive / "replay.json").is_file():
            raise KeyError(run_id)
        return self._delete_history(archive, related)

    def _delete_history(self, archive: Path | None, related: list[dict[str, Any]]) -> dict[str, Any]:
        executions = [history_directory(self.root, job["id"]) for job in related]
        staged = stage_history_deletion(archive, executions)
        deleted_ids = [job["id"] for job in related]
        for identity in deleted_ids:
            self.jobs.pop(identity)
            self._payloads.pop(identity, None)
            self._watch.pop(identity, None)
            self._stopping_since.pop(identity, None)
        for directory in staged:
            shutil.rmtree(directory)
        return {"deleted": True, "run_id": archive.name if archive is not None else None, "deleted_job_ids": deleted_ids}

    def _archive(self, run_id: str) -> Path:
        directory = (self.archive_root / run_id).resolve()
        if directory.parent != self.archive_root.resolve():
            raise ValueError("无效的存档路径")
        if not (directory / "replay.json").is_file():
            raise ValueError("找不到续跑存档")
        return directory

    def _save(self, job: dict[str, Any]) -> None:
        write_json(self.root / job["id"] / "state.json", job)

    def _consume(self, job: dict[str, Any], *, drain: bool = False) -> None:
        # 运行中单轮有界消费。进程退出后读取至末尾，确保不遗漏终态事件。
        pages = 0
        while drain or pages < 4:
            pages += 1
            page = self.events(job["id"], job["cursor"], 200)
            for record in page["events"]:
                apply_event(job, record["event"], record["at"])
                job["cursor"] = record["cursor"]
            if not page["has_more"]:
                break

    def _launch(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        payload = self._payloads.pop(job_id)
        preset = self.presets[payload["preset"]]
        request = {"payload": payload, "data_dir": str(preset.data_dir.resolve()), "data_source": preset.to_dict(),
                   "archive_root": str(self.archive_root.resolve()),
                   "resume_dir": str(self._archive(payload["resume_run_id"] or payload["rerun_run_id"])) if payload["resume_run_id"] or payload["rerun_run_id"] else None}
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        job.update(status="starting", started_at=timestamp())
        set_phase(job, "starting", timestamp())
        self._save(job)
        log = (self.root / job_id / "stderr.log").open("wb")
        try:
            process = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-m", "guild_manager_bench.api.workbench_worker", str((self.root / job_id).resolve())],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log, text=True, encoding="utf-8", **kwargs,
            )
        finally:
            log.close()
        self._processes[job_id] = process
        job["status"] = "running"
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            job["error"] = "工作进程启动失败，请检查该任务的 stderr.log"

    def _finish(self, job: dict[str, Any], process: subprocess.Popen) -> None:
        self._consume(job, drain=True)
        was_stopping = job["status"] == "stopping"
        if process.returncode == 0 and job["outcome"] == "completed":
            job["status"] = "completed"
        elif was_stopping or job["outcome"] == "interrupted":
            job["status"] = "interrupted"
            job["error"] = job["error"] or "任务已停止，可从最近成功写入的存档续跑"
        else:
            job["status"] = "failed"
            job["error"] = job["error"] or f"工作进程退出（退出码 {process.returncode}），请检查 stderr.log"
        job["ended_at"] = timestamp()
        set_phase(job, "idle", timestamp())
        process.stdin.close()
        self._processes.pop(job["id"], None)
        self._stopping_since.pop(job["id"], None)
        self._save(job)

    @staticmethod
    def _kill_tree(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    async def _run(self) -> None:
        try:
            while True:
                now = asyncio.get_running_loop().time()
                for job_id, process in list(self._processes.items()):
                    job = self.jobs[job_id]
                    self._consume(job)
                    if process.poll() is not None:
                        self._finish(job, process)
                    elif job["status"] == "stopping" and now - self._stopping_since[job_id] >= self.stop_timeout:
                        await asyncio.to_thread(self._kill_tree, process)
                        job["error"] = "已超过停止等待时间，强制结束进程。续跑将使用最近成功写入的检查点"
                    else:
                        self._save(job)
                endpoints = Counter(self._endpoint(self.jobs[job_id]) for job_id in self._processes)
                for job in list(self.jobs.values()):
                    if len(self._processes) >= self.settings.max_parallel:
                        break
                    endpoint = self._endpoint(job)
                    if job["status"] == "queued" and endpoints[endpoint] < self.settings.per_endpoint:
                        try:
                            self._launch(job)
                            endpoints[endpoint] += 1
                        except (OSError, ValueError) as exc:
                            job.update(status="failed", error=str(exc), ended_at=timestamp())
                            set_phase(job, "idle", timestamp())
                            self._save(job)
                await asyncio.sleep(.25)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failure = f"调度器已停止：{type(exc).__name__}"
            # 调度或日志存储失效时不能留下无人控制、继续计费的工作进程。
            for process in list(self._processes.values()):
                await asyncio.to_thread(self._kill_tree, process)
                await asyncio.to_thread(process.wait)
                process.stdin.close()
            self._processes.clear()
            self._payloads.clear()
            for job in self.jobs.values():
                if job["status"] in ACTIVE:
                    job.update(status="interrupted", ended_at=timestamp(), error=self.failure)
                    set_phase(job, "idle", timestamp())
            # 不再尝试写入已经失效的存储。API 公开故障，重启后按检查点恢复。
            raise

    @staticmethod
    def _endpoint(job: dict[str, Any]) -> str:
        return job["config"]["base_url"].rstrip("/").lower()
