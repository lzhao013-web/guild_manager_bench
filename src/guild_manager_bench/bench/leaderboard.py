"""Leaderboard data builder.

Aggregates LLM replay files by model and produces a leaderboard JSON
consumed by the static leaderboard frontend.

Supports incremental builds: processed run info is cached so unchanged
replay files are skipped on subsequent builds.

Usage via CLI::

    uv run guild-manager build-leaderboard
    uv run guild-manager build-leaderboard --data-dir path/to/replays --output path/to/out.json

Usage programmatically::

    from guild_manager_bench.bench.leaderboard import build_leaderboard
    from pathlib import Path
    build_leaderboard(Path("web/leaderboard/data"), Path("web/leaderboard/leaderboard_data.json"))
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from guild_manager_bench.bench.replay_scoring import (
    with_rank_score_curve,
    with_rank_score_from_final_observation,
)

# Incremental build cache version — bump when _extract_run_info schema changes.
_CACHE_VERSION = 3


# ── Helpers ───────────────────────────────────────────────────────────────────
def _extract_run_info(replay: dict, *, source_path: Path | None = None) -> dict | None:
    """Extract leaderboard-relevant fields from a replay dict."""
    if replay.get("kind") != "llm_replay":
        return None
    if replay.get("status") != "completed":
        return None

    score_data = replay.get("score")
    if not score_data:
        return None

    model = (replay.get("agent", {}).get("config", {}).get("model") or "").strip()
    if not model:
        return None

    turns = replay.get("turns")
    turns_count = len(turns) if isinstance(turns, list) else None
    data = replay.get("data")
    data = data if isinstance(data, dict) else {}
    final_observation = replay.get("final_observation")
    final_observation = final_observation if isinstance(final_observation, dict) else {}

    # Extract stats (prefer top-level stats, fall back to per-turn aggregation)
    stats = replay.get("stats")
    stats = stats if isinstance(stats, dict) else {}
    turns_list = turns if isinstance(turns, list) else []

    # Per-turn rank score curve (if available)
    # 1. Collect rank_score from completed turns only (skip retries & incomplete)
    # 2. Remove null entries
    # 3. Truncate to max_turns effective points
    max_turns = (
        (final_observation.get("max_turns") if isinstance(final_observation, dict) else None)
        or 0
    )

    completed_scores: list[float] = []
    for t in turns_list:
        if not isinstance(t, dict):
            continue
        if t.get("status") != "completed":
            continue
        v = t.get("rank_score")
        if v is not None:
            completed_scores.append(v)

    # Truncate to max_turns
    if max_turns > 0:
        completed_scores = completed_scores[:max_turns]

    rank_score_curve: list[dict[str, int | float]] = [
        {"turn": i + 1, "rank_score": v}
        for i, v in enumerate(completed_scores)
    ]

    # Aggregate timing/token from per-turn timing_usage if stats not available
    fallback_timing = _aggregate_turn_timing(turns_list) if not stats.get("timing") else None
    fallback_tokens = _aggregate_turn_tokens(turns_list) if not stats.get("token_usage") else None

    return {
        "run_id": source_path.stem if source_path is not None else replay.get("session_id", ""),
        "session_id": replay.get("session_id"),
        "model": model,
        "score": score_data.get("score"),
        "rank_score": score_data.get("rank_score"),
        "rank_score_source": score_data.get("rank_score_source"),
        "win_rate": score_data.get("chosen_win_rate"),
        "score_mode": score_data.get("mode"),
        "score_seed": score_data.get("seed"),
        "score_waves": score_data.get("waves"),
        "score_wave_size": score_data.get("wave_size"),
        "created_at": replay.get("created_at", ""),
        "updated_at": replay.get("updated_at", ""),
        "turns": turns_count,
        "preset": data.get("preset") or _preset_from_data_dir(data.get("data_dir")),
        "data_hash": data.get("data_hash"),
        "game_seed": data.get("game_seed"),
        "scoring_seed": data.get("scoring_seed"),
        "final_turn": final_observation.get("turn"),
        "max_turns": final_observation.get("max_turns"),
        "final_gold": final_observation.get("gold"),
        "final_experience_pool": final_observation.get("experience_pool"),
        "party_size": final_observation.get("party_size"),
        "party_size_limit": final_observation.get("party_size_limit"),
        "best_adventurer": _best_adventurer(score_data),
        # ── New fields ──
        "rank_score_curve": rank_score_curve or None,
        "token_usage": _extract_token_usage(stats) or fallback_tokens,
        "timing": _extract_timing(stats) or fallback_timing,
        "tool_calls": _extract_tool_calls(stats) or _count_turn_tool_calls(turns_list),
        "game_actions": _compute_game_actions(turns_list, final_observation, stats),
    }


def _extract_token_usage(stats: dict) -> dict | None:
    tu = stats.get("token_usage")
    if not isinstance(tu, dict):
        return None
    result: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens"):
        val = tu.get(key)
        if isinstance(val, int):
            result[key] = val
    if not result:
        return None
    # Optional cache fields
    for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        val = tu.get(key)
        if isinstance(val, int) and val > 0:
            result[key] = val
    return result


def _extract_timing(stats: dict) -> dict | None:
    timing = stats.get("timing")
    if not isinstance(timing, dict):
        return None
    duration_s = timing.get("total_duration_seconds")
    if isinstance(duration_s, (int, float)):
        return {"total_seconds": round(duration_s, 1)}
    return None


def _extract_tool_calls(stats: dict) -> dict | None:
    tc = stats.get("tool_calls")
    if not isinstance(tc, dict):
        return None
    total = tc.get("total")
    if isinstance(total, int):
        return {"total": total, "successful": tc.get("successful", 0), "failed": tc.get("failed", 0)}
    return None


def _extract_game_actions(stats: dict) -> dict | None:
    ga = stats.get("game_actions")
    if not isinstance(ga, dict):
        return None
    fields = {
        "battles_total": ga.get("battles_total"),
        "battles_won": ga.get("battles_won"),
        "battles_lost": ga.get("battles_lost"),
        "total_gold_earned": ga.get("total_gold_earned"),
        "total_experience_earned": ga.get("total_experience_earned"),
        "total_equipment_crafted": ga.get("total_equipment_crafted"),
        "total_upgrades_purchased": ga.get("total_upgrades_purchased"),
        "total_recruits": ga.get("total_recruits"),
    }
    if not any(isinstance(v, int) for v in fields.values()):
        return None
    return {k: v for k, v in fields.items() if isinstance(v, int)}


def _compute_game_actions(
    turns_list: list,
    final_observation: dict,
    stats: dict,
) -> dict | None:
    """Compute game actions using the most reliable data source.

    Stats may be incomplete after a resumed run (earlier turns lose their
    structured tool-call results).  We combine multiple sources:

    * **gold / exp earned**: prefer observation-based positive deltas (always
      accurate) over ``stats.game_actions`` (may be partial after resume).
    * **battle counts / win rate**: parse from the ``end_turn`` tool-result
      text content (always present) rather than relying on
      ``stats.game_actions.battles_total`` (may be partial).
    * **other counts** (crafted, upgrades, recruits, dismissals): from stats
      when available.
    """
    # ── Economy from observation deltas (most reliable) ──
    obs_result = _compute_game_actions_from_observations(turns_list, final_observation)

    # ── Battle stats from end_turn text content (reliable after resume) ──
    text_battles = _parse_battle_stats_from_text(turns_list)

    # ── Other counts from stats ──
    stats_result = _extract_game_actions(stats)

    # Merge: text-parsed data is the most reliable source for battles and
    # rewards (always present, even after resume).  Fall back to stats,
    # then observation deltas for gold/exp.
    result: dict[str, int] = {}

    # Battles from text parsing
    if text_battles:
        for key in ("battles_total", "battles_won", "battles_lost"):
            val = text_battles.get(key)
            if isinstance(val, int):
                result[key] = val
    elif stats_result:
        for key in ("battles_total", "battles_won", "battles_lost"):
            val = stats_result.get(key)
            if isinstance(val, int):
                result[key] = val

    # Gold / exp: prefer text-parsed battle rewards (actual earnings),
    # then stats, then observation deltas (which are reduced by mid-turn spending).
    if text_battles:
        for key in ("total_gold_earned", "total_experience_earned"):
            val = text_battles.get(key)
            if isinstance(val, int):
                result[key] = val
    if "total_gold_earned" not in result:
        for src in (stats_result, obs_result):
            if isinstance(src, dict):
                val = src.get("total_gold_earned")
                if isinstance(val, int):
                    result["total_gold_earned"] = val
                    break
    if "total_experience_earned" not in result:
        for src in (stats_result, obs_result):
            if isinstance(src, dict):
                val = src.get("total_experience_earned")
                if isinstance(val, int):
                    result["total_experience_earned"] = val
                    break

    # Other counts from stats only
    if stats_result:
        for key in (
            "total_equipment_crafted",
            "total_upgrades_purchased",
            "total_recruits",
            "total_dismissals",
        ):
            val = stats_result.get(key)
            if isinstance(val, int):
                result[key] = val

    return result if result else None


def _parse_battle_stats_from_text(turns_list: list) -> dict | None:
    """Parse battle counts and rewards from end_turn tool-result text.

    The text format is::

        OK end_turn: 结束第 N 回合：X 场战斗，Y 胜 Z 负
        ...
        - 冒险者 vs 怪物: 胜; 奖励 {金币:A, 经验:B, ...}

    This is always present in the replay (even after resume), making it a
    more reliable source than ``stats.game_actions`` which can be incomplete
    after a resumed run.  It also captures per-battle rewards so gold/exp
    totals reflect actual battle earnings, not observation deltas that are
    reduced by mid-turn spending.
    """
    import re

    battles_total = 0
    battles_won = 0
    battles_lost = 0
    total_gold = 0
    total_exp = 0

    for t in turns_list:
        if not isinstance(t, dict):
            continue
        for step in (t.get("steps") or []):
            if not isinstance(step, dict):
                continue
            if step.get("type") != "tool_result" or step.get("name") != "end_turn":
                continue
            content = step.get("content")
            if not isinstance(content, str):
                continue
            # Battle summary: X 场战斗，Y 胜 Z 负
            summary = re.search(
                r"(\d+)\s*场战斗[，,]\s*(\d+)\s*胜\s*(\d+)\s*负", content,
            )
            if not summary:
                continue
            battles_total += int(summary.group(1))
            battles_won += int(summary.group(2))
            battles_lost += int(summary.group(3))
            # Individual battle rewards: 奖励 {金币:X, 经验:Y, ...}
            for rm in re.finditer(r"奖励\s*\{([^}]+)\}", content):
                reward_text = rm.group(1)
                gm = re.search(r"金币[:\s]*(\d+)", reward_text)
                em = re.search(r"经验[:\s]*(\d+)", reward_text)
                if gm:
                    total_gold += int(gm.group(1))
                if em:
                    total_exp += int(em.group(1))

    if battles_total == 0:
        return None
    result: dict[str, int] = {
        "battles_total": battles_total,
        "battles_won": battles_won,
        "battles_lost": battles_lost,
    }
    if total_gold > 0:
        result["total_gold_earned"] = total_gold
    if total_exp > 0:
        result["total_experience_earned"] = total_exp
    return result


def _compute_game_actions_from_observations(
    turns_list: list,
    final_observation: dict,
) -> dict | None:
    """Compute gold/exp earned from per-turn observation_before snapshots.

    Sums only positive deltas between consecutive observations,
    so spending gold or allocating experience does not reduce the total.
    """
    # Collect gold & exp values in chronological order:
    # [obs_before_turn1, obs_before_turn2, ..., obs_before_turnN, final_obs]
    gold_series: list[int] = []
    exp_series: list[int] = []

    for t in turns_list:
        if not isinstance(t, dict):
            continue
        obs = t.get("observation_before")
        if not isinstance(obs, dict):
            continue
        g = obs.get("gold")
        e = obs.get("experience_pool")
        if isinstance(g, int):
            gold_series.append(g)
        if isinstance(e, int):
            exp_series.append(e)

    # Append final observation
    fg = final_observation.get("gold") if isinstance(final_observation, dict) else None
    fe = final_observation.get("experience_pool") if isinstance(final_observation, dict) else None
    if isinstance(fg, int):
        gold_series.append(fg)
    if isinstance(fe, int):
        exp_series.append(fe)

    # Sum positive deltas
    gold_earned = sum(
        max(0, gold_series[i + 1] - gold_series[i])
        for i in range(len(gold_series) - 1)
    )
    exp_earned = sum(
        max(0, exp_series[i + 1] - exp_series[i])
        for i in range(len(exp_series) - 1)
    )

    if gold_earned == 0 and exp_earned == 0:
        return None

    result: dict[str, int] = {}
    if gold_earned > 0:
        result["total_gold_earned"] = gold_earned
    if exp_earned > 0:
        result["total_experience_earned"] = exp_earned
    return result


def _aggregate_turn_timing(turns_list: list) -> dict | None:
    """Sum per-turn timing_usage.duration_ms into total_seconds."""
    total_ms = 0
    found = False
    for t in turns_list:
        if not isinstance(t, dict):
            continue
        tu = t.get("timing_usage")
        if isinstance(tu, dict):
            ms = tu.get("duration_ms")
            if isinstance(ms, (int, float)):
                total_ms += ms
                found = True
    if not found:
        return None
    return {"total_seconds": round(total_ms / 1000, 1)}


def _aggregate_turn_tokens(turns_list: list) -> dict | None:
    """Sum per-turn timing_usage tokens into totals."""
    input_total = 0
    output_total = 0
    found = False
    for t in turns_list:
        if not isinstance(t, dict):
            continue
        tu = t.get("timing_usage")
        if isinstance(tu, dict):
            inp = tu.get("input_tokens")
            out = tu.get("output_tokens")
            if isinstance(inp, int):
                input_total += inp
                found = True
            if isinstance(out, int):
                output_total += out
                found = True
    if not found:
        return None
    return {"input_tokens": input_total, "output_tokens": output_total}


def _count_turn_tool_calls(turns_list: list) -> dict | None:
    """Count tool_result entries in per-turn steps as a rough tool call proxy."""
    total = 0
    for t in turns_list:
        if not isinstance(t, dict):
            continue
        steps = t.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict) and step.get("type") == "tool_result":
                total += 1
    if total == 0:
        return None
    return {"total": total, "successful": total, "failed": 0}


# ── Aggregation ───────────────────────────────────────────────────────────────
def _aggregate_model(runs: list[dict]) -> dict:
    """Aggregate a list of per-run info dicts into a model summary."""
    scores = [r["score"] for r in runs if r["score"] is not None]
    rank_scores = [r["rank_score"] for r in runs if r["rank_score"] is not None]
    win_rates = [r["win_rate"] for r in runs if r["win_rate"] is not None]
    timestamps = [r["created_at"] for r in runs if r["created_at"]]

    result: dict = {"runs": len(runs)}

    # ── Core metrics ──
    if scores:
        result["score"] = {
            "best": max(scores),
            "mean": round(mean(scores), 2),
            "median": round(median(scores), 2),
        }
    else:
        result["score"] = None

    if rank_scores:
        result["rank_score"] = {
            "best": max(rank_scores),
            "mean": round(mean(rank_scores), 2),
            "median": round(median(rank_scores), 2),
        }
    else:
        result["rank_score"] = None

    if win_rates:
        result["win_rate"] = {
            "best": round(max(win_rates), 4),
            "mean": round(mean(win_rates), 4),
        }
    else:
        result["win_rate"] = None

    # ── Efficiency: aggregate token, timing, tool_calls ──
    result["efficiency"] = _aggregate_efficiency(runs)

    # ── Game quality: aggregate game_actions ──
    result["game_quality"] = _aggregate_game_quality(runs)

    result["last_run"] = max(timestamps) if timestamps else ""
    result["run_details"] = [
        _run_detail(run)
        for run in sorted(runs, key=lambda item: item.get("created_at") or "", reverse=True)
    ]

    return result


def _aggregate_efficiency(runs: list[dict]) -> dict | None:
    """Aggregate token usage, timing, and tool call stats across runs."""
    input_tokens = [r["token_usage"]["input_tokens"] for r in runs
                    if isinstance(r.get("token_usage"), dict) and "input_tokens" in r["token_usage"]]
    output_tokens = [r["token_usage"]["output_tokens"] for r in runs
                     if isinstance(r.get("token_usage"), dict) and "output_tokens" in r["token_usage"]]
    durations = [r["timing"]["total_seconds"] for r in runs
                 if isinstance(r.get("timing"), dict) and "total_seconds" in r["timing"]]
    tool_calls_total = [r["tool_calls"]["total"] for r in runs
                        if isinstance(r.get("tool_calls"), dict) and "total" in r["tool_calls"]]

    if not any([input_tokens, output_tokens, durations, tool_calls_total]):
        return None

    result: dict[str, Any] = {}
    if input_tokens:
        result["input_tokens"] = {"mean": round(mean(input_tokens)), "total": sum(input_tokens)}
    if output_tokens:
        result["output_tokens"] = {"mean": round(mean(output_tokens)), "total": sum(output_tokens)}
    if durations:
        result["duration_seconds"] = {"mean": round(mean(durations), 1), "total": round(sum(durations), 1)}
    if tool_calls_total:
        result["tool_calls"] = {"mean": round(mean(tool_calls_total)), "total": sum(tool_calls_total)}
    return result


def _aggregate_game_quality(runs: list[dict]) -> dict | None:
    """Aggregate game action stats across runs."""
    gold_earned = [r["game_actions"]["total_gold_earned"] for r in runs
                   if isinstance(r.get("game_actions"), dict)
                   and isinstance(r["game_actions"].get("total_gold_earned"), int)]
    exp_earned = [r["game_actions"]["total_experience_earned"] for r in runs
                  if isinstance(r.get("game_actions"), dict)
                  and isinstance(r["game_actions"].get("total_experience_earned"), int)]
    battles_won = [r["game_actions"]["battles_won"] for r in runs
                   if isinstance(r.get("game_actions"), dict)
                   and isinstance(r["game_actions"].get("battles_won"), int)]
    battles_total = [r["game_actions"]["battles_total"] for r in runs
                     if isinstance(r.get("game_actions"), dict)
                     and isinstance(r["game_actions"].get("battles_total"), int)]

    if not any([gold_earned, exp_earned, battles_won]):
        return None

    result: dict[str, Any] = {}
    if gold_earned:
        result["gold_earned"] = {"mean": round(mean(gold_earned)), "best": max(gold_earned)}
    if exp_earned:
        result["exp_earned"] = {"mean": round(mean(exp_earned)), "best": max(exp_earned)}
    if battles_won and battles_total:
        win_rates = [w / t if t > 0 else 0 for w, t in zip(battles_won, battles_total)]
        result["battle_win_rate"] = round(mean(win_rates), 4)
        result["battles_total"] = sum(battles_total)
        result["battles_won"] = sum(battles_won)
    return result


def _sort_key(entry: dict) -> float:
    """Sort key: rank_score.best descending, then score.best descending."""
    rank = entry.get("rank_score")
    if rank and rank.get("best") is not None:
        return rank["best"]
    score = entry.get("score")
    if score and score.get("best") is not None:
        return score["best"]
    return -1.0


# ── Incremental Build Cache ───────────────────────────────────────────────────

def _file_fingerprint(path: Path) -> str:
    """Fast fingerprint using mtime + size (avoids reading full content)."""
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def _load_cache(cache_path: Path) -> dict[str, dict]:
    """Load the incremental build cache. Returns {filename: {fingerprint, info}}."""
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("version") != _CACHE_VERSION:
            return {}  # schema changed — discard stale cache
        return data.get("entries", {})
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
        return {}


def _save_cache(cache_path: Path, entries: dict[str, dict]) -> None:
    """Persist the incremental build cache."""
    cache_path.write_text(
        json.dumps({"version": _CACHE_VERSION, "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Public API ────────────────────────────────────────────────────────────────

def _load_previous_output(output: Path) -> dict[str, dict] | None:
    """Load the previous leaderboard output. Returns {model_name: model_data} or None."""
    if not output.exists():
        return None
    try:
        data = json.loads(output.read_text(encoding="utf-8"))
        if data.get("schema_version") != 2:
            return None
        return {m["model"]: m for m in data.get("models", []) if isinstance(m, dict)}
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
        return None


def build_leaderboard(data_dir: Path, output: Path, *, incremental: bool = True) -> None:
    """Scan *data_dir* for replay JSON files and write aggregated leaderboard to *output*.

    When *incrementual* is True (default):

    * A ``.build_cache.json`` file is maintained next to *output* so that
      unchanged replay files are skipped on subsequent runs.
    * The previous ``leaderboard_data.json`` is read; only models whose replays
      have changed (added / modified / deleted) are re-aggregated.  Models with
      no changes are carried forward verbatim.
    """
    json_files = sorted(data_dir.glob("*.json"))
    if not json_files:
        print(f"No JSON files found in {data_dir}")
        sys.exit(1)

    cache_path = output.parent / ".build_cache.json"
    prev_models = _load_previous_output(output) if incremental else None
    cache: dict[str, dict] = _load_cache(cache_path) if incremental else {}
    new_cache: dict[str, dict] = {}

    # Determine which files changed vs. cache
    current_names = {p.name for p in json_files}
    cached_names = set(cache.keys())

    new_or_changed = set()
    deleted = cached_names - current_names

    for path in json_files:
        fp = _file_fingerprint(path)
        cached = cache.get(path.name)
        if not (cached and cached.get("fingerprint") == fp):
            new_or_changed.add(path.name)

    # Collect affected models from new/changed files
    affected_models: set[str] = set()
    for fname in new_or_changed:
        # Model name will be resolved during parsing below
        pass

    # Build per-model run lists:
    #   - For affected models: re-aggregate from scratch (all runs)
    #   - For unaffected models: carry from previous output
    model_runs: dict[str, list[dict]] = {}
    skipped = 0
    reused = 0
    parsed = 0

    for path in json_files:
        fp = _file_fingerprint(path)
        cached = cache.get(path.name)
        is_hit = incremental and cached and cached.get("fingerprint") == fp

        if is_hit:
            info = cached.get("info")
            if info is not None:
                model_runs.setdefault(info["model"], []).append(info)
                new_cache[path.name] = cached
                reused += 1
                # If this model is already affected, we'll re-aggregate anyway
                if info["model"] in affected_models:
                    continue
                # Otherwise this model is clean — no need to re-aggregate
                continue

        # Parse the file (cache miss or non-incremental)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  ⚠ Skipping {path.name}: {e}")
            skipped += 1
            continue

        data = with_rank_score_from_final_observation(data)
        data = with_rank_score_curve(data)
        info = _extract_run_info(data, source_path=path)
        if info is None:
            skipped += 1
            continue

        affected_models.add(info["model"])
        model_runs.setdefault(info["model"], []).append(info)
        new_cache[path.name] = {"fingerprint": fp, "info": info}
        parsed += 1

    if not model_runs and not prev_models:
        print("No valid completed replays found.")
        sys.exit(1)

    # Aggregate: re-aggregate affected models, carry unaffected from prev
    models: list[dict] = []
    unaffected_count = 0

    for model_name, runs in model_runs.items():
        if model_name in affected_models:
            agg = _aggregate_model(runs)
            agg["model"] = model_name
            models.append(agg)
        elif prev_models and model_name in prev_models:
            models.append(prev_models[model_name])
            unaffected_count += 1
        else:
            # New model — aggregate fresh
            agg = _aggregate_model(runs)
            agg["model"] = model_name
            models.append(agg)

    # Carry over models from prev that have no replays in data_dir.
    # These models keep their historical data (replays may have been moved
    # out of data_dir but their aggregated stats should persist).
    if prev_models:
        current_model_names = {m["model"] for m in models}
        for name, prev in prev_models.items():
            if name not in current_model_names:
                models.append(prev)
                unaffected_count += 1

    # Sort and assign ranks
    models.sort(key=_sort_key, reverse=True)
    for i, entry in enumerate(models):
        entry["rank"] = i + 1

    # Build output
    result = {
        "schema_version": 2,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_runs": sum(m["runs"] for m in models),
        "models": models,
    }

    # Write output and cache
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    if incremental:
        _save_cache(cache_path, new_cache)

    changed_count = len(affected_models)
    inc_info = ""
    if incremental:
        parts = []
        if parsed:
            parts.append(f"{parsed} parsed")
        if reused:
            parts.append(f"{reused} cached")
        if unaffected_count:
            parts.append(f"{unaffected_count} models unchanged")
        if deleted:
            parts.append(f"{len(deleted)} deleted")
        if parts:
            inc_info = f" ({', '.join(parts)})"
    print(f"✓ {len(models)} model(s), {result['total_runs']} run(s)"
          + (f", {skipped} skipped" if skipped else "")
          + inc_info)
    print(f"  → {output}")
    for m in models:
        rs = m.get("rank_score")
        rs_str = f"{rs['best']:,.1f}" if rs else "—"
        sc = m.get("score")
        sc_str = f"{sc['best']:.2f}" if sc else "—"
        eff = m.get("efficiency") or {}
        tok = eff.get("input_tokens", {})
        tok_str = f"{tok.get('mean', 0):,} tok" if tok else ""
        print(f"  #{m['rank']}  {m['model']:<40s}  rank={rs_str}  score={sc_str}  {tok_str}  ({m['runs']} runs)")


def _run_detail(run: dict) -> dict:
    return {
        "run_id": run.get("run_id"),
        "session_id": run.get("session_id"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "score": run.get("score"),
        "rank_score": run.get("rank_score"),
        "rank_score_source": run.get("rank_score_source"),
        "win_rate": run.get("win_rate"),
        "turns": run.get("turns"),
        "preset": run.get("preset"),
        "data_hash": run.get("data_hash"),
        "game_seed": run.get("game_seed"),
        "scoring_seed": run.get("scoring_seed"),
        "score_mode": run.get("score_mode"),
        "score_seed": run.get("score_seed"),
        "score_waves": run.get("score_waves"),
        "score_wave_size": run.get("score_wave_size"),
        "final_turn": run.get("final_turn"),
        "max_turns": run.get("max_turns"),
        "final_gold": run.get("final_gold"),
        "final_experience_pool": run.get("final_experience_pool"),
        "party_size": run.get("party_size"),
        "party_size_limit": run.get("party_size_limit"),
        "best_adventurer": run.get("best_adventurer"),
        # ── New fields ──
        "rank_score_curve": run.get("rank_score_curve"),
        "token_usage": run.get("token_usage"),
        "timing": run.get("timing"),
        "tool_calls": run.get("tool_calls"),
        "game_actions": run.get("game_actions"),
    }


def _best_adventurer(score_data: dict) -> dict | None:
    per_adventurer = score_data.get("per_adventurer")
    if not isinstance(per_adventurer, list):
        return None
    candidates = [item for item in per_adventurer if isinstance(item, dict)]
    if not candidates:
        return None
    best = max(candidates, key=lambda item: item.get("average_score") or -1)
    return {
        "name": best.get("name"),
        "average_score": best.get("average_score"),
        "win_rate": best.get("win_rate"),
        "assignments": best.get("assignments"),
    }


def _preset_from_data_dir(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.parent.name != "presets":
        return None
    return path.name or None
