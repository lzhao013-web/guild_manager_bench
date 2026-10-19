"""按需读取已有战斗事件，不重新运行战斗。"""

from collections import Counter
from typing import Any, Mapping, Sequence


BATTLE_LOG_SCHEMA = {
    "name": "get_battle_log",
    "description": (
        "查询本回合已执行的 preview_battle（source=preview）或上一回合实际讨伐（source=actual）的战斗记录，仅消耗普通工具调用预算。"
        "用法：1) 不传 battle_index 时返回该来源的战斗目录；"
        "2) 传入 battle_index 且 detail=summary（默认）时返回整场伤害/治疗/技能释放统计；"
        "3) 传入 battle_index 且 detail=events 时分页返回逐行动事件（支持用 action_start/action_end 按双方累计行动序号过滤，"
        "用 offset/limit 翻页；即时技能附带的普攻合并在该技能事件中）。"
    ),
    "parameters": {
        "type": "object",
        "required": ["source"],
        "properties": {
            "source": {"type": "string", "enum": ["preview", "actual"]},
            "battle_index": {"type": "integer", "minimum": 1,
                             "description": "指定来源内的战斗目录序号，不是冒险者或怪物编号。"},
            "detail": {"type": "string", "enum": ["summary", "events"], "default": "summary"},
            "action_start": {"type": "integer", "minimum": 1, "maximum": 1000},
            "action_end": {"type": "integer", "minimum": 1, "maximum": 1000},
            "offset": {"type": "integer", "minimum": 0, "default": 0,
                       "description": "筛选后的事件偏移量。翻页时保持其他筛选参数不变。"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        },
        "additionalProperties": False,
    },
}


def query_battle_log(
    records: Sequence[Mapping[str, Any]], *, source: str, turn: int,
    battle_index: int | None = None, detail: str = "summary",
    action_start: int | None = None, action_end: int | None = None,
    offset: int = 0, limit: int = 20,
) -> dict[str, Any]:
    if source not in ("preview", "actual"):
        raise ValueError("source 必须是 preview 或 actual")
    if detail not in ("summary", "events"):
        raise ValueError("detail 必须是 summary 或 events")
    _integer(offset, "offset", 0)
    _integer(limit, "limit", 1, 50)
    for name, value in (("action_start", action_start), ("action_end", action_end)):
        if value is not None:
            _integer(value, name, 1, 1000)
    if action_start is not None and action_end is not None and action_start > action_end:
        raise ValueError("action_start 不能大于 action_end")
    if detail != "events" and (action_start is not None or action_end is not None or offset != 0 or limit != 20):
        raise ValueError("行动区间和分页参数仅用于 detail=events")
    result: dict[str, Any] = {
        "ok": True, "source": source, "turn": turn, "detail": detail,
        "record_count": len(records),
    }
    if battle_index is None:
        if detail != "summary":
            raise ValueError("查看事件必须指定 battle_index，请先查询战斗目录")
        result["battles"] = [_summary(record, i) for i, record in enumerate(records, 1)]
        return result

    _integer(battle_index, "battle_index", 1)
    if battle_index > len(records):
        raise ValueError("battle_index 不在该来源的可查询目录中")
    record = records[battle_index - 1]
    result["battle"] = _summary(record, battle_index)
    events = record["combat"]["events"]
    if detail == "summary":
        result["statistics"] = _statistics(events)
    else:
        selected = [event for event in events
                    if (action_start is None or event["action_index"] >= action_start)
                    and (action_end is None or event["action_index"] <= action_end)]
        page = selected[offset:offset + limit]
        result.update({
            "events": [_visible_event(event) for event in page],
            "total_events": len(events), "matched_events": len(selected),
            "offset": offset, "limit": limit,
            "next_offset": offset + len(page) if offset + len(page) < len(selected) else None,
        })
    return result


def _integer(value: Any, name: str, minimum: int, maximum: int | None = None) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} 必须是大于等于 {minimum} 的整数")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} 不能超过 {maximum}")


def _summary(record: Mapping[str, Any], index: int) -> dict[str, Any]:
    combat = record["combat"]
    return {
        "battle_index": index,
        "adventurer_name": record["adventurer_name"], "monster_name": record["monster_name"],
        "outcome": combat["outcome"], "reason": combat["reason"],
        "actions_taken": combat["actions_taken"], "time_elapsed": combat["time_elapsed"],
        "event_count": len(combat["events"]),
    }


def _visible_event(event: Mapping[str, Any]) -> dict[str, Any]:
    # 使用战斗内左右侧，不把历史对象编号混入当前回合的可操作编号。
    return {key: value for key, value in event.items() if key not in ("actor_id", "target_id")}


def _statistics(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result = {}
    for side in ("left", "right"):
        own = [event for event in events if event["actor_side"] == side]
        skill_uses = Counter(event["skill_id"] for event in own if event["action_type"] == "skill")
        result[side] = {
            "basic_attack_events": sum(event["action_type"] == "basic_attack" for event in own),
            "skill_uses": dict(skill_uses),
            "direct_damage": sum(event["damage"] for event in own if event["action_type"] != "status"),
            "status_damage_taken": sum(event["damage"] for event in own if event["action_type"] == "status"),
            "healing_received": sum(event["healing"] for event in events if event["healing_target_side"] == side),
        }
    return result
