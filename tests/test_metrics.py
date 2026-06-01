from dataclasses import replace
from pathlib import Path

from guild_manager_bench.bench.metrics import score_final_state
from guild_manager_bench.game.actions import RecruitAction
from guild_manager_bench.game.engine import apply_preparation_action, new_game
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.state import ScoringRules


def test_score_final_state_is_deterministic_and_bounded() -> None:
    definition = _small_scoring_definition()
    state = new_game(definition)

    first = score_final_state(definition, state)
    second = score_final_state(definition, state)

    assert first.to_dict() == second.to_dict()
    assert 0 <= first.score <= 100
    assert first.rank_score >= 0
    assert first.simulated_battles == len(state.adventurers) * definition.scoring.wave_size * definition.scoring.waves
    assert first.chosen_battles == len(state.adventurers) * definition.scoring.waves
    assert len(first.per_adventurer) == len(state.adventurers)
    assert "rank_score" in first.to_dict()
    assert len(first.rank_score_per_adventurer) == len(state.adventurers)
    assert all(item.rank_score >= 0 for item in first.per_adventurer)
    assert sum(item["rank_score"] for item in first.rank_score_per_adventurer) >= 0


def test_rank_score_is_deterministic() -> None:
    definition = _small_scoring_definition()
    state = new_game(definition)

    first = score_final_state(definition, state)
    second = score_final_state(definition, state)

    assert first.rank_score == second.rank_score


def test_rank_score_rewards_stronger_team() -> None:
    definition = _small_scoring_definition()
    state = new_game(definition)
    state = apply_preparation_action(
        definition,
        state,
        RecruitAction(candidate_id=state.recruit_candidates[0].candidate_id),
    )
    stronger_stats = CombatStats(
        hp=300,
        mp=100,
        attack=120,
        defense=80,
        speed=50,
        recovery=30,
    )
    stronger_adventurer = replace(
        state.adventurers[0],
        base_stats=stronger_stats,
        resources=CombatResources.full(stronger_stats),
    )
    stronger_state = replace(
        state,
        adventurers=(stronger_adventurer,) + state.adventurers[1:],
    )

    normal = score_final_state(definition, state)
    stronger = score_final_state(definition, stronger_state)

    assert stronger.score > normal.score
    assert stronger.rank_score > normal.rank_score


def _small_scoring_definition():
    definition = load_game_definition(
        Path(__file__).resolve().parents[1] / "data" / "presets" / "default"
    )
    return replace(
        definition,
        scoring=ScoringRules(
            seed=123,
            waves=8,
            wave_size=3,
            difficulty_factors=(0, 2),
            rank_min_diff=5,
            rank_max_diff=30,
            rank_step=5,
            rank_waves=4,
        ),
    )
