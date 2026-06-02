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
    schemas = tool_schemas()
    names = {schema["name"] for schema in schemas}

    assert {
        "get_crafting",
        "get_inventory",
        "get_upgrades",
        "get_recruitment",
        "craft_equipment",
        "equip_item",
        "unequip_item",
        "allocate_experience",
        "recruit_adventurer",
        "purchase_upgrade",
        "end_turn",
        "get_events",
        "dismiss_adventurer",
    }.issubset(names)
    assert "start_session" not in names
    assert "get_available_actions" not in names
    assert "list_sessions" not in names
    assert "get_score" not in names
    assert "get_observation" not in names
    assert "preview_battle" not in names
    for schema in schemas:
        parameters = schema["parameters"]
        assert "session_id" not in parameters.get("required", [])
        assert "session_id" not in parameters.get("properties", {})
    by_name = {schema["name"]: schema for schema in schemas}
    assert (
        by_name["allocate_experience"]["parameters"]["properties"]["adventurer_id"]["type"]
        == "integer"
    )
    assert (
        "数字 id"
        in by_name["equip_item"]["parameters"]["properties"]["equipment_instance_id"][
            "description"
        ]
    )
    assert (
        by_name["recruit_adventurer"]["parameters"]["properties"]["candidate_id"]["type"]
        == "integer"
    )
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

    adventurer_id = _recruit_first(tools, session_id)
    observation = tools.get_observation(session_id)["observation"]
    adventurer_name = observation["adventurers"][0]["name"]

    equipped = tools.equip_item(session_id, adventurer_id, "eq_0001")
    assert equipped["ok"] is True
    assert "observation" not in equipped
    assert equipped["event"]["summary"] == f"{adventurer_name} 装备 铁剑"

    observation = tools.get_observation(session_id)["observation"]
    assert observation["adventurers"][0]["equipment"][0]["instance_id"] == "eq_0001"

    recruitment = tools.get_recruitment(session_id)
    assert recruitment["party_size"] == 1
    assert recruitment["party_size_limit"] == 3
    assert recruitment["recruit_candidates"]

    ended = tools.end_turn(
        session_id,
        [
            {
                "adventurer_id": adventurer_id,
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
    assert "未找到制作配方" in result["error"]
    assert "observation" not in result


def test_call_tool_dispatches_and_validates_tool_name() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    started = tools.start_session("dispatch-test")

    assert started["session_id"] == "dispatch-test"
    party = tools.call_tool("get_party", {"session_id": "dispatch-test"})
    assert party["session_id"] == "dispatch-test"
    assert party["adventurers"] == []

    with pytest.raises(ToolCallError):
        tools.call_tool("unknown_tool", {})


def test_turn_tool_harness_enforces_budget_and_still_allows_end_turn() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("budget-test")["session_id"]
    harness = TurnToolHarness(tools, session_id, max_tool_calls=1)

    observed = harness.call_tool("get_party", {"session_id": "wrong-session"})
    assert observed["tool_budget"]["remaining"] == 0
    assert observed["tool_budget"]["end_turn_required"] is True
    assert observed["session_id"] == session_id

    blocked = harness.call_tool("get_events")
    assert blocked["ok"] is False
    assert blocked["tool_budget"]["allowed_tools"] == ["end_turn"]

    ended = harness.call_tool("end_turn", {"hunts": []})
    assert ended["ok"] is True
    assert "next_state_summary" not in ended
    assert ended["tool_budget"]["allowed_tools"] == []


def test_turn_tool_harness_records_memo() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("memo-test")["session_id"]
    harness = TurnToolHarness(tools, session_id, max_tool_calls=2)

    assert "write_memo" in {schema["name"] for schema in harness.tool_schemas()}

    result = harness.call_tool("write_memo", {"content": "下回合优先处理装备。"})

    assert result["ok"] is True
    assert result["memo"]["count"] == 1
    assert result["tool_budget"]["used"] == 1
    assert harness.memo_store.snapshot() == ("下回合优先处理装备。",)


def test_turn_tool_harness_resolves_numeric_ids() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("numeric-refs")["session_id"]
    harness = TurnToolHarness(tools, session_id, max_tool_calls=3)

    recruited = harness.call_tool("recruit_adventurer", {"candidate_id": 1})
    assert recruited["ok"] is True

    crafted = harness.call_tool("craft_equipment", {"recipe_id": 1})
    assert crafted["ok"] is True

    equipped = harness.call_tool(
        "equip_item",
        {
            "adventurer_id": 1,
            "equipment_instance_id": 1,
        },
    )
    assert equipped["ok"] is True

    observation = tools.get_observation(session_id)["observation"]
    assert observation["adventurers"][0]["equipment"][0]["instance_id"] == "eq_0001"

    ended = harness.call_tool(
        "end_turn",
        {"hunts": [{"adventurer_id": 1, "monster_id": 1}]},
    )
    assert ended["ok"] is True


def test_turn_tool_harness_resolves_recruit_candidate_numeric_id() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("recruit-numeric-refs")["session_id"]
    harness = TurnToolHarness(tools, session_id, max_tool_calls=2)

    recruited = harness.call_tool("recruit_adventurer", {"candidate_id": 1})

    assert recruited["ok"] is True
    observation = tools.get_observation(session_id)["observation"]
    assert len(observation["adventurers"]) == 1
    assert observation["adventurers"][-1]["adventurer_id"] == "recruit_0001"


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
    assert "未知工具" in disabled["error"]
    assert disabled["tool_budget"]["used"] == 1

    enabled_tools = GuildManagerTools(
        replace(
            base_tools.definition,
            llm_tools=LlmToolRules(expose_battle_preview=True),
        )
    )
    session = enabled_tools.start_session("preview-enabled")
    session_id = session["session_id"]
    adventurer_id = _recruit_first(enabled_tools, session_id)
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
            "adventurer_id": adventurer_id,
            "monster_id": monster_id,
        },
    )
    after = enabled_tools.get_observation(session_id)["observation"]

    assert preview["ok"] is True
    assert preview["preview"]["adventurer_id"] == adventurer_id
    assert preview["preview"]["monster_id"] == monster_id
    assert "adventurer_resources" in preview["preview"]
    assert "events" in preview["preview"]
    assert preview["tool_budget"]["used"] == 1
    assert after["turn"] == before["turn"]
    assert after["adventurers"][0]["resources"] == before["adventurers"][0]["resources"]
    assert after["materials"] == before["materials"]


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "presets" / "default"


def _recruit_first(tools: GuildManagerTools, session_id: str) -> str:
    recruitment = tools.get_recruitment(session_id)
    candidate_id = recruitment["recruit_candidates"][0]["candidate_id"]
    result = tools.recruit_adventurer(session_id, candidate_id)
    assert result["ok"] is True
    observation = tools.get_observation(session_id)["observation"]
    return observation["adventurers"][0]["adventurer_id"]
