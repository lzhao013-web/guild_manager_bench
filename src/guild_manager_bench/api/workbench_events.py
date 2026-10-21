"""从 runner 的真实事件投影运行摘要，不重算游戏或推断模型动机。"""
from __future__ import annotations

from typing import Any


def set_phase(job: dict[str, Any], phase: str, now: str) -> None:
    if job["phase"] != phase:
        job["phase"] = phase
        job["phase_started_at"] = now


def observe(job: dict[str, Any], observation: dict[str, Any]) -> None:
    job["turn"] = observation["max_turns"] if observation.get("finished") else observation["turn"]
    job["max_turns"] = observation["max_turns"]
    job["observation"] = {
        key: observation.get(key) for key in (
            "gold", "experience_pool", "materials", "adventurers", "equipment_inventory",
            "party_size_limit", "turn", "max_turns",
        )
    }


def restore_stats(job: dict[str, Any], stats: dict[str, Any]) -> None:
    job["calls"] = stats.get("model_interaction", {}).get("total_model_steps", job["calls"])
    job["tools"] = stats.get("tool_calls", {}).get("total", job["tools"])
    # 存档汇总可能把未报告用量写成 0。用量只从历史响应的实际字段恢复。


def apply_event(job: dict[str, Any], event: dict[str, Any], now: str) -> None:
    kind = event["type"]
    job["updated_at"] = now
    if kind == "run_started":
        job["data"] = event.get("data", {})
        if job["data"].get("protocol_status") == "mixed":
            job["notice"] = "跨版本执行：历史与当前规则不完全一致，不作为同版本标准评测比较"
        job["effective_config"] = event.get("config", {})
        observe(job, event["observation"])
    elif kind == "run_archived":
        job["archive_id"] = event["archive"]["directory"].replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    elif kind == "run_resumed":
        job["archive_id"] = event["archive"]["directory"].replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        restore_stats(job, event.get("stats", {}))
        observe(job, event["restored_observation"])
        job["last_score"] = event.get("last_rank_score")
        history = event["workbench_history"]
        job["usage"] = history["usage"]
        job["scores"] = history["scores"]
    elif kind == "turn_started":
        observe(job, event["observation"])
        job["budget"] = None
        set_phase(job, "preparing", now)
    elif kind == "model_request":
        job["calls"] += 1
        set_phase(job, "waiting_model", now)
    elif kind in {"model_delta", "model_reasoning_delta", "tool_call_delta"}:
        set_phase(job, "receiving", now)
    elif kind == "model_response":
        usage = event.get("usage") or {}
        job["usage"]["responses"] += 1
        has_usage = False
        for target, names in {"input_tokens": ("input_tokens", "prompt_tokens"),
                              "output_tokens": ("output_tokens", "completion_tokens")}.items():
            value = next((usage[name] for name in names if isinstance(usage.get(name), (int, float))), None)
            if value is not None:
                job["usage"][target] = (job["usage"][target] or 0) + value
                has_usage = True
        job["usage"]["reported"] += int(has_usage)
        job["last_response_ms"] = event.get("timing", {}).get("duration_ms")
    elif kind == "tool_call":
        set_phase(job, "executing_tool", now)
    elif kind == "tool_result":
        job["tools"] += 1
        result = event.get("result") or {}
        job["budget"] = result.get("tool_budget")
        after = result.get("_observation_after")
        if isinstance(after, dict):
            observe(job, after)
        action = {"name": event["name"], "turn": event.get("turn"), "ok": result.get("ok"),
                  "summary": str(result.get("error") or result.get("event", {}).get("summary") or event.get("content", ""))[:240]}
        job["recent_actions"] = [*job["recent_actions"][-4:], action]
        if result.get("ok") is False:
            job["tool_failures"] += 1
    elif kind in {"retry", "turn_retry"}:
        job["retries"] += 1
        job["notice"] = event.get("reason", "")
        set_phase(job, "retrying", now)
    elif kind == "scoring_started":
        set_phase(job, "scoring", now)
    elif kind == "turn_scored":
        job["last_score"] = event["rank_score"]
        job["scores"] = [p for p in job["scores"] if p["turn"] != event["turn"]]
        job["scores"].append({"turn": event["turn"], "value": event["rank_score"]})
    elif kind == "checkpoint_completed":
        job["checkpoint_at"] = now
        job["checkpoint_error"] = None
    elif kind == "checkpoint_failed":
        job["checkpoint_error"] = event["error"]
    elif kind in {"run_completed", "run_failed"}:
        run = event["run"]
        # 终态以进程退出确认，不能把完成事件误当作进程已经回收。
        job["outcome"] = "completed" if kind == "run_completed" else "failed"
        job["error"] = run.get("failure_reason")
        score = run.get("score") or {}
        if score.get("rank_score") is not None:
            job["last_score"] = score["rank_score"]
        set_phase(job, "finalizing", now)
    elif kind == "worker_failed":
        job["outcome"] = "failed"
        job["error"] = event["error"]
    elif kind == "worker_stopped":
        job["outcome"] = "interrupted"
    # turn_failed 可能随后被 runner 自动重试，不能提前将整局标为失败。
