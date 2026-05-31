"""Estimate rank scores for LLM replays and baseline using difficulty-sweep scoring.

For each team, sweep through linearly-increasing difficulty tiers and compute
a weighted-integral rank score:
    rank = Σ performance(d) × d  for d in difficulty_tiers

where performance(d) = total_wave_score / (waves × wave_size × 100).
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from guild_manager_bench.bench.metrics import (
    _arena_monster,
    _best_assignment,
    _evaluate_battle,
    _round_score,
    _sample_arena_monster,
    score_final_state,
)
from guild_manager_bench.bench.operators.greedy_operator import GreedyOperator
from guild_manager_bench.bench.runner import run_operator
from guild_manager_bench.game.combat import Combatant, run_auto_battle
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect, StatusDefinition
from guild_manager_bench.game.state import GameDefinition, GameState, ScoringRules

# ── Configuration ──────────────────────────────────────────────────────────

RUNS_DIR = _PROJECT_ROOT / "runs" / "llm"

LLM_RUN_IDS = [
    "20260531-020253-906247_19dd7550b60e4cb39975de99b1f85b2a",
    "20260531-000041-144737_15012021b8204ef79005979f744ff893",
]

# Linear difficulty sweep parameters
MIN_DIFF = 10
MAX_DIFF = 300
DIFF_STEP = 10
WAVES_PER_TIER = 200
SCORING_SEED = 20260529


# ── Skill Deserialization ──────────────────────────────────────────────────

def _parse_status(data: dict[str, Any]) -> StatusDefinition:
    return StatusDefinition(
        status_id=data["status_id"],
        name=data["name"],
        duration=data["duration"],
        effects=tuple(_parse_effect(e) for e in data["effects"]),
        polarity=data.get("polarity", "neutral"),
        stack_mode=data.get("stack_mode", "refresh"),
    )


def _parse_effect(data: dict[str, Any]) -> SkillEffect:
    status = _parse_status(data["status"]) if data.get("status") else None
    return SkillEffect(
        effect_type=data["type"],
        value=data.get("value", 0),
        stat=data.get("stat"),
        target=data.get("target", "target"),
        status=status,
    )


def _parse_condition(data: dict[str, Any]) -> SkillCondition:
    children = data.get("conditions", [])
    return SkillCondition(
        condition_type=data["type"],
        value=data.get("value"),
        conditions=tuple(_parse_condition(c) for c in children) if children else (),
    )


def _parse_skill(data: dict[str, Any]) -> Skill:
    return Skill(
        skill_id=data["skill_id"],
        name=data["name"],
        kind=data["kind"],
        condition=_parse_condition(data["condition"]),
        effects=tuple(_parse_effect(e) for e in data["effects"]),
        mp_cost=data.get("mp_cost", 0),
        priority=data.get("priority", 0),
        once_per_battle=data.get("once_per_battle", False),
        free=data.get("free", False),
    )


def _parse_combat_stats(data: dict[str, int]) -> CombatStats:
    return CombatStats(
        hp=data["hp"],
        mp=data["mp"],
        attack=data["attack"],
        defense=data["defense"],
        speed=data["speed"],
        recovery=data["recovery"],
        mp_recovery=data["mp_recovery"],
    )


# ── Scoring from Observation ───────────────────────────────────────────────

def _evaluate_battle_direct(
    adv_stats: CombatStats,
    adv_skills: tuple[Skill, ...],
    adv_id: str,
    monster_stats: CombatStats,
    monster_skills: tuple[Skill, ...],
    monster_id: str,
) -> tuple[float, bool]:
    """Evaluate a single arena battle using pre-computed effective stats."""
    result = run_auto_battle(
        Combatant(
            combatant_id=adv_id,
            stats=adv_stats,
            resources=CombatResources.full(adv_stats),
            skills=adv_skills,
        ),
        Combatant(
            combatant_id=monster_id,
            stats=monster_stats,
            resources=CombatResources.full(monster_stats),
            skills=monster_skills,
        ),
    )
    enemy_progress = 1 - result.right_resources.current_hp / monster_stats.hp
    survival_margin = result.left_resources.current_hp / adv_stats.hp
    outcome_score = {
        "left_win": 1.0,
        "draw": 0.4,
        "right_win": 0.0,
    }[result.outcome]
    score = _round_score(max(0.0, min(100.0, 70 * outcome_score + 20 * enemy_progress + 10 * survival_margin)))
    return score, result.outcome == "left_win"


def score_from_observation(
    definition: GameDefinition,
    obs_adventurers: list[dict[str, Any]],
    *,
    waves: int = WAVES_PER_TIER,
    wave_size: int = 6,
    difficulty: int = 50,
    scoring_seed: int = SCORING_SEED,
) -> tuple[float, float, int, int]:
    """Score a team from observation data at a single difficulty tier.

    Returns (score_0_100, win_rate, simulated_battles, chosen_battles).
    """
    rng = random.Random(scoring_seed)
    rules = definition.scoring

    # Parse adventurers once
    adventurers = []
    for adv in obs_adventurers:
        stats = _parse_combat_stats(adv["effective_stats"])
        skills = tuple(_parse_skill(s) for s in adv["skills"])
        adventurers.append((adv["adventurer_id"], stats, skills))

    total_score = 0.0
    chosen_wins = 0
    chosen_battles = 0
    simulated_battles = 0

    for wave_index in range(waves):
        monsters = tuple(
            _sample_arena_monster(
                definition, rng,
                wave_index=wave_index + 1,
                index=index + 1,
                difficulty=difficulty,
            )
            for index in range(wave_size)
        )

        # Build score matrix
        matrix = []
        for adv_id, adv_stats, adv_skills in adventurers:
            row = []
            for monster in monsters:
                score, won = _evaluate_battle_direct(
                    adv_stats, adv_skills, adv_id,
                    monster.stats, monster.skills, monster.monster_id,
                )
                row.append((score, won))
            matrix.append(row)

        simulated_battles += len(adventurers) * len(monsters)

        # Best assignment using the matrix
        assignments = _best_assignment_simple(matrix)
        for ai, mi in assignments:
            score, won = matrix[ai][mi]
            total_score += score
            chosen_wins += 1 if won else 0
            chosen_battles += 1

    denominator = waves * wave_size * 100
    score = _round_score(100 * total_score / denominator) if denominator else 0.0
    win_rate = chosen_wins / chosen_battles if chosen_battles else 0.0
    return score, win_rate, simulated_battles, chosen_battles


def _best_assignment_simple(matrix: list[list[tuple[float, bool]]]) -> list[tuple[int, int]]:
    """Simplified best assignment that works with (score, won) tuples."""
    if not matrix or not matrix[0]:
        return []

    n_adv = len(matrix)
    n_mon = len(matrix[0])
    target = min(n_adv, n_mon)

    @lru_cache(maxsize=None)
    def search(ai: int, used: int, assigned: int) -> tuple[float, tuple]:
        if assigned == target:
            return 0.0, ()
        if ai >= n_adv:
            return float("-inf"), ()
        remaining = n_adv - ai
        if assigned + remaining < target:
            return float("-inf"), ()

        best_score = float("-inf")
        best_pairs = ()
        if assigned + remaining > target:
            best_score, best_pairs = search(ai + 1, used, assigned)

        for mi in range(n_mon):
            bit = 1 << mi
            if used & bit:
                continue
            rest_score, rest_pairs = search(ai + 1, used | bit, assigned + 1)
            candidate = matrix[ai][mi][0] + rest_score
            if candidate > best_score:
                best_score = candidate
                best_pairs = ((ai, mi),) + rest_pairs
        return best_score, best_pairs

    return list(search(0, 0, 0)[1])


# ── Rank Score Computation ─────────────────────────────────────────────────

def compute_rank_score(
    definition: GameDefinition,
    obs_adventurers: list[dict[str, Any]],
    *,
    min_diff: int = MIN_DIFF,
    max_diff: int = MAX_DIFF,
    diff_step: int = DIFF_STEP,
    waves_per_tier: int = WAVES_PER_TIER,
    scoring_seed: int = SCORING_SEED,
) -> tuple[float, list[tuple[int, float, float]]]:
    """Compute rank score via linear difficulty-sweep weighted integral."""

    difficulties = list(range(min_diff, max_diff + 1, diff_step))
    details: list[tuple[int, float, float]] = []
    rank_score = 0.0

    wave_size = definition.scoring.wave_size

    for d in difficulties:
        score, win_rate, _, _ = score_from_observation(
            definition, obs_adventurers,
            waves=waves_per_tier,
            wave_size=wave_size,
            difficulty=d,
            scoring_seed=scoring_seed,
        )
        performance = score / 100.0
        contribution = performance * d
        rank_score += contribution
        details.append((d, performance, contribution))

        if performance < 0.001 and d > min_diff + diff_step * 5:
            break

    return rank_score, details


# ── Main ────────────────────────────────────────────────────────────────────

def extract_final_observation(run_id: str) -> dict[str, Any]:
    """Read the final_observation from a replay JSON."""
    replay_path = RUNS_DIR / run_id / "replay.json"
    # Read just enough to get final_observation
    with open(replay_path, "r", encoding="utf-8") as f:
        # The file is large, but we just need top-level keys
        data = json.load(f)
    obs = data.get("final_observation")
    if obs is None:
        raise ValueError(f"no final_observation in {run_id}")
    score_data = data.get("score")
    config_data = data.get("config", {})
    data_info = data.get("data", {})
    return {
        "observation": obs,
        "score": score_data,
        "game_seed": data_info.get("game_seed"),
        "data_dir": data_info.get("data_dir"),
        "status": data.get("status"),
    }


def main():
    results: dict[str, Any] = {}

    # Load game definition for monster archetypes, skills, etc.
    data_dir = str(_PROJECT_ROOT / "data" / "presets" / "full")
    definition = load_game_definition(data_dir)

    # ── LLM Replays ────────────────────────────────────────────────────────
    for run_id in LLM_RUN_IDS:
        label = run_id.split("_")[0]
        print(f"\n{'='*60}")
        print(f"LLM Replay: {label}")
        print(f"{'='*60}")

        info = extract_final_observation(run_id)
        obs = info["observation"]
        adventurers = obs["adventurers"]

        print(f"  game_seed = {info['game_seed']}")
        print(f"  status = {info['status']}")
        print(f"  turn = {obs['turn']}/{obs['max_turns']}")
        print(f"  party_size = {obs['party_size']}")
        for adv in adventurers:
            es = adv["effective_stats"]
            print(
                f"    Lv{adv['level']:2d} {adv['name']:12s}  "
                f"ATK={es['attack']:3d} DEF={es['defense']:3d} "
                f"SPD={es['speed']:3d} HP={es['hp']:4d}"
            )

        # Old score for reference
        old_score = info["score"]
        if old_score:
            print(f"\n  Old score (0-100) = {old_score.get('score', '?')}")
            print(f"  Old win_rate = {old_score.get('chosen_win_rate', '?')}")

        # Also compute old score with current definition for apples-to-apples
        # (can't easily do this without GameState, so skip)

        # New rank score
        print(f"\n  Sweeping difficulty [{MIN_DIFF}..{MAX_DIFF}] step={DIFF_STEP} ...")
        rank, details = compute_rank_score(definition, adventurers)

        results[f"LLM:{label}"] = {
            "rank": rank,
            "old_score": old_score.get("score") if old_score else None,
            "party_size": obs["party_size"],
        }

        _print_sweep(details)
        print(f"\n  >>> RANK SCORE = {rank:.2f}")

    # ── Baseline ────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Baseline: GreedyOperator")
    print(f"{'='*60}")

    # Use game seed from first replay
    first_info = extract_final_observation(LLM_RUN_IDS[0])
    game_seed = first_info["game_seed"]
    print(f"  game_seed = {game_seed}")

    seeded_def = replace(definition, rules=replace(definition.rules, seed=game_seed))
    session = run_operator(seeded_def, GreedyOperator(seed=0), max_steps=1_000)
    state = session.state

    obs = session.observation()
    print(f"  turn = {obs['turn']}/{obs['max_turns']}")
    print(f"  party_size = {obs['party_size']}")
    for adv in obs["adventurers"]:
        es = adv["effective_stats"]
        print(
            f"    Lv{adv['level']:2d} {adv['name']:12s}  "
            f"ATK={es['attack']:3d} DEF={es['defense']:3d} "
            f"SPD={es['speed']:3d} HP={es['hp']:4d}"
        )

    # Old score for baseline
    old_report = score_final_state(seeded_def, state)
    print(f"\n  Old score (0-100) = {old_report.score:.2f}")
    print(f"  Old win_rate = {old_report.chosen_win_rate:.2%}")

    # New rank score
    print(f"\n  Sweeping difficulty [{MIN_DIFF}..{MAX_DIFF}] step={DIFF_STEP} ...")
    rank, details = compute_rank_score(definition, obs["adventurers"])

    results["Baseline:Greedy"] = {
        "rank": rank,
        "old_score": old_report.score,
        "party_size": obs["party_size"],
    }

    _print_sweep(details)
    print(f"\n  >>> RANK SCORE = {rank:.2f}")

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"  {'Team':<40s}  {'Rank':>8s}  {'Old (0-100)':>12s}  {'Party':>5s}")
    print(f"  {'-'*40}  {'-'*8}  {'-'*12}  {'-'*5}")
    for label, data in sorted(results.items(), key=lambda x: -x[1]["rank"]):
        old = f"{data['old_score']:.2f}" if data["old_score"] is not None else "—"
        party = data.get("party_size", "?")
        print(f"  {label:<40s}  {data['rank']:8.2f}  {old:>12s}  {party:>5d}")


def _print_sweep(details: list[tuple[int, float, float]]) -> None:
    """Print the difficulty sweep details, abbreviating long zero tails."""
    print(f"\n  {'Diff':>6s}  {'Perf':>6s}  {'Contrib':>8s}  {'Cumul':>8s}")
    print(f"  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*8}")

    cumulative = 0.0
    last_nonzero_idx = 0
    for i, (d, perf, _) in enumerate(details):
        if perf > 0.001:
            last_nonzero_idx = i

    printed_ellipsis = False
    for i, (d, perf, contrib) in enumerate(details):
        cumulative += contrib
        # Print if: near start, near end, significant performance, or last non-zero
        if i <= 2 or i >= len(details) - 1 or perf > 0.01 or i == last_nonzero_idx:
            print(f"  {d:6d}  {perf:6.3f}  {contrib:8.2f}  {cumulative:8.2f}")
            printed_ellipsis = False
        elif not printed_ellipsis:
            print(f"  {'...':>6s}  {'...':>6s}  {'...':>8s}  {'...':>8s}")
            printed_ellipsis = True


if __name__ == "__main__":
    main()
