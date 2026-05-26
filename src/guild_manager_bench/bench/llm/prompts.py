from __future__ import annotations

from typing import Any, Mapping

from guild_manager_bench.bench.llm.formatting import skill_summary


DEFAULT_OBJECTIVE = "最大化本局最终表现：尽量赢得战斗、提升队伍、积累有价值资源。"


def build_turn_prompt(
    observation: Mapping[str, Any],
    *,
    objective: str = DEFAULT_OBJECTIVE,
    max_tool_calls: int,
    previous_turn_event: Mapping[str, Any] | None = None,
) -> str:
    """构造单个游戏回合开始时给 LLM 的提示词。"""

    return "\n".join(
        [
            "你正在进行 Guild Manager Bench。",
            f"目标：{objective}",
            "",
            "流程：你可以先调用工具查询完整状态并执行准备操作；本回合最后必须成功调用 end_turn。",
            f"本回合最多允许 {max_tool_calls} 次非 end_turn 工具调用；查询和非法调用也会消耗预算。",
            "预算耗尽后只能调用 end_turn。工具结果是紧凑文本，会包含 OK/FAIL 和 budget 行。",
            "如果工具返回 FAIL，请阅读失败原因并修正后继续。",
            "动作工具只返回变更摘要，不会自动返回完整状态；需要完整状态时请主动调用 get_observation。",
            "",
            _state_summary(observation),
            "",
            _previous_turn_summary(previous_turn_event),
        ]
    ).strip()


def _state_summary(observation: Mapping[str, Any]) -> str:
    adventurers = observation.get("adventurers", ())
    monsters = observation.get("monsters", ())
    scoring = observation.get("scoring", {})
    if not isinstance(scoring, Mapping):
        scoring = {}
    lines = [
        f"当前回合：{observation['turn']}/{observation['max_turns']}",
        f"session_id：{observation['session_id']}",
        f"随机种子：游戏 {observation.get('seed')}，评分 {scoring.get('seed')}",
        f"资源：金币 {observation['gold']}，经验池 {observation['experience_pool']}，材料 {dict(observation['materials'])}",
        "冒险者：",
    ]
    for adventurer in adventurers:
        resources = adventurer["resources"]
        stats = adventurer["effective_stats"]
        equipment = [
            item["instance_id"]
            for item in adventurer["equipment"]
        ]
        lines.append(
            "- "
            f"{adventurer['adventurer_id']} {adventurer['name']} "
            f"Lv{adventurer['level']} "
            f"HP {resources['current_hp']}/{stats['hp']} "
            f"MP {resources['current_mp']}/{stats['mp']} "
            f"ATK {stats['attack']} DEF {stats['defense']} SPD {stats['speed']} "
            f"装备 {equipment or '无'} "
            f"技能 {skill_summary(adventurer.get('skills'))}"
        )

    lines.append("当前怪物：")
    for monster in monsters:
        stats = monster["stats"]
        reward = monster["reward"]
        lines.append(
            "- "
            f"{monster['monster_id']} {monster['name']} "
            f"HP {stats['hp']} ATK {stats['attack']} DEF {stats['defense']} SPD {stats['speed']} "
            f"技能 {skill_summary(monster.get('skills'))} "
            f"奖励 gold={reward['gold']} exp={reward['experience']} materials={dict(reward['materials'])}"
        )

    return "\n".join(lines)


def _previous_turn_summary(previous_turn_event: Mapping[str, Any] | None) -> str:
    if previous_turn_event is None:
        return "上一回合：无，这是本局第一个回合。"

    payload = previous_turn_event.get("payload", previous_turn_event)
    lines = [f"上一回合：{payload['summary']}"]
    for battle in payload.get("battles", ()):
        result = "胜" if battle["won"] else "负"
        reward = battle["reward"]
        lines.append(
            "- "
            f"{battle['adventurer_id']} vs {battle['monster_id']}：{result}，"
            f"奖励 gold={reward['gold']} exp={reward['experience']} materials={dict(reward['materials'])}"
        )
    return "\n".join(lines)
