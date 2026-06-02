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
_CACHE_VERSION = 5


# ── Helpers ───────────────────────────────────────────────────────────────────
def _extract_run_info(replay: dict, *, source_path: Path | None = None) -> dict | None:
    """Extract leaderboard-relevant fields from a replay dict."""
    kind = replay.get("kind")

    if kind == "manual_replay":
        return _extract_manual_run_info(replay, source_path=source_path)

    if kind != "llm_replay":
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
    turns_count = (
        sum(1 for t in turns if isinstance(t, dict) and t.get("status") == "completed")
        if isinstance(turns, list)
        else None
    )
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

    tool_calls = _merge_tool_calls(
        _extract_tool_calls(stats),
        _count_turn_tool_calls(turns_list),
    )

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
        "rank_score_per_adventurer": _rank_score_per_adventurer(score_data),
        # ── New fields ──
        "rank_score_curve": rank_score_curve or None,
        "token_usage": _extract_token_usage(stats) or fallback_tokens,
        "timing": _extract_timing(stats) or fallback_timing,
        "tool_calls": tool_calls,
        "game_actions": _compute_game_actions(turns_list, final_observation, stats),
    }


def _extract_manual_run_info(replay: dict, *, source_path: Path | None = None) -> dict | None:
    """Extract leaderboard fields from a manual_replay export."""
    if replay.get("status") not in ("finished", "completed"):
        return None

    score_data = replay.get("score")
    if not score_data or score_data.get("rank_score") is None:
        return None

    final_observation = replay.get("final_observation")
    final_observation = final_observation if isinstance(final_observation, dict) else {}
    manual_stats = replay.get("stats")
    manual_stats = manual_stats if isinstance(manual_stats, dict) else {}
    game_actions = manual_stats.get("game_actions")
    game_actions = game_actions if isinstance(game_actions, dict) else {}

    per_adv = _rank_score_per_adventurer(score_data)

    # Build economy curve from turns battles
    turns_list = replay.get("turns") or []
    economy_curve = _manual_economy_curve(turns_list)

    return {
        "run_id": source_path.stem if source_path is not None else replay.get("session_id", ""),
        "session_id": replay.get("session_id"),
        "model": "✋ 手动操作",
        "score": None,
        "rank_score": score_data.get("rank_score"),
        "rank_score_source": score_data.get("rank_score_source", "final_observation"),
        "win_rate": None,
        "score_mode": None,
        "score_seed": None,
        "score_waves": None,
        "score_wave_size": None,
        "created_at": replay.get("created_at", ""),
        "updated_at": replay.get("created_at", ""),
        "turns": final_observation.get("max_turns"),
        "preset": "manual",
        "data_hash": None,
        "game_seed": final_observation.get("seed"),
        "scoring_seed": None,
        "final_turn": final_observation.get("turn"),
        "max_turns": final_observation.get("max_turns"),
        "final_gold": final_observation.get("gold"),
        "final_experience_pool": final_observation.get("experience_pool"),
        "party_size": final_observation.get("party_size"),
        "party_size_limit": final_observation.get("party_size_limit"),
        "best_adventurer": _best_adventurer(score_data),
        "rank_score_per_adventurer": per_adv,
        "rank_score_curve": None,
        "token_usage": None,
        "timing": None,
        "tool_calls": None,
        "game_actions": {
            "battles_won": game_actions.get("battles_won", 0),
            "battles_total": game_actions.get("battles_total", 0),
            "total_gold_earned": game_actions.get("total_gold_earned", 0),
            "total_experience_earned": game_actions.get("total_experience_earned", 0),
            "economy_curve": economy_curve,
            "strongest_defeated_enemy": game_actions.get("strongest_defeated_enemy"),
        },
    }


def _manual_economy_curve(turns_list: list) -> list[dict] | None:
    """Build economy curve from manual replay turns (each has battles)."""
    curve: list[dict] = []
    cum_gold = 0
    cum_exp = 0
    for turn in turns_list:
        if not isinstance(turn, dict):
            continue
        turn_gold = 0
        turn_exp = 0
        for battle in turn.get("battles") or []:
            if not isinstance(battle, dict):
                continue
            reward = battle.get("reward")
            if isinstance(reward, dict):
                g = reward.get("gold")
                e = reward.get("experience")
                if isinstance(g, (int, float)):
                    turn_gold += int(g)
                if isinstance(e, (int, float)):
                    turn_exp += int(e)
        cum_gold += turn_gold
        cum_exp += turn_exp
        curve.append({
            "turn": turn.get("turn", len(curve) + 1),
            "gold_earned": turn_gold,
            "experience_earned": turn_exp,
            "cumulative_gold_earned": cum_gold,
            "cumulative_experience_earned": cum_exp,
        })
    return curve or None


