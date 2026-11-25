"""模型决策接口：整队预览、投影编号、目录简表与筛选。"""
from copy import deepcopy
from dataclasses import replace

import pytest

from guild_manager_bench.bench.llm.harness import TurnToolHarness
from guild_manager_bench.bench.llm.runner import _format_tool_result_for_model, _memo_store_from_replay
from guild_manager_bench.bench.llm.prompts import build_turn_prompt
from guild_manager_bench.game.engine import spawn_monsters
from guild_manager_bench.bench.llm.tools import GuildManagerTools
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.state import AdventurerState


@pytest.fixture
def game():
    definition = GuildManagerTools.from_data_dir("data/presets/advanced").definition
    strong = CombatStats(hp=1000, mp=50, attack=300, defense=100, speed=100, recovery=5)
    weak = CombatStats(hp=10, mp=0, attack=1, defense=0, speed=1, recovery=0)
    heroes = (
        AdventurerState("strong", "强者", strong, CombatResources(900, 20), template_id="cleric"),
        AdventurerState("weak", "弱者", weak, CombatResources(1, 0), template_id="jester"),
        AdventurerState("rest", "休整者", strong, CombatResources(100, 1), template_id="bloodfiend"),
    )
    definition = replace(
        definition, content=replace(definition.content, adventurers=heroes), starting_gold=10000,
        starting_materials={**definition.starting_materials, "iron_ore": 30, "bone": 30},
        rules=replace(definition.rules, recruitment=replace(definition.rules.recruitment, initial_party_size_limit=6)),
        llm_tools=replace(definition.llm_tools, endgame_start_turn=None),
    )
    tools = GuildManagerTools(definition)
    tools.start_session("support")
    return tools, "support", TurnToolHarness(tools, "support", max_tool_calls=20)


def test_hunts_match_real_settlement_rewards_recovery_and_logs(game):
    tools, sid, harness = game
    before = tools.get_state(sid)
    events, refs = deepcopy(tools.get_events(sid)), deepcopy(harness._turn_refs)
    hunts = [{"adventurer_id": 1, "monster_id": 1}, {"adventurer_id": 2, "monster_id": 2}]
    preview = harness.call_tool("preview_hunts", {"hunts": hunts})
    assert preview["ok"], preview
    assert tools.get_state(sid) is before
    assert tools.get_events(sid) == events
    assert harness._turn_refs == refs
    assert preview["tool_budget"]["used"] == preview["tool_budget"]["battle_preview_used"] == 1
    assert [b["won"] for b in preview["battles"]] == [True, False]
    assert preview["battles"][1]["reward"] == {"gold": 0, "experience": 0, "materials": {}}
    assert len({b["battle_id"] for b in preview["battles"]}) == 2
    for battle in preview["battles"]:
        log = tools.get_battle_log(sid, battle["battle_id"])
        assert log["battle"]["outcome"] == battle["outcome"]
    text = _format_tool_result_for_model("preview_hunts", preview)
    assert "真实状态未改变" in text and "休整者" in text and "总收益" in text
    real = harness.call_tool("end_turn", {"hunts": hunts})
    assert real["ok"]
    after = tools.get_state(sid)
    assert after.gold - before.gold == preview["reward"]["gold"]
    assert after.experience_pool - before.experience_pool == preview["reward"]["experience"]
    for projected, actual in zip(preview["party_after_recovery"], after.adventurers, strict=True):
        assert projected["resources"] == {"current_hp": actual.resources.current_hp, "current_mp": actual.resources.current_mp}
    for projected, actual in zip(preview["battles"], real["turn_result"]["battles"], strict=True):
        assert projected["won"] == actual["won"] and projected["reward"] == actual["reward"]


