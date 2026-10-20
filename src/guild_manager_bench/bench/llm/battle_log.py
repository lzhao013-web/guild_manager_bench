"""按战斗编号读取已有事件，不重新运行战斗。"""

from collections import Counter
from typing import Any, Mapping, Sequence


EVENTS_PER_PAGE = 20

BATTLE_LOG_SCHEMA = {
    "name": "get_battle_log",
    "description": "按 battle_id 查看本回合预览或上回合实战记录（只读，代表当时快照）。默认 summary 返回伤害/治疗/技能统计，events 分页返回逐行动事件。",
    "parameters": {
        "type": "object",
        "required": ["battle_id"],
        "properties": {
            "battle_id": {"type": "string", "minLength": 1,
                          "description": "预览或回合结算返回的 battle_id（非角色或怪物编号）。"},
            "detail": {"type": "string", "enum": ["summary", "events"], "default": "summary",
                       "description": "summary 返回整场统计，events 返回逐行动事件（每页20条）。"},
            "cursor": {"type": "integer", "minimum": 0, "default": 0,
                       "description": "events 分页游标，续查时传入上一页返回的 next_cursor。"},
        },
        "additionalProperties": False,
    },
}


def battle_log_id(source: str, turn: int, index: int) -> str:
    return f"{source}:T{turn}:{index}"


def query_battle_log(
    records: Mapping[str, Mapping[str, Any]], *, battle_id: str,
    detail: str = "summary", cursor: int = 0,
) -> dict[str, Any]:
    if not isinstance(battle_id, str) or not battle_id:
        raise ValueError("battle_id 必须是战斗结果中返回的非空字符串编号")
    if detail not in ("summary", "events"):
        raise ValueError("detail 必须是 summary 或 events")
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
        raise ValueError("cursor 必须是大于等于 0 的整数")
    if detail != "events" and cursor != 0:
        raise ValueError("cursor 仅用于 detail=events")
    if battle_id not in records:
        raise ValueError("战斗编号不存在或已过期，仅可查询本回合预览与上一回合实战")
    record = records[battle_id]
    result: dict[str, Any] = {
        "ok": True, "source": record["source"], "turn": record["turn"], "detail": detail,
        "battle": _summary(record, battle_id),
    }
    events = record["combat"]["events"]
    if detail == "summary":
        result["statistics"] = _statistics(events)
    else:
        page = events[cursor:cursor + EVENTS_PER_PAGE]
        result.update({
            "events": [_visible_event(event) for event in page],
            "total_events": len(events), "cursor": cursor,
            "next_cursor": cursor + len(page) if cursor + len(page) < len(events) else None,
        })
    return result


def _summary(record: Mapping[str, Any], battle_id: str) -> dict[str, Any]:
    combat = record["combat"]
    return {
        "battle_id": battle_id,
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