def _extract_token_usage(stats: dict) -> dict | None:
    tu = stats.get("token_usage")
    if not isinstance(tu, dict):
        return None
    result: dict[str, Any] = {}
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
        result = {
            "total": total,
            "successful": tc.get("successful", 0),
            "failed": tc.get("failed", 0),
        }
        by_name = _int_dict(tc.get("by_name"))
        if by_name:
            result["by_name"] = by_name
        by_name_detail = _tool_call_detail_dict(tc.get("by_name_detail"))
        if by_name_detail:
            result["by_name_detail"] = by_name_detail
        return result
    return None


def _merge_tool_calls(primary: dict | None, fallback: dict | None) -> dict | None:
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    result = dict(primary)
    if result.get("total") == fallback.get("total"):
        for key in ("successful", "failed"):
            val = fallback.get(key)
            if isinstance(val, int):
                result[key] = val
    for key in ("by_name", "by_name_detail"):
        if key not in result and fallback.get(key):
            result[key] = fallback[key]
    return result


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
        "total_dismissals": ga.get("total_dismissals"),
        "total_experience_allocated": ga.get("total_experience_allocated"),
        "total_equips": ga.get("total_equips"),
        "total_unequips": ga.get("total_unequips"),
    }
    economy_curve = _sanitize_economy_curve(ga.get("economy_curve"))
    strongest = _sanitize_strongest_enemy(ga.get("strongest_defeated_enemy"))
    if (
        not any(isinstance(v, int) for v in fields.values())
        and not economy_curve
        and not strongest
    ):
        return None
    result: dict[str, Any] = {k: v for k, v in fields.items() if isinstance(v, int)}
    if economy_curve:
        result["economy_curve"] = economy_curve
    if strongest:
        result["strongest_defeated_enemy"] = strongest
    return result


def _int_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): val
        for key, val in value.items()
        if isinstance(val, int)
    }


def _tool_call_detail_dict(value: Any) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, int]] = {}
    for name, counts in value.items():
        if not isinstance(counts, dict):
            continue
        detail = {
            key: val
            for key, val in counts.items()
            if key in {"total", "successful", "failed"} and isinstance(val, int)
        }
        if detail:
            result[str(name)] = detail
    return result


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
            "total_experience_allocated",
            "total_equips",
            "total_unequips",
        ):
            val = stats_result.get(key)
            if isinstance(val, int):
                result[key] = val

    economy_curve = (
        stats_result.get("economy_curve")
        if isinstance(stats_result, dict)
        else None
    )
    if not economy_curve:
        economy_curve = _compute_economy_curve_from_turns(turns_list)
    if economy_curve:
        result["economy_curve"] = economy_curve

    strongest = (
        stats_result.get("strongest_defeated_enemy")
        if isinstance(stats_result, dict)
        else None
    )
    if not strongest:
        strongest = _compute_strongest_defeated_enemy(turns_list)
    if strongest:
        result["strongest_defeated_enemy"] = strongest

    return result if result else None


