from dataclasses import replace
from pathlib import Path

from guild_manager_bench.bench.metrics import score_final_state
from guild_manager_bench.game.engine import new_game
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
    assert first.simulated_battles == len(state.adventurers) * definition.scoring.wave_size * definition.scoring.waves
    assert first.chosen_battles == len(state.adventurers) * definition.scoring.waves
    assert len(first.per_adventurer) == len(state.adventurers)


def test_score_final_state_rewards_stronger_final_team() -> None:
    definition = _small_scoring_definition()
    state = new_game(definition)
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

    assert score_final_state(definition, stronger_state).score > score_final_state(definition, state).score


def _small_scoring_definition():
    definition = load_game_definition(Path(__file__).resolve().parents[1] / "data")
    return replace(
        definition,
        scoring=ScoringRules(
            seed=123,
            waves=8,
            wave_size=3,
            difficulty_factors=(0, 2),
        ),
    )