@pytest.mark.parametrize("hunts", [
    None, {}, "bad", [None], [{"adventurer_id": 1}],
    [{"adventurer_id": 1, "monster_id": 1, "extra": True}],
    [{"adventurer_id": 1, "monster_id": 1}] * 2,
    [{"adventurer_id": 1, "monster_id": 1}, {"adventurer_id": 2, "monster_id": 1}],
    [{"adventurer_id": 1, "monster_id": 1}] * 7,
    [{"adventurer_id": 999, "monster_id": 1}],
    [{"adventurer_id": True, "monster_id": 1}],
    [{"adventurer_id": "$missing", "monster_id": 1}],
])
def test_invalid_hunts_are_atomic_and_consume_one_plan_quota(game, hunts):
    tools, sid, harness = game
    before, events = tools.get_state(sid), deepcopy(tools.get_events(sid))
    result = harness.call_tool("preview_hunts", {"hunts": hunts})
    assert not result["ok"]
    assert result["tool_budget"]["used"] == result["tool_budget"]["battle_preview_used"] == 1
    assert tools.get_state(sid) is before and tools.get_events(sid) == events
    assert sid not in tools._battle_previews


def test_only_three_whole_hunt_plans_are_allowed_and_old_tool_is_not_exposed(game):
    tools, sid, harness = game
    names = {s["name"] for s in harness.tool_schemas()}
    assert "preview_hunts" in names and "preview_battle" not in names
    for _ in range(3):
        assert harness.call_tool("preview_hunts", {"hunts": []})["ok"]
    blocked = harness.call_tool("preview_hunts", {"hunts": []})
    assert not blocked["ok"] and blocked["tool_budget"]["used"] == 3
    assert blocked["tool_budget"]["battle_preview_used"] == 3
    assert harness.call_tool("end_turn", {"hunts": []})["ok"]


def test_hunts_can_use_preparation_projection_and_recruit_alias_without_execution(game):
    tools, sid, harness = game
    before, refs = tools.get_state(sid), deepcopy(harness._turn_refs)
    plan = harness.call_tool("preview_team_power", {"operations": [
        {"tool": "recruit_adventurer", "arguments": {"candidate_id": 1}, "as": "new"},
        {"tool": "craft_equipment", "arguments": {"recipe_id": 1}, "as": "sword"},
        {"tool": "equip_item", "arguments": {"adventurer_id": "$new", "equipment_instance_id": "$sword"}},
    ]})
    assert plan["ok"], plan
    number = next(a["id"] for a in plan["plan_entities"]["adventurers"] if a["id"] not in refs["adventurer"].values())
    projected = harness.call_tool("preview_hunts", {"plan_id": plan["plan_id"], "hunts": [
        {"adventurer_id": "$new", "monster_id": 2},
    ]})
    numbered = harness.call_tool("preview_hunts", {"plan_id": plan["plan_id"], "hunts": [
        {"adventurer_id": number, "monster_id": 2},
    ]})
    assert projected["ok"] and numbered["ok"]
    assert projected["battles"][0]["adventurer_resources"] == numbered["battles"][0]["adventurer_resources"]
    assert tools.get_state(sid) is before and harness._turn_refs == refs
    assert projected["_llm_refs"] == refs
    assert len(projected["_preview_refs"]["adventurer"]) == len(refs["adventurer"]) + 1
    text = _format_tool_result_for_model("preview_hunts", projected)
    assert "recruit_0001" not in text
    assert harness.call_tool("execute_plan", {"plan_id": plan["plan_id"]})["ok"]
    actual = harness.call_tool("end_turn", {"hunts": [{"adventurer_id": number, "monster_id": 2}]})
    assert actual["ok"]
    assert actual["turn_result"]["battles"][0]["reward"] == projected["battles"][0]["reward"]


def test_prepared_refresh_has_independent_monster_numbers_and_cached_logs(game):
    tools, sid, harness = game
    before, refs = tools.get_state(sid), deepcopy(harness._turn_refs)
    plan = harness.call_tool("preview_team_power", {"operations": [
        {"tool": "refresh_monsters", "arguments": {"locked_monster_ids": []}},
    ]})
    assert plan["ok"]
    number = plan["plan_entities"]["monsters"][0]["id"]
    assert number not in refs["monster"].values()
    preview = harness.call_tool("preview_hunts", {"plan_id": plan["plan_id"], "hunts": [
        {"adventurer_id": 1, "monster_id": number},
    ]})
    assert preview["ok"]
    assert tools.get_battle_log(sid, preview["battles"][0]["battle_id"])["ok"]
    assert tools.get_state(sid) is before and harness._turn_refs == refs
    assert not harness.call_tool("preview_hunts", {"hunts": [{"adventurer_id": 1, "monster_id": number}]})["ok"]
    assert harness.call_tool("purchase_upgrade", {"upgrade_id": 6})["ok"]
    stale = harness.call_tool("preview_hunts", {"plan_id": plan["plan_id"], "hunts": []})
    assert not stale["ok"] and "状态已改变" in stale["error"]