def _sanitize_economy_curve(value: Any) -> list[dict[str, int]] | None:
    if not isinstance(value, list):
        return None
    result: list[dict[str, int]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        turn = item.get("turn")
        if not isinstance(turn, int):
            continue
        point: dict[str, int] = {"turn": turn}
        for key in (
            "gold_earned",
            "experience_earned",
            "cumulative_gold_earned",
            "cumulative_experience_earned",
        ):
            val = item.get(key)
            if isinstance(val, int):
                point[key] = val
        if len(point) > 1:
            result.append(point)
    return result or None


def _sanitize_strongest_enemy(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    power = value.get("power")
    if not isinstance(power, (int, float)):
        return None
    result = {
        "turn": value.get("turn"),
        "monster_id": value.get("monster_id"),
        "name": value.get("name"),
        "power": power,
    }
    for key in ("tier", "archetype_id", "stats", "reward"):
        val = value.get(key)
        if val is not None:
            result[key] = val
    return result


def _compute_economy_curve_from_turns(turns_list: list) -> list[dict[str, int]] | None:
    cumulative_gold = 0
    cumulative_exp = 0
    curve: list[dict[str, int]] = []

    for index, turn in enumerate(turns_list):
        if not isinstance(turn, dict):
            continue
        if turn.get("status") != "completed":
            continue
        turn_gold = 0
        turn_exp = 0
        for step in turn.get("steps") or []:
            if not (
                isinstance(step, dict)
                and step.get("type") == "tool_result"
                and step.get("name") == "end_turn"
            ):
                continue
            structured = _battle_reward_stats_from_step(step)
            if structured is not None:
                turn_gold += structured.get("gold_earned", 0)
                turn_exp += structured.get("experience_earned", 0)
            else:
                rewards = _reward_stats_from_text(step.get("content"))
                turn_gold += rewards.get("gold_earned", 0)
                turn_exp += rewards.get("experience_earned", 0)
        cumulative_gold += turn_gold
        cumulative_exp += turn_exp
        turn_number = turn.get("turn")
        curve.append(
            {
                "turn": turn_number if isinstance(turn_number, int) else index + 1,
                "gold_earned": turn_gold,
                "experience_earned": turn_exp,
                "cumulative_gold_earned": cumulative_gold,
                "cumulative_experience_earned": cumulative_exp,
            }
        )

    return curve or None


def _battle_reward_stats_from_step(step: dict) -> dict[str, int] | None:
    result = step.get("result")
    if not isinstance(result, dict):
        return None
    turn_result = result.get("turn_result")
    if not isinstance(turn_result, dict):
        return None
    battles = turn_result.get("battles")
    if not isinstance(battles, list):
        return None
    gold = 0
    exp = 0
    for battle in battles:
        if not isinstance(battle, dict):
            continue
        reward = battle.get("reward")
        if not isinstance(reward, dict):
            continue
        g = reward.get("gold")
        e = reward.get("experience")
        if isinstance(g, (int, float)):
            gold += int(g)
        if isinstance(e, (int, float)):
            exp += int(e)
    return {"gold_earned": gold, "experience_earned": exp}


def _reward_stats_from_text(content: Any) -> dict[str, int]:
    import re

    text = content if isinstance(content, str) else ""
    reward_lines = [
        line
        for line in text.splitlines()
        if "奖励" in line or "reward" in line.lower()
    ]
    battle_reward_lines = [
        line
        for line in reward_lines
        if line.lstrip().startswith("-")
    ]
    gold = 0
    exp = 0
    for line in battle_reward_lines or reward_lines:
        gm = re.search(r"(?:金币|gold)\s*[:=＝]\s*(\d+)", line, re.IGNORECASE)
        em = re.search(
            r"(?:经验|experience|exp)\s*[:=＝]\s*(\d+)",
            line,
            re.IGNORECASE,
        )
        if gm:
            gold += int(gm.group(1))
        if em:
            exp += int(em.group(1))
    return {"gold_earned": gold, "experience_earned": exp}


def _compute_strongest_defeated_enemy(turns_list: list) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for turn in turns_list:
        if not isinstance(turn, dict):
            continue
        observation = turn.get("observation_before")
        observation = observation if isinstance(observation, dict) else None
        turn_number = turn.get("turn")
        if not isinstance(turn_number, int):
            turn_number = 0
        for step in turn.get("steps") or []:
            if not (
                isinstance(step, dict)
                and step.get("type") == "tool_result"
                and step.get("name") == "end_turn"
            ):
                continue
            candidate = _strongest_enemy_from_step_result(step, observation, turn_number)
            if candidate is None:
                candidate = _strongest_enemy_from_step_text(step, observation, turn_number)
            best = _stronger_enemy(best, candidate)
    return best


def _strongest_enemy_from_step_result(
    step: dict,
    observation: dict | None,
    turn_number: int,
) -> dict[str, Any] | None:
    result = step.get("result")
    if not isinstance(result, dict):
        return None
    turn_result = result.get("turn_result")
    if not isinstance(turn_result, dict):
        return None
    battles = turn_result.get("battles")
    if not isinstance(battles, list):
        return None
    best: dict[str, Any] | None = None
    for battle in battles:
        if not isinstance(battle, dict) or _battle_won_dict(battle) is not True:
            continue
        best = _stronger_enemy(
            best,
            _defeated_enemy_from_battle_dict(battle, observation, turn_number),
        )
    return best


def _strongest_enemy_from_step_text(
    step: dict,
    observation: dict | None,
    turn_number: int,
) -> dict[str, Any] | None:
    import re

    content = step.get("content")
    if not isinstance(content, str):
        return None
    best: dict[str, Any] | None = None
    for line in content.splitlines():
        if not line.lstrip().startswith("-") or " vs " not in line:
            continue
        match = re.match(
            r"^\s*-\s+(?:(\d+)\s+)?(.+?)\s+vs\s+(?:(\d+)\s+)?(.+?)[:：]\s*([^;；]+)",
            line,
            re.IGNORECASE,
        )
        if not match:
            continue
        outcome = match.group(5).strip().lower()
        if "负" in outcome or outcome in {
            "right_win",
            "monster_win",
            "enemy_win",
            "loss",
            "lost",
            "defeat",
        }:
            continue
        if "胜" not in outcome and outcome not in {
            "left_win",
            "adventurer_win",
            "player_win",
            "win",
            "won",
            "victory",
        }:
            continue
        battle: dict[str, Any] = {"monster_name": match.group(4).strip()}
        monster_id = _monster_id_from_ref(observation, match.group(3))
        if monster_id is not None:
            battle["monster_id"] = monster_id
        best = _stronger_enemy(
            best,
            _defeated_enemy_from_battle_dict(battle, observation, turn_number),
        )
    return best


def _battle_won_dict(battle: dict) -> bool | None:
    won = battle.get("won")
    if isinstance(won, bool):
        return won
    outcome = battle.get("outcome") or battle.get("result") or battle.get("winner") or battle.get("status")
    if not isinstance(outcome, str):
        return None
    value = outcome.lower()
    if value in {"left_win", "adventurer_win", "player_win", "win", "won", "victory"}:
        return True
    if value in {"right_win", "monster_win", "enemy_win", "loss", "lost", "defeat"}:
        return False
    return None


def _monster_id_from_ref(observation: dict | None, ref: str | None) -> str | None:
    if ref is None or not isinstance(observation, dict):
        return None
    try:
        index = int(ref) - 1
    except ValueError:
        return None
    monsters = observation.get("monsters")
    if not isinstance(monsters, list) or index < 0 or index >= len(monsters):
        return None
    monster = monsters[index]
    if not isinstance(monster, dict):
        return None
    monster_id = monster.get("monster_id")
    return str(monster_id) if monster_id is not None else None


def _defeated_enemy_from_battle_dict(
    battle: dict,
    observation: dict | None,
    turn_number: int,
) -> dict[str, Any] | None:
    monster = _monster_from_observation_dict(battle, observation)
    stats_source = monster.get("stats") if isinstance(monster, dict) else None
    if not isinstance(stats_source, dict):
        stats_source = battle.get("monster_stats")
    if not isinstance(stats_source, dict):
        stats_source = battle.get("stats")
    if not isinstance(stats_source, dict):
        return None
    stats = _numeric_map(stats_source)
    if not stats:
        return None

    reward_source = (
        monster.get("reward")
        if isinstance(monster, dict) and isinstance(monster.get("reward"), dict)
        else battle.get("reward")
    )
    reward = _numeric_map(reward_source) if isinstance(reward_source, dict) else {}
    monster_id = monster.get("monster_id") if isinstance(monster, dict) else battle.get("monster_id")
    name = (
        battle.get("monster_name")
        or (monster.get("name") if isinstance(monster, dict) else None)
        or battle.get("monster")
        or monster_id
    )
    result: dict[str, Any] = {
        "turn": turn_number,
        "monster_id": None if monster_id is None else str(monster_id),
        "name": None if name is None else str(name),
        "power": _monster_power(stats),
        "stats": stats,
    }
    if reward:
        result["reward"] = reward
    if isinstance(monster, dict):
        for key in ("tier", "archetype_id"):
            value = monster.get(key)
            if value is not None:
                result[key] = value
    return result


def _monster_from_observation_dict(
    battle: dict,
    observation: dict | None,
) -> dict | None:
    if not isinstance(observation, dict):
        return None
    monsters = observation.get("monsters")
    if not isinstance(monsters, list):
        return None
    monster_id = battle.get("monster_id")
    if monster_id is not None:
        monster_id_text = str(monster_id)
        for monster in monsters:
            if isinstance(monster, dict) and str(monster.get("monster_id")) == monster_id_text:
                return monster
    monster_name = battle.get("monster_name") or battle.get("monster")
    if monster_name is not None:
        for monster in monsters:
            if isinstance(monster, dict) and monster.get("name") == monster_name:
                return monster
    return None


def _numeric_map(values: dict) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in values.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _monster_power(stats: dict[str, int]) -> int:
    return (
        int(stats.get("hp", 0))
        + int(stats.get("mp", 0))
        + int(stats.get("attack", 0)) * 8
        + int(stats.get("defense", 0)) * 8
        + int(stats.get("speed", 0)) * 5
        + int(stats.get("recovery", 0)) * 5
        + int(stats.get("mp_recovery", 0)) * 5
    )


def _stronger_enemy(
    current: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    current_power = current.get("power")
    candidate_power = candidate.get("power")
    if not isinstance(current_power, (int, float)):
        return candidate
    if not isinstance(candidate_power, (int, float)):
        return current
    return candidate if candidate_power > current_power else current


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
    successful = 0
    failed = 0
    by_name: dict[str, int] = {}
    by_name_detail: dict[str, dict[str, int]] = {}
    for t in turns_list:
        if not isinstance(t, dict):
            continue
        steps = t.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict) and step.get("type") == "tool_result":
                total += 1
                name = str(step.get("name") or "?")
                by_name[name] = by_name.get(name, 0) + 1
                detail = by_name_detail.setdefault(
                    name,
                    {"total": 0, "successful": 0, "failed": 0},
                )
                detail["total"] += 1
                ok = _tool_step_succeeded(step)
                if ok:
                    successful += 1
                    detail["successful"] += 1
                else:
                    failed += 1
                    detail["failed"] += 1
    if total == 0:
        return None
    return {
        "total": total,
        "successful": successful,
        "failed": failed,
        "by_name": by_name,
        "by_name_detail": by_name_detail,
    }


def _tool_step_succeeded(step: dict) -> bool:
    if step.get("ok") is True:
        return True
    if step.get("ok") is False or step.get("error"):
        return False
    result = step.get("result")
    if isinstance(result, dict) and isinstance(result.get("ok"), bool):
        return bool(result["ok"])
    content = step.get("content")
    if isinstance(content, str):
        stripped = content.lstrip()
        if stripped.startswith("OK ") or stripped.startswith("成功 "):
            return True
        if (
            stripped.startswith("FAIL ")
            or stripped.startswith("失败 ")
            or stripped.startswith("ERROR ")
            or stripped.startswith("错误 ")
        ):
            return False
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("ok"), bool):
            return bool(parsed["ok"])
    return True


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
            print(f"  ! Skipping {path.name}: {e}")
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
    print(f"OK {len(models)} model(s), {result['total_runs']} run(s)"
          + (f", {skipped} skipped" if skipped else "")
          + inc_info)
    print(f"  -> {output}")
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
        "rank_score_per_adventurer": run.get("rank_score_per_adventurer"),
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


def _rank_score_per_adventurer(score_data: dict) -> list[dict] | None:
    values = score_data.get("rank_score_per_adventurer")
    if not isinstance(values, list):
        values = score_data.get("per_adventurer")
    if not isinstance(values, list):
        return None

    result: list[dict] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        contribution = item.get("rank_score_contribution")
        if contribution is None:
            contribution = item.get("rank_score")
        if contribution is None:
            continue
        result.append(
            {
                "adventurer_id": item.get("adventurer_id"),
                "name": item.get("name"),
                "rank_score": contribution,
                "rank_score_contribution": contribution,
                "rank_score_share": item.get("rank_score_share"),
                "assignments": item.get("assignments"),
            }
        )
    return result or None


def _preset_from_data_dir(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.parent.name != "presets":
        return None
    return path.name or None
