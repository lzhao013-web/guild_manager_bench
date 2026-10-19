from pathlib import Path

import pytest

from guild_manager_bench.bench.llm.formatting import skill_summary_lines
from guild_manager_bench.bench.llm.prompts import (
    build_endgame_system_prompt,
    build_system_prompt,
    build_turn_prompt,
)
from guild_manager_bench.bench.llm.runner import _format_tool_result_for_model
from guild_manager_bench.bench.llm.tools import GuildManagerTools


@pytest.mark.parametrize("talent_tree_enabled", [False, True])
def test_system_prompt_explains_combat_timing_and_resource_boundaries(talent_tree_enabled):
    prompt = build_system_prompt(talent_tree_enabled=talent_tree_enabled)
    for rule in (
        "确定性的自动1V1",
        "经营讨伐使用角色当前HP/MP",
        "行动条从0开始",
        "达到100即可行动，行动后扣除100",
        "依次比较当前行动条、速度（SPD），仍相同则冒险者先动",
        "先结算其身上的持续伤害、治疗和回魔",
        "若仍存活，再检查主动技能",
        "判定通过后才扣除本次技能的MP消耗",
        "不包含战斗中的被动与状态加成",
        "（面板属性＋固定加成之和）×各倍率之积",
        "双方累计行动次数",
        "状态持有者自己的行动次数",
        "累计达到1000次行动",
        "经营讨伐只有胜利才获得奖励",
        "战败不会永久失去角色",
        "包括未出战和战败者",
        "不跨场保留",
    ):
        assert rule in prompt
    assert prompt.count("技能条件中的“行动序号”") == 1
    assert "行动条件：" not in prompt
    assert "每回合最多允许 20 次" in prompt
    assert "工具调用" in prompt
    assert "敌方损失HP的比例" not in prompt


def test_system_prompt_keeps_recovery_formula_and_active_skill_rules():
    prompt = build_system_prompt(
        turn_recovery_rules={"hp": 5, "mp": 4, "hp_percent": 0.08, "mp_percent": 0.15},
        tactics_enabled=True,
    )
    assert "HP回复 = 5 + 最大HP×8% + 战后回血属性" in prompt
    assert "MP回复 = 4 + 最大MP×15% + 战后回魔属性" in prompt
    assert "即使技能只有治疗或增益效果" in prompt
    assert "不会继续释放其他主动技能" in prompt
    assert "也不免除其MP消耗" in prompt
    assert "configure_tactics" in prompt
    assert "释放优先级或停用指定主动技能" in prompt


def test_skill_text_uses_explicit_count_units_and_damage_segments():
    skill = {
        "skill_id": "test_skill", "name": "测试技能", "kind": "active",
        "condition": {"type": "all", "conditions": [
            {"type": "action_index_gte", "value": 2},
            {"type": "action_index_lte", "value": 4},
        ]},
        "effects": [
            {"type": "damage_multiplier", "value": 1.5},
            {"type": "damage_bonus", "value": 5},
            {"type": "apply_status", "target": "target", "status": {
                "name": "测试中毒", "duration": 3, "stack_mode": "refresh",
                "effects": [{"type": "true_damage", "value": 4}],
            }},
        ],
    }
    text = skill_summary_lines([skill])[0]
    assert "双方累计行动序号至少为2" in text
    assert "双方累计行动序号不超过4" in text
    assert "持续状态持有者的3次行动" in text
    assert "状态持有者每次行动开始受到4点无视防御伤害" in text
    assert "造成一段“扣防后普攻伤害×1.5”的伤害，向下取整且最低1点" in text
    assert "造成一段“普通攻击伤害＋5”的伤害" in text
    assert "在普通攻击伤害上额外" not in text

    # The same contract must reach the model through a query, not just a helper.
    query_text = _format_tool_result_for_model("get_inventory", {
        "equipment_inventory": [{"instance_id": "eq_1", "name": "测试装备", "skills": [skill]}],
    })
    assert text in query_text


def test_scoring_explanation_only_appears_in_endgame_prompts():
    tools = GuildManagerTools.from_data_dir(Path(__file__).resolve().parents[1] / "data/presets/advanced")
    observation = tools.start_session("battle-rule-text")["observation"]
    early = build_turn_prompt({**observation, "turn": 30}, endgame_start_turn=31)
    late = build_turn_prompt({**observation, "turn": 31}, endgame_start_turn=31)
    explanation = "同时计入敌方损失HP的比例和己方剩余HP的比例"
    assert explanation not in early
    for text in (late, build_endgame_system_prompt(31, 35)):
        assert explanation in text
        assert "战败或平局也可能获得部分分数" in text
        assert "不同难度下的表现" in text
        assert "满HP/MP" in text
        assert "最佳一一配对" in text
        assert "operations=[]" in text
        assert "试算不会实际执行方案" in text