@pytest.mark.parametrize("invalidated_by", ["other_session", "execution", "next_turn"])
def test_projected_hunts_reject_wrong_session_executed_and_expired_receipts(game, invalidated_by):
    tools, sid, harness = game
    plan = harness.call_tool("preview_team_power", {"operations": [
        {"tool": "craft_equipment", "arguments": {"recipe_id": 1}},
    ]})
    assert plan["ok"]
    if invalidated_by == "other_session":
        sid = tools.start_session("other")["session_id"]
        harness = TurnToolHarness(tools, sid, max_tool_calls=5)
    elif invalidated_by == "execution":
        assert harness.call_tool("execute_plan", {"plan_id": plan["plan_id"]})["ok"]
    else:
        assert harness.call_tool("end_turn", {"hunts": []})["ok"]
        harness = TurnToolHarness(tools, sid, max_tool_calls=5)
    state, events = tools.get_state(sid), deepcopy(tools.get_events(sid))
    result = harness.call_tool("preview_hunts", {"plan_id": plan["plan_id"], "hunts": []})
    assert not result["ok"]
    assert tools.get_state(sid) is state and tools.get_events(sid) == events


def test_six_distinct_hunts_use_one_preview_quota(game):
    tools, sid, _ = game
    state = tools.get_state(sid)
    heroes = tuple(replace(state.adventurers[0], adventurer_id=f"hero_{i}", name=f"角色{i}") for i in range(6))
    tools._sessions[sid].state = replace(state, turn=11, adventurers=heroes,
                                         current_monsters=spawn_monsters(tools.definition, 11))
    harness = TurnToolHarness(tools, sid, max_tool_calls=3)
    preview = harness.call_tool("preview_hunts", {"hunts": [
        {"adventurer_id": i, "monster_id": i} for i in range(1, 7)
    ]})
    assert preview["ok"] and len(preview["battles"]) == 6
    assert len(preview["party_after_recovery"]) == 6
    assert preview["tool_budget"]["used"] == preview["tool_budget"]["battle_preview_used"] == 1


def test_long_memo_survives_restore_and_reaches_next_turn_prompt(game):
    tools, sid, harness = game
    content = "长期计划与比较依据。" * 350
    assert 500 < len(content) <= 4000
    result = harness.call_tool("write_memo", {"content": content})
    assert result["ok"] and result["memo"]["max_chars"] == 4000
    restored = _memo_store_from_replay({"turns": [{"turn": 1, "status": "completed", "steps": [
        {"type": "tool_result", "name": "write_memo", "arguments": {"content": content}, "result": result},
    ]}]})
    assert restored.snapshot().content == content
    assert content in build_turn_prompt(tools.get_observation(sid)["observation"], memo=restored.snapshot())


def test_final_preparation_can_only_preview_rest(game):
    tools, sid, _ = game
    tools._sessions[sid].state = replace(tools.get_state(sid), turn=35)
    harness = TurnToolHarness(tools, sid, max_tool_calls=5)
    assert not harness.call_tool("preview_hunts", {"hunts": [{"adventurer_id": 1, "monster_id": 1}]})["ok"]
    assert harness.call_tool("preview_hunts", {"hunts": []})["ok"]
    assert tools.get_state(sid).turn == 35


