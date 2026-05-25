from pathlib import Path

import pytest

from guild_manager_bench.bench.llm import (
    GuildManagerTools,
    ToolCallError,
    TurnToolHarness,
    tool_schemas,
)


def test_tool_schemas_expose_agent_facing_tools() -> None:
    names = {schema["name"] for schema in tool_schemas()}

    assert {
        "get_observation",
        "craft_equipment",
        "equip_item",
        "unequip_item",
        "allocate_experience",
        "purchase_upgrade",
        "end_turn",
        "get_events",
    }.issubset(names)
    assert "start_session" not in names
    assert "get_available_actions" not in names
    assert "list_sessions" not in names
    assert "get_score" not in names


def test_toolbox_runs_actions_through_typed_tools() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    started = tools.start_session("tool-test")
    session_id = started["session_id"]

    crafted = tools.craft_equipment(session_id, "iron_sword_recipe")
    assert crafted["ok"] is True
    assert crafted["observation"]["equipment_inventory"][0]["instance_id"] == "eq_0001"

    equipped = tools.equip_item(session_id, "vanguard", "eq_0001")
    assert equipped["ok"] is True
    assert equipped["observation"]["adventurers"][0]["equipment"][0]["instance_id"] == "eq_0001"

    observation = tools.get_observation(session_id)["observation"]
    ended = tools.end_turn(
        session_id,
        [
            {
                "adventurer_id": "vanguard",
                "monster_id": observation["monsters"][0]["monster_id"],
            }
        ],
    )
    assert ended["ok"] is True
    assert ended["observation"]["turn"] == 2
    assert len(ended["turn_result"]["battles"]) == 1


def test_invalid_action_returns_rejection_event_without_raising() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session()["session_id"]

    result = tools.craft_equipment(session_id, "missing_recipe")

    assert result["ok"] is False
    assert result["event"]["type"] == "action_rejected"
    assert "unknown recipe" in result["error"]


def test_call_tool_dispatches_and_validates_tool_name() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    started = tools.start_session("dispatch-test")

    assert started["session_id"] == "dispatch-test"
    observation = tools.call_tool("get_observation", {"session_id": "dispatch-test"})
    assert observation["observation"]["session_id"] == "dispatch-test"

    with pytest.raises(ToolCallError):
        tools.call_tool("unknown_tool", {})


def test_turn_tool_harness_enforces_budget_and_still_allows_end_turn() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("budget-test")["session_id"]
    harness = TurnToolHarness(tools, session_id, max_tool_calls=1)

    observed = harness.call_tool("get_observation")
    assert observed["tool_budget"]["remaining"] == 0
    assert observed["tool_budget"]["end_turn_required"] is True

    blocked = harness.call_tool("get_events")
    assert blocked["ok"] is False
    assert blocked["tool_budget"]["allowed_tools"] == ["end_turn"]

    ended = harness.call_tool("end_turn", {"hunts": []})
    assert ended["ok"] is True
    assert ended["observation"]["turn"] == 2
    assert ended["tool_budget"]["allowed_tools"] == []


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"
