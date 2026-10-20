from __future__ import annotations

import asyncio
from urllib.parse import urlsplit
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from guild_manager_bench.api.workbench import ACTIVE, Workbench
from guild_manager_bench.api.workbench_curves import CurveStore, empty_curves
from guild_manager_bench.api.workbench_trajectories import TrajectoryStore
from guild_manager_bench.api.routes_llm_archive import _run_directory
from guild_manager_bench.api.api_profiles import (
    CredentialError,
    ProfileConnection,
    ProfileWrite,
    resolve_connection,
)
from guild_manager_bench.api.api_history import import_local_history
from guild_manager_bench.api.model_catalog import (
    ModelCatalogError,
    fetch_models,
    fetch_temporary_models,
)
from guild_manager_bench.api.workbench_models import CreateBatch, SchedulerSettings


def workbench_router(manager: Workbench) -> APIRouter:
    router = APIRouter(prefix="/api/workbench", tags=["workbench"])
    curves = CurveStore()
    trajectories = TrajectoryStore()

    async def same_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin and (
            urlsplit(origin).netloc != request.headers.get("host")
            or urlsplit(origin).scheme != request.url.scheme
        ):
            raise HTTPException(403, "请从工作台所在站点提交操作")

    def profile_error(exc: Exception):
        if isinstance(exc, KeyError):
            return HTTPException(404, "API 配置不存在")
        if isinstance(exc, CredentialError):
            return HTTPException(503, str(exc))
        if isinstance(exc, OSError):
            return HTTPException(
                503, "API 配置暂不可写或不可读，请检查目录权限、磁盘空间，或等待正在进行的修改完成"
            )
        if isinstance(exc, ModelCatalogError):
            return HTTPException(502, str(exc))
        return HTTPException(422, str(exc))

    @router.get("/api-profiles")
    async def list_profiles():
        try:
            return {"profiles": manager.api_profiles.list()}
        except OSError as exc:
            raise profile_error(exc) from None

    @router.post("/api-profiles", dependencies=[Depends(same_origin)], status_code=201)
    async def create_profile(body: ProfileWrite):
        try:
            return manager.api_profiles.save(body)
        except (ValueError, RuntimeError, OSError) as exc:
            raise profile_error(exc) from None

    @router.post("/api-profiles/{profile_id}/key", dependencies=[Depends(same_origin)])
    async def read_profile_key(profile_id: str):
        try:
            connection = manager.api_profiles.resolve(profile_id)
            return JSONResponse(
                {"api_key": connection["api_key"]},
                headers={"Cache-Control": "no-store"},
            )
        except (KeyError, CredentialError, OSError) as exc:
            raise profile_error(exc) from None

    @router.put("/api-profiles/{profile_id}", dependencies=[Depends(same_origin)])
    async def update_profile(profile_id: str, body: ProfileWrite):
        try:
            return manager.api_profiles.save(body, profile_id)
        except (KeyError, ValueError, RuntimeError, OSError) as exc:
            raise profile_error(exc) from None

    @router.delete("/api-profiles/{profile_id}", dependencies=[Depends(same_origin)])
    async def delete_profile(profile_id: str):
        try:
            manager.api_profiles.delete(profile_id)
            return {"deleted": True}
        except (KeyError, CredentialError, OSError) as exc:
            raise profile_error(exc) from None

    @router.post("/api-profiles/import-history", dependencies=[Depends(same_origin)])
    async def import_profiles():
        try:
            return import_local_history(manager.api_profiles)
        except (ValueError, RuntimeError, OSError) as exc:
            raise profile_error(exc) from None

    @router.post("/api-profiles/models", dependencies=[Depends(same_origin)])
    async def temporary_models(body: ProfileConnection):
        try:
            return await asyncio.to_thread(fetch_temporary_models, body)
        except (CredentialError, ModelCatalogError, ValueError, RuntimeError) as exc:
            raise profile_error(exc) from None

    @router.post("/api-profiles/{profile_id}/models", dependencies=[Depends(same_origin)])
    async def profile_models(profile_id: str):
        try:
            connection = manager.api_profiles.resolve(profile_id)
            connection = resolve_connection(
                connection["provider"], connection["base_url"], connection["api_key"]
            )
            return await asyncio.to_thread(fetch_models, connection)
        except (KeyError, CredentialError, ModelCatalogError, ValueError, RuntimeError) as exc:
            raise profile_error(exc) from None

    @router.get("")
    async def snapshot():
        return manager.snapshot()

    @router.get("/curves")
    async def read_curves(job_id: str | None = None, run_id: str | None = None):
        if (job_id is None) == (run_id is None):
            raise HTTPException(422, "请选择一个工作台 run 或存档")
        job = None
        if job_id is not None:
            try:
                job = manager.get(job_id)
            except KeyError:
                raise HTTPException(404, "找不到运行任务") from None
            run_id = job.get("archive_id") or job["config"].get("resume_run_id")
            if not run_id:
                return empty_curves(job)
        path = _run_directory(manager.archive_root, run_id) / "replay.json"
        try:
            payload = await asyncio.to_thread(curves.read, path)
            if job:
                payload = {**payload, "status": job["status"]}
                if job.get("checkpoint_error"):
                    payload["warning"] = "最近存档写入失败，曲线可能落后于运行状态"
            return payload
        except FileNotFoundError:
            if job and job["status"] in ACTIVE:
                return empty_curves(job)
            raise HTTPException(404, "找不到曲线存档") from None
        except OSError:
            raise HTTPException(503, "回放暂不可读，请稍后重试") from None
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "回放格式不支持曲线提取，原存档保持不变") from None

    @router.get("/jobs/{job_id}/trajectories")
    async def read_trajectories(job_id: str, attempt: int | None = Query(None, ge=1), raw_after: int | None = Query(None, ge=0), origin: Literal["archive", "execution"] | None = None):
        if attempt is not None and raw_after is not None:
            raise HTTPException(422, "请选择回合或原始事件，不能同时读取")
        try:
            job = manager.get(job_id)
        except KeyError:
            raise HTTPException(404, "找不到运行任务") from None
        run_id = job.get("archive_id") or job["config"].get("resume_run_id")
        archive_path = _run_directory(manager.archive_root, run_id) / "trace.jsonl" if run_id else None
        has_archive = archive_path is not None and archive_path.is_file()
        if origin == "archive" and not has_archive:
            raise HTTPException(404, "整局轨迹已移动或不存在，不能改用其他日志定位")
        archived = origin == "archive" or origin is None and has_archive
        path = archive_path if archived else manager.root / job_id / "events.jsonl"
        try:
            if raw_after is not None:
                return await asyncio.to_thread(trajectories.raw, path, archived=archived, after=raw_after)
            payload = await asyncio.to_thread(trajectories.read, path, archived=archived, attempt=attempt)
            if attempt is not None:
                payload = {**payload, "monster_stages": manager.monster_stages(job_id)}
            if attempt is not None and job.get("status") in {"interrupted", "failed"}:
                # 受控进程停止事件只写入工作台日志，整局归档可能停在执行或评分中。
                tail = payload["attempts"][-1]
                if tail["status"] in {"running", "scoring"}:
                    tail["status"] = job["status"]
            warning = "" if archived else "整局归档轨迹尚不可用，目前只覆盖当前运行阶段，不能据此断言此前未购买或制作。"
            if job.get("checkpoint_error"):
                warning += "最近检查点保存失败，已记录操作可能未进入恢复点，续跑时以恢复位置为准。"
            return {**payload, "origin": "archive" if archived else "execution", "warning": warning or None}
        except OSError:
            raise HTTPException(503, "轨迹日志暂不可读，请稍后重试") from None
        except ValueError:
            raise HTTPException(422, "轨迹格式不支持或所选位置已不存在，原日志保持不变") from None

    @router.post("/batches", dependencies=[Depends(same_origin)], status_code=201)
    async def create_batch(body: CreateBatch):
        try:
            return manager.create_batch(body)
        except (ValueError, RuntimeError) as exc:
            # 配置错误在公开边界转换，不将凭证或内部堆栈发给浏览器。
            raise HTTPException(422, str(exc)) from exc

    @router.put("/settings", dependencies=[Depends(same_origin)])
    async def settings(body: SchedulerSettings):
        return manager.configure(body)

    @router.post("/jobs/{job_id}/stop", dependencies=[Depends(same_origin)])
    async def stop_job(job_id: str):
        try:
            return manager.stop(job_id)
        except KeyError as exc:
            raise HTTPException(404, "找不到运行任务") from exc

    @router.get("/jobs/{job_id}/watch")
    async def watch(job_id: str, attempt: int | None = Query(None, ge=1)):
        try:
            return await asyncio.to_thread(manager.watch, job_id, attempt)
        except KeyError as exc:
            raise HTTPException(404, "找不到运行任务") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/jobs/{job_id}/events")
    async def events(
        job_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)
    ):
        try:
            return manager.events(job_id, after, limit)
        except KeyError as exc:
            raise HTTPException(404, "找不到运行任务") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    return router
