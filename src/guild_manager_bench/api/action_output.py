"""从已记录返回及快照提取变化对象和明确操作目标，不执行游戏逻辑。"""
from __future__ import annotations

from typing import Any


FIELDS = {
    "adventurers": ("adventurer_id", "name", "template_id", "level", "experience", "resources", "effective_stats"),
    "equipment_inventory": ("instance_id", "name", "slot", "stats", "equipped_by", "allowed_class_names"),
    "global_upgrades": ("upgrade_id", "name", "unlocked", "description", "effects", "stats", "party_size_bonus", "tier", "branch"),
}
IDENTITIES = {"adventurers": "adventurer_id", "equipment_inventory": "instance_id", "global_upgrades": "upgrade_id"}


def _entity(collection: str, item: dict[str, Any]) -> dict[str, Any]:
    result = {key: item[key] for key in FIELDS[collection] if key in item}
    if "skills" in item:
        result["skills"] = [{key: skill[key] for key in ("skill_id", "name", "description") if key in skill} for skill in item["skills"]]
    if collection == "adventurers" and "tactics" in item:
        tactics = item["tactics"]
        result["tactics"] = {key: tactics[key] for key in ("skill_priority", "disabled_skills") if key in tactics}
        if "available_active_skills" in tactics:
            result["tactics"]["available_active_skills"] = [
                {key: skill[key] for key in ("skill_id", "name") if key in skill}
                for skill in tactics["available_active_skills"]
            ]
    return result


def action_output(result: dict[str, Any], before: dict[str, Any], after: dict[str, Any], *, members: tuple[str, ...] = ()) -> dict[str, Any]:
    """不从请求参数推断产物。没有完整前后集合时，仅使用显式返回。"""
    if result.get("ok") is not True or result.get("simulation_only") is True:
        return {}
    output: dict[str, Any] = {}
    for collection, identity in IDENTITIES.items():
        if collection not in before or collection not in after:
            continue
        old = {item[identity]: item for item in before[collection]}
        changed = []
        for item in after[collection]:
            previous = old.get(item[identity])
            if collection == "global_upgrades":
                # 动态价格等变化不是解锁成果。
                include = previous is not None and item.get("unlocked") is True and previous.get("unlocked") is False
            else:
                include = previous is None or _entity(collection, previous) != _entity(collection, item)
            if include or collection == "adventurers" and item[identity] in members:
                changed.append(_entity(collection, item))
        if changed:
            output[collection] = changed
        if collection == "adventurers":
            current = {item[identity] for item in after[collection]}
            removed = [_entity(collection, item) for key, item in old.items() if key not in current]
            if removed:
                output["dismissed_adventurers"] = removed
    if "recruited_adventurer" in result and not output.get("adventurers"):
        output["adventurers"] = [_entity("adventurers", result["recruited_adventurer"])]
    return output
