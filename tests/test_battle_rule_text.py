from dataclasses import asdict
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
        "先结算行动者已有状态的持续伤害、治疗和回魔",
        "若仍存活，先结算自动回复，再检查普通主动技能" if talent_tree_enabled else "若仍存活，再检查主动技能",
        "通过后扣除本次技能的MP消耗",
        "不包含战斗中的被动与状态加成",
        "（面板属性＋固定加成之和）×各倍率之积",
        "双方累计行动次数",
        "状态持有者自己的行动次数",
        "累计达到1000次行动",
        "经营讨伐胜利后发放奖励",
        "战败角色仍留在队伍",
        "全部讨伐结束后，全体角色统一结算回合末恢复",
        "战后回血/回魔在此结算",
        "包括未出战和战败者",
        "战斗状态和技能次数逐场重置",
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


@pytest.mark.parametrize(
    "wave_size,resource_mode,resources,rank_min,rank_max,rank_step,rank_waves,total_waves",
    [
        (4, "full", "双方以满HP/MP独立开战", 2, 13, 5, 7, 21),
        (8, "current", "敌人以满HP/MP、角色以终局当前HP/MP独立开战", 5, 5, 10, 3, 3),
    ],
)
def test_system_prompt_discloses_evaluation_structure_from_scoring_rules(
    wave_size, resource_mode, resources, rank_min, rank_max, rank_step, rank_waves, total_waves,
):
    text = build_system_prompt(scoring_rules={
        "wave_size": wave_size, "resource_mode": resource_mode, "seed": 987654321,
        "rank_min_diff": rank_min, "rank_max_diff": rank_max,
        "rank_step": rank_step, "rank_waves": rank_waves,
        "waves": 1000, "difficulty_factors": [30, 40],
    })
    difficulty = "难度固定的" if rank_min == rank_max else "难度按档逐步提高的"
    assert f"{difficulty}{total_waves}波敌人" in text
    assert f"每波刷新{wave_size}名敌人" in text
    assert resources in text
    assert "系统自动选择最适合的角色进行1v1配对" in text
    assert "每名角色和敌人每波至多参战一次" in text
    assert "每场战斗的分数判定以胜负为主，败场只能拿到较低分数，未配对的敌人不记分" in text
    assert "单场战斗的分数汇总为该波次的分数" in text
    assert "所有波次的分数按难度加权汇总为rank_score" in text
    assert "1000波" not in text
    assert "987654321" not in text


def test_advanced_evaluation_notice_uses_complete_rank_wave_count():
    tools = GuildManagerTools.from_data_dir(Path(__file__).resolve().parents[1] / "data/presets/advanced")

    text = build_system_prompt(scoring_rules=asdict(tools.definition.scoring))

    assert "难度按档逐步提高的3000波敌人" in text
    assert "每波刷新6名敌人，双方以满HP/MP独立开战" in text


def test_endgame_prompts_focus_on_plan_preview_without_repeating_static_rules():
    tools = GuildManagerTools.from_data_dir(Path(__file__).resolve().parents[1] / "data/presets/advanced")
    observation = tools.start_session("battle-rule-text")["observation"]
    early = build_turn_prompt({**observation, "turn": 30}, endgame_start_turn=31)
    assert "preview_team_power" not in early
    late = build_turn_prompt({**observation, "turn": 31}, endgame_start_turn=31)
    for text in (late, build_endgame_system_prompt(31, 35)):
        assert "终局评测：" not in text
        assert "operations=[]" in text
        assert "相对当前真实状态" in text
        assert "execute_plan" in text
        assert "plan_id" in text


def test_memo_instructions_place_writing_before_end_turn():
    from guild_manager_bench.bench.llm.harness import TurnToolHarness

    tools = GuildManagerTools.from_data_dir(Path(__file__).resolve().parents[1] / "data/presets/default")
    observation = tools.start_session("memo-instructions")["observation"]
    schema = next(s for s in TurnToolHarness(tools, "memo-instructions", max_tool_calls=20).tool_schemas() if s["name"] == "write_memo")
    assert "各回合独立对话" in schema["description"]
    assert "回合末、调用 end_turn 前汇总写入" in schema["description"]
    assert "下一回合初展示一次后清空" in schema["description"]
    assert "准备完成后调用 end_turn 提交讨伐并结束本轮交互" in build_system_prompt()
    assert "end_turn 前用 write_memo 记录" in build_turn_prompt(observation)