def test_catalogs_default_to_summary_but_keep_full_facts_and_stable_ids(game):
    tools, sid, harness = game
    before, refs = tools.get_state(sid), deepcopy(harness._turn_refs)
    summary = harness.call_tool("get_crafting", {})
    full = harness.call_tool("get_crafting", {"detail": "full"})
    assert summary["detail"] == "summary"
    assert summary["crafting_recipes"] == full["crafting_recipes"]
    assert len(summary["crafting_recipes"]) == 57
    text = _format_tool_result_for_model("get_crafting", summary)
    original = _format_tool_result_for_model("get_crafting", full)
    assert len(text) < len(original)
    assert "HP:0" not in text and "产物技能: 无" not in text
    assert "〔护甲·神官（class_id=cleric）·" in text
    assert "sacred_veil" in text and "耗6MP" in text and "每场1次" in text
    assert "即时主动，施放后追加普攻" in text
    assert "持续状态持有者的6次行动" in text and "恢复8%最大HP" in text
    assert "攻击力的255%作为无视防御伤害" in text
    assert "双方累计行动序号至少为5" in text and "自身MP不低于42%" in text
    selected = harness.call_tool("get_crafting", {"ids": [27], "detail": "full"})
    assert selected["crafting_recipes"] == [full["crafting_recipes"][26]]
    assert "- 27 " in _format_tool_result_for_model("get_crafting", selected)
    assert tools.get_state(sid) is before and harness._turn_refs == refs
    # Direct Python callers retain their complete-view default.
    assert set(tools.get_crafting(sid)) == {"session_id", "gold", "materials", "crafting_recipes"}


def test_class_id_is_present_in_fresh_turn_member_and_candidate_context(game):
    tools, sid, harness = game
    schema = next(s for s in harness.tool_schemas() if s["name"] == "get_crafting")
    legal = schema["parameters"]["properties"]["class_id"]["enum"]
    expected = {t.template_id for t in tools.definition.content.recruitable_adventurers}
    assert set(legal) == expected
    assert {"jester", "cannoneer", "ascetic_monk"} <= set(legal)
    assert not {"combo_adept", "combo", "combo_artist", "gunner", "ascetic"} & set(legal)
    candidate = harness.call_tool("get_recruitment", {})
    visible = _format_tool_result_for_model("get_recruitment", candidate)
    for item in candidate["recruit_candidates"]:
        assert f"{item['name']}（class_id={item['template_id']}）" in visible
    party = _format_tool_result_for_model("get_party", tools.get_party(sid))
    assert "弱者（class_id=jester）" in party
    assert harness.call_tool("end_turn", {"hunts": []})["ok"]
    # 不依赖上回合查询、工具消息或备忘录，也能从新回合直接拿到筛选值。
    prompt = build_turn_prompt(tools.get_observation(sid)["observation"])
    assert "弱者（class_id=jester）" in prompt
    assert "强者（class_id=cleric）" in prompt


def test_class_enum_is_definition_specific_and_does_not_modify_generic_schema(game):
    from guild_manager_bench.bench.llm.tools import tool_schemas

    tools, _, harness = game
    assert "enum" in next(s for s in harness.tool_schemas() if s["name"] == "get_crafting")["parameters"]["properties"]["class_id"]
    generic = next(s for s in tool_schemas() if s["name"] == "get_crafting")
    assert "enum" not in generic["parameters"]["properties"]["class_id"]
    content = replace(tools.definition.content, recruitable_adventurers=(), adventurers=tuple(
        replace(a, template_id="") for a in tools.definition.content.adventurers
    ))
    classless = GuildManagerTools(replace(tools.definition, content=content))
    schema = next(s for s in classless.list_tool_schemas() if s["name"] == "get_crafting")
    assert "class_id" not in schema["parameters"]["properties"]


@pytest.mark.parametrize("detail", ["summary", "full"])
def test_recipe_and_inventory_restrictions_show_names_and_class_ids(game, detail):
    tools, sid, harness = game
    result = harness.call_tool("get_crafting", {"ids": [27], "detail": detail})
    assert "神官（class_id=cleric）" in _format_tool_result_for_model("get_crafting", result)
    recipe = tools.definition.content.crafting_recipes[28]
    state = tools.get_state(sid)
    tools._sessions[sid].state = replace(state, materials={
        **state.materials, **{cost.material_id: cost.quantity for cost in recipe.material_costs},
    })
    assert harness.call_tool("craft_equipment", {"recipe_id": 29})["ok"]
    free = _format_tool_result_for_model("get_inventory", harness.call_tool("get_inventory", {}))
    assert "连击师（class_id=jester）" in free and "吸血魔（class_id=bloodfiend）" in free
    assert harness.call_tool("equip_item", {"adventurer_id": 2, "equipment_instance_id": 1})["ok"]
    equipped = _format_tool_result_for_model("get_inventory", harness.call_tool("get_inventory", {}))
    assert "[装备者 2]" in equipped and "连击师（class_id=jester）" in equipped


