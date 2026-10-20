"""战斗记录和未来刷新查询的有界、模型可见文本。"""

from typing import Any, Mapping

from guild_manager_bench.bench.llm.formatting import skill_summary_lines
from guild_manager_bench.game.state import MATERIAL_NAMES


_SIDES = {"left": "冒险者", "right": "怪物", None: "未单独记录"}
_OUTCOMES = {"left_win": "冒险者胜", "right_win": "怪物胜", "draw": "平局"}
_STATS = {"hp": "HP", "mp": "MP", "attack": "攻击", "defense": "防御", "speed": "速度",
          "recovery": "战后回血", "mp_recovery": "战后回魔"}


def battle_log_lines(result: Mapping[str, Any]) -> list[str]:
    source = "当轮预览记录（未实际执行）" if result["source"] == "preview" else "上轮真实战斗记录"
    lines = [f"来源：{source}，T{result['turn']}。记录反映当时状态，不代表当前队伍。",
             "战斗内 left=冒险者，right=怪物。battle_id 仅用于查日志，不是可操作对象编号。"]

    lines.append(_battle_summary(result["battle"]))
    lines.append("伤害为日志记录值，可能包含溢出伤害。即时技能附带普攻合并在该技能事件内。")
    if "statistics" in result:
        for side, stats in result["statistics"].items():
            uses = ", ".join(f"{skill}×{count}" for skill, count in stats["skill_uses"].items()) or "无"
            lines.append(
                f"{_SIDES[side]}：独立普攻事件 {stats['basic_attack_events']}，技能释放 {uses}，"
                f"直接伤害 {stats['direct_damage']}，自身状态伤害 {stats['status_damage_taken']}，"
                f"收到的实际治疗 {stats['healing_received']}。"
            )
        lines.append("设 detail=events 查看逐行动事件。日志不包含逐动作MP余额或技能跳过原因。")
        return lines

    lines.append(f"整场 {result['total_events']} 条事件，本页 {len(result['events'])} 条，cursor={result['cursor']}。")
    for event in result["events"]:
        action_type = event["action_type"]
        if action_type == "skill":
            action = f"技能 {event['skill_name']} [{event['skill_id']}]"
        elif action_type == "status":
            action = f"状态结算 {event['status_name']}"
        else:
            action = "普攻"
        text = (
            f"- 行动 {event['action_index']}，时间 {event['time_elapsed']}："
            f"{_SIDES[event['actor_side']]} {action}，"
            f"目标 {_SIDES[event['target_side']]}，伤害 {event['damage']}，目标HP {event['target_hp']}"
        )
        if event["healing"]:
            text += (f"，治疗 {_SIDES[event['healing_target_side']]} {event['healing']}，"
                     f"治疗后HP {event['healing_target_hp']}")
        lines.append(text)
    next_cursor = result["next_cursor"]
    lines.append("已返回全部事件。" if next_cursor is None else f"还有未返回的事件。保持 battle_id 和 detail 不变，下一页 cursor={next_cursor}。")
    return lines


def forecast_lines(result: Mapping[str, Any]) -> list[str]:
    lines = [
        f"未来刷新查询：当前 T{result['current_turn']}，查询 T{result['start_turn']}—T{result['end_turn']}。",
        "仅查询经营刷新，真实状态未改变。future: 引用不能用于当前操作或方案试算。",
        f"筛选命中 {result['total_matches']} 条，本页 {len(result['items'])} 条，offset={result['offset']}。",
    ]
    if result["kind"] == "monsters":
        lines.append("奖励为未计经营天赋加成的基础奖励。是否获得奖励取决于未来实际讨伐结果。")
    for item in result["items"]:
        prefix = f"- {item['forecast_ref']} {item['name']}"
        if result["kind"] == "monsters":
            tier = {"normal": "普通", "elite": "精英", "boss": "首领", "challenge": "固定挑战"}[item["tier"]]
            reward = item["base_reward"]
            materials = ", ".join(f"{MATERIAL_NAMES.get(key, key)} {value}" for key, value in reward["materials"].items()) or "无"
            lines.append(f"{prefix}，{tier}，原型 {item['archetype_id']}，基础奖励：金币 {reward['gold']}，经验 {reward['experience']}，材料 {materials}")
            if "availability_condition" in item:
                lines.append(f"  存在条件：{item['availability_condition']}。")
            if "stats" in item:
                lines.append(f"  属性：{_stats_text(item['stats'])}")
        else:
            lines.append(f"{prefix}，职业 {item['template_id']}，招募金币 {item['recruit_gold']}")
            if "base_stats" in item:
                lines.append(f"  初始属性：{_stats_text(item['base_stats'])}")
                lines.append(f"  每级成长：{_stats_text(item['stat_growth_per_level'])}")
        if "skills" in item:
            lines.extend(f"  技能：{text}" for text in skill_summary_lines(item["skills"]))
        for unlock in item.get("level_skill_unlocks", ()):
            lines.extend(f"  Lv{unlock['level']} 解锁：{text}" for text in skill_summary_lines(unlock["skills"]))
    lines.append(_next_page(result["next_offset"]))
    if result["detail"] == "summary" and result["items"]:
        lines.append("详情：指定条目对应的 start_turn、entry_index，并设 detail=full。entry_index 是该回合原始列表序号。")
    return lines


def _battle_summary(battle: Mapping[str, Any]) -> str:
    return (f"- battle_id={battle['battle_id']} {battle['adventurer_name']} vs {battle['monster_name']}："
            f"{_OUTCOMES[battle['outcome']]}，原因 {battle['reason']}，"
            f"行动数 {battle['actions_taken']}，耗时 {battle['time_elapsed']}，事件数 {battle['event_count']}")


def _stats_text(stats: Mapping[str, Any]) -> str:
    return "，".join(f"{label} {stats[key]}" for key, label in _STATS.items())


def _next_page(offset: int | None) -> str:
    return "已返回全部匹配结果。" if offset is None else f"还有未返回的结果。保持筛选条件不变，下一页 offset={offset}。"
