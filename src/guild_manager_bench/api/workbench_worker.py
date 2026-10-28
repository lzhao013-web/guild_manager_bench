"""工作台专用的隔离执行入口。请通过调度器启动，不通过命令行传递凭证。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

from guild_manager_bench.api.workbench_events import recovery_history


class StopRequested(Exception):
    pass


def _scoring_pool():
    # 每个 run 已有独立进程。评分顺序执行，避免每局再派生整机规模的进程池。
    # 只替换工作进程中的执行器，不改评分函数、输入、冻结文件或协议指纹。
    return ThreadPoolExecutor(max_workers=1)


def main() -> int:
    from guild_manager_bench.api.routes_llm_debug import _debug_agent
    from guild_manager_bench.api.workbench import timestamp
    from guild_manager_bench.api.workbench_lock import archive_lease
    from guild_manager_bench.bench.llm import LlmRunConfig, run_llm_game
    from guild_manager_bench.bench.llm import runner
    from guild_manager_bench.bench import metrics
    from guild_manager_bench.bench.llm.recovery import inspect_recovery

    directory = Path(sys.argv[1])
    request = json.loads(sys.stdin.readline())
    payload = request["payload"]
    secret = payload.get("api_key")
    history = recovery_history([])
    stop = threading.Event()

    def control() -> None:
        line = sys.stdin.readline()
        stop.set()
        if not line:
            # 父进程异常退出。即使正阻塞于网络或评分，也不能留下孤儿任务。
            time.sleep(8)
            os._exit(130)

    threading.Thread(target=control, daemon=True).start()
    journal = (directory / "events.jsonl").open("a", encoding="utf-8", buffering=1)

    def record(event: dict[str, Any]) -> None:
        serialized = json.dumps({"at": timestamp(), "event": event}, ensure_ascii=False)
        if secret:
            serialized = serialized.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[REDACTED]")
        journal.write(serialized + "\n")

    lease = None

    def emit(event: dict[str, Any]) -> None:
        nonlocal lease
        if event["type"] == "run_archived" or event["type"] == "run_resumed" and payload["rerun_run_id"]:
            child_lease = archive_lease(Path(event["archive"]["directory"]))
            child_lease.__enter__()
            if lease is not None:
                lease.__exit__(None, None, None)
            lease = child_lease
        if event["type"] == "run_resumed":
            event = {**event, "workbench_history": history}
        record(event)
        if stop.is_set():
            raise StopRequested("用户请求停止运行")

    try:
        recovery = None
        if request["resume_dir"]:
            lease = archive_lease(Path(request["resume_dir"]))
            lease.__enter__()
            recovery = inspect_recovery(Path(request["resume_dir"]), request["data_dir"], payload["rerun_from_turn"])
            if recovery.token != payload["recovery_token"]:
                raise ValueError("排队期间存档或版本已变化，请重新提交并确认，尚未请求模型")
            recovery.authorize(payload["version_confirmation"])
            turns = [turn for turn in recovery.replay["turns"] if recovery.from_turn is None or turn["turn"] < recovery.from_turn]
            history = recovery_history(turns)
        runner.ProcessPoolExecutor = _scoring_pool
        metrics.ProcessPoolExecutor = _scoring_pool
        if payload["preset"] == "full":
            from guild_manager_bench.bench.protocols import full_runtime
            from guild_manager_bench._frozen.full_v1.bench import metrics as frozen_metrics
            full_runtime().ProcessPoolExecutor = _scoring_pool
            frozen_metrics.ProcessPoolExecutor = _scoring_pool
        agent = _debug_agent(payload)
        fields = ("objective", "max_tool_calls_per_turn", "max_empty_responses", "max_end_turn_attempts",
                  "max_model_steps_per_turn", "max_turn_retries", "game_seed", "scoring_seed")
        config = LlmRunConfig(**{key: payload[key] for key in fields if key in payload}, archive_dir=request["archive_root"])
        run = run_llm_game(agent, data_dir=request["data_dir"], config=config, event_sink=emit,
                           resume_archive_dir=request["resume_dir"], data_source=recovery.current if recovery is not None else request["data_source"], recovery=recovery)
        return 0 if run.status == "completed" else 1
    except StopRequested:
        record({"type": "worker_stopped"})
        return 130
    except Exception as exc:
        record({"type": "worker_failed", "error": str(exc)})
        return 1
    finally:
        journal.close()
        if lease is not None:
            lease.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
