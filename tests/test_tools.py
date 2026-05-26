from pathlib import Path
from dataclasses import replace

import pytest

from guild_manager_bench.bench.llm import (
    GuildManagerTools,
    ToolCallError,
    TurnToolHarness,
    tool_schemas,
)
from guild_manager_bench.game.state import LlmToolRules


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
    assert "preview_battle" not in names
    assert "preview_battle" in {
        schema["name"]
        for schema in tool_schemas(expose_battle_preview=True)
    }


def test_toolbox_runs_actions_through_typed_tools() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    started = tools.start_session("tool-test")
    session_id = started["session_id"]

    crafted = tools.craft_equipment(session_id, "iron_sword_recipe")
    assert crafted["ok"] is True
    assert "observation" not in crafted
    assert crafted["event"]["summary"] == "合成 打造铁剑"
    assert crafted["event"]["changes"][0]["label"] == "金币"

    equipped = tools.equip_item(session_id, "vanguard", "eq_0001")
    assert equipped["ok"] is True
    assert "observation" not in equipped
    assert equipped["event"]["summary"] == "先锋 装备 铁剑"

    observation = tools.get_observation(session_id)["observation"]
    assert observation["adventurers"][0]["equipment"][0]["instance_id"] == "eq_0001"
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
    assert "observation" not in ended
    assert "next_state_summary" not in ended
    assert len(ended["turn_result"]["battles"]) == 1
    battle = ended["turn_result"]["battles"][0]
    assert "combat" not in battle
    assert "adventurer_before_resources" not in battle
    assert "adventurer_after_resources" not in battle
    assert "combat_summary" not in battle


def test_invalid_action_returns_rejection_event_without_raising() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session()["session_id"]

    result = tools.craft_equipment(session_id, "missing_recipe")

    assert result["ok"] is False
    assert result["event"]["type"] == "action_rejected"
    assert "unknown recipe" in result["error"]
    assert "observation" not in result


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
    assert "next_state_summary" not in ended
    assert ended["tool_budget"]["allowed_tools"] == []


def test_battle_preview_tool_is_controlled_by_definition_switch() -> None:
    base_tools = GuildManagerTools.from_data_dir(_data_dir())
    disabled_tools = GuildManagerTools(
        replace(
            base_tools.definition,
            llm_tools=LlmToolRules(expose_battle_preview=False),
        )
    )
    disabled_session_id = disabled_tools.start_session("preview-disabled")["session_id"]
    disabled_harness = TurnToolHarness(
        disabled_tools,
        disabled_session_id,
        max_tool_calls=2,
    )
    assert "preview_battle" not in {
        schema["name"]
        for schema in disabled_harness.tool_schemas()
    }
    disabled = disabled_harness.call_tool(
        "preview_battle",
        {
            "adventurer_id": "vanguard",
            "monster_id": "turn_1_monster_1",
        },
    )
    assert disabled["ok"] is False
    assert "unknown tool" in disabled["error"]
    assert disabled["tool_budget"]["used"] == 1

    enabled_tools = GuildManagerTools(
        replace(
            base_tools.definition,
            llm_tools=LlmToolRules(expose_battle_preview=True),
        )
    )
    session = enabled_tools.start_session("preview-enabled")
    session_id = session["session_id"]
    before = enabled_tools.get_observation(session_id)["observation"]
    harness = TurnToolHarness(enabled_tools, session_id, max_tool_calls=2)

    assert "preview_battle" in {
        schema["name"]
        for schema in harness.tool_schemas()
    }

    monster_id = before["monsters"][0]["monster_id"]
    preview = harness.call_tool(
        "preview_battle",
        {
            "adventurer_id": "vanguard",
            "monster_id": monster_id,
        },
    )
    after = enabled_tools.get_observation(session_id)["observation"]

    assert preview["ok"] is True
    assert preview["preview"]["adventurer_id"] == "vanguard"
    assert preview["preview"]["monster_id"] == monster_id
    assert "adventurer_resources" in preview["preview"]
    assert "events" in preview["preview"]
    assert preview["tool_budget"]["used"] == 1
    assert after["turn"] == before["turn"]
    assert after["adventurers"][0]["resources"] == before["adventurers"][0]["resources"]
    assert after["materials"] == before["materials"]


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"
