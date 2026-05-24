from pathlib import Path

from guild_manager_bench.bench.operators.random_operator import RandomHuntOperator
from guild_manager_bench.bench.runner import run_operator
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.runtime.action_codec import (
    decode_end_turn_action,
    decode_preparation_action,
)
from guild_manager_bench.runtime.session import GameSession


def test_action_codec_decodes_preparation_and_end_turn_actions() -> None:
    craft = decode_preparation_action(
        {"type": "craft", "recipe_id": "iron_sword_recipe"}
    )
    end_turn = decode_end_turn_action(
        {
            "type": "end_turn",
            "hunts": [{"adventurer_id": "vanguard", "monster_id": "m1"}],
        }
    )

    assert craft.recipe_id == "iron_sword_recipe"
    assert end_turn.hunts[0].adventurer_id == "vanguard"


def test_game_session_applies_actions_and_builds_observation() -> None:
    definition = load_game_definition(_data_dir())
    session = GameSession(definition)

    event = session.apply_preparation(
        decode_preparation_action(
            {"type": "craft", "recipe_id": "iron_sword_recipe"}
        )
    )
    observation = session.observation()

    assert event.event_type == "preparation_applied"
    assert event.payload["summary"] == "合成 打造铁剑"
    assert any(change["label"] == "获得装备" for change in event.payload["changes"])
    assert observation["session_id"] == session.session_id
    assert observation["equipment_inventory"][0]["template_id"] == "iron_sword"
    assert observation["crafting_recipes"][0]["can_craft"] is False
    assert observation["adventurers"][0]["next_level"]["remaining"] > 0
    assert observation["adventurers"][0]["equipment_slots"][0]["slot"] == "main_hand"

    result, event = session.end_turn(decode_end_turn_action({"type": "end_turn"}))

    assert event.event_type == "turn_ended"
    assert event.payload["summary"] == "结束第 1 回合：0 场战斗，0 胜 0 负"
    assert event.payload["changes"][0]["label"] == "回合"
    assert result.state.turn == 2
    assert session.observation()["turn"] == 2


def test_random_operator_runner_finishes_without_direct_engine_calls() -> None:
    definition = load_game_definition(_data_dir())

    session = run_operator(definition, RandomHuntOperator(seed=1), max_steps=20)

    assert session.state is not None
    assert session.state.turn == definition.rules.max_turns + 1
    assert session.events[-1].event_type == "turn_ended"


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"