@pytest.mark.parametrize("wrong", ["combo_adept", "combo", "ascetic", "combo_artist", "gunner"])
def test_wrong_class_id_returns_actionable_legal_values(game, wrong):
    _, _, harness = game
    result = harness.call_tool("get_crafting", {"class_id": wrong})
    assert not result["ok"]
    assert "可选值" in result["error"]
    assert all(value in result["error"] for value in ("jester", "cannoneer", "ascetic_monk"))


def test_crafting_filters_include_universal_equipment_and_actual_discount(game):
    tools, sid, harness = game
    selected = harness.call_tool("get_crafting", {"slot": "armor", "class_id": "cleric"})
    assert selected["crafting_recipes"]
    assert any(not r["output_allowed_classes"] for r in selected["crafting_recipes"])
    assert all(r["output_slot"] == "armor" and (not r["output_allowed_classes"] or "cleric" in r["output_allowed_classes"])
               for r in selected["crafting_recipes"])
    empty = harness.call_tool("get_crafting", {"name_contains": "不存在的配方"})
    assert empty["crafting_recipes"] == [] and "0项" in _format_tool_result_for_model("get_crafting", empty)
    assert harness.call_tool("purchase_upgrade", {"upgrade_id": 6})["ok"]
    # Give this fixture the XP required by a real economy-node purchase.
    tools._sessions[sid].state = replace(tools.get_state(sid), experience_pool=100)
    assert harness.call_tool("purchase_upgrade", {"upgrade_id": 3})["ok"]
    discounted = harness.call_tool("get_crafting", {"ids": [1], "craftable_only": True})
    assert discounted["crafting_recipes"][0]["gold_cost"] == 21
    assert "21金" in _format_tool_result_for_model("get_crafting", discounted)


def test_upgrade_filters_and_summary_preserve_missing_and_exclusions(game):
    tools, sid, harness = game
    full = tools.get_upgrades(sid)
    summary = harness.call_tool("get_upgrades", {})
    assert summary["global_upgrades"] == full["global_upgrades"]
    text = _format_tool_result_for_model("get_upgrades", summary)
    assert "前置未购 31稳固战线" in text
    assert "自身HP不高于45%" in text and "自身防御×1.4" in text
    assert "19圣殿骑士团" in text and "25秘法结社" in text
    assert "技能: 无" not in text and "属性 {}" not in text and "互斥: 无" not in text
    economy = harness.call_tool("get_upgrades", {"branch": "economy", "tier": 3, "status": "locked"})
    assert {u["upgrade_id"] for u in economy["global_upgrades"]} == {"training_manual_2", "trade_network"}
    assert harness.call_tool("purchase_upgrade", {"upgrade_id": 6})["ok"]
    purchased = harness.call_tool("get_upgrades", {"status": "purchased"})
    assert [u["upgrade_id"] for u in purchased["global_upgrades"]] == ["blacksmith"]
    assert "已购：6铁匠铺" in _format_tool_result_for_model("get_upgrades", purchased)
    assert "成本" not in _format_tool_result_for_model("get_upgrades", purchased)


@pytest.mark.parametrize("name,args", [
    ("get_crafting", {"detail": "all"}), ("get_crafting", {"slot": "missing"}),
    ("get_crafting", {"class_id": "missing"}), ("get_crafting", {"craftable_only": 1}),
    ("get_upgrades", {"branch": "missing"}), ("get_upgrades", {"tier": True}),
    ("get_upgrades", {"status": "missing"}), ("get_upgrades", {"ids": []}),
    ("get_upgrades", {"ids": [1, 1]}), ("get_upgrades", {"ids": [999]}),
    ("get_upgrades", {"name_contains": " "}),
])
def test_catalog_validation_is_read_only_and_uses_query_budget(game, name, args):
    tools, sid, harness = game
    before, events, refs = tools.get_state(sid), deepcopy(tools.get_events(sid)), deepcopy(harness._turn_refs)
    result = harness.call_tool(name, args)
    assert result["ok"] is False
    assert result["tool_budget"]["used"] == 1 and result["tool_budget"]["battle_preview_used"] == 0
    assert tools.get_state(sid) is before and tools.get_events(sid) == events and harness._turn_refs == refs
