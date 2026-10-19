from copy import deepcopy
from pathlib import Path

import pytest

from guild_manager_bench.bench.llm.formatting import skill_summary, skill_summary_lines
from guild_manager_bench.bench.llm.prompts import build_system_prompt, build_turn_prompt
from guild_manager_bench.bench.llm.runner import _format_tool_result_for_model
from guild_manager_bench.bench.llm.tools import GuildManagerTools


EXPECTED_NAMES = ("高优先技能", "同级先发", "同级后发", "即时技能", "被动乙", "被动甲")


@pytest.fixture
def unordered_skills():
    return [
        {"skill_id": "passive_z", "name": "被动乙", "kind": "passive", "priority": 0},
        {"skill_id": "instant", "name": "即时技能", "kind": "active", "priority": 0, "free": True},
        {"skill_id": "active_b", "name": "同级后发", "kind": "active", "priority": 5},
        {"skill_id": "passive_a", "name": "被动甲", "kind": "passive", "priority": 0},
        {"skill_id": "active_z", "name": "高优先技能", "kind": "active", "priority": 20, "mp_cost": 100},
        {"skill_id": "active_a", "name": "同级先发", "kind": "active", "priority": 5},
    ]


def test_skill_summaries_follow_combat_order_without_mutating_input(unordered_skills):
    original = deepcopy(unordered_skills)

    lines = skill_summary_lines(unordered_skills)

    assert tuple(line.split("：", 1)[0] for line in lines) == EXPECTED_NAMES
    assert skill_summary(unordered_skills) == "; ".join(lines)
    assert "需要100MP" in lines[0]
    assert "即时主动技能" in lines[3]
    assert all("优先级" not in line for line in lines)
    assert unordered_skills == original


@pytest.mark.parametrize(
    ("tool_name", "list_key", "id_key", "skills_key"),
    [
        ("get_party", "adventurers", "adventurer_id", "skills"),
        ("get_monsters", "monsters", "monster_id", "skills"),
        ("get_recruitment", "recruit_candidates", "candidate_id", "skills"),
        ("get_inventory", "equipment_inventory", "instance_id", "skills"),
        ("get_crafting", "crafting_recipes", "recipe_id", "output_skills"),
        ("get_upgrades", "global_upgrades", "upgrade_id", "skills"),
    ],
)
def test_query_skill_order_is_consistent(tool_name, list_key, id_key, skills_key, unordered_skills):
    result = {
        "ok": True,
        list_key: [{id_key: "test-object", "name": "测试对象", skills_key: unordered_skills}],
    }

    text = _format_tool_result_for_model(tool_name, result)

    positions = [text.index(name + "：") for name in EXPECTED_NAMES]
    assert positions == sorted(positions)


@pytest.mark.parametrize("list_key", ["adventurers", "monsters"])
def test_turn_prompt_skill_order_matches_queries(list_key, unordered_skills):
    data_dir = Path(__file__).resolve().parents[1] / "data" / "presets" / "default"
    tools = GuildManagerTools.from_data_dir(data_dir)
    session_id = "skill-order"
    initial = tools.start_session(session_id)["observation"]
    candidate_id = initial["recruit_candidates"][0]["candidate_id"]
    assert tools.recruit_adventurer(session_id, candidate_id)["ok"]
    observation = tools.get_observation(session_id)["observation"]
    observation[list_key][0]["skills"] = unordered_skills

    text = build_turn_prompt(observation)

    positions = [text.index(name + "：") for name in EXPECTED_NAMES]
    assert positions == sorted(positions)


def test_system_prompt_explains_skill_selection_and_instant_skill_limit():
    prompt = build_system_prompt()

    assert "按列表顺序检查主动技能" in prompt
    assert "仅释放首个满足触发条件、MP充足且次数未耗尽的技能" in prompt
    assert "无可用技能则进行普攻" in prompt
    assert "普通主动技能会替代本次普攻" in prompt
    assert "“即时”主动技能释放后会立即追加一次普攻，不会继续释放其他主动技能" in prompt
    assert "被动技能不参与排序，满足条件时动态生效" in prompt
