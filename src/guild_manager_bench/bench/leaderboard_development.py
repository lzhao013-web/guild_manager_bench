"""从已记录的经营主线提取摘要，不重放动作、不估算假想收益。"""
from __future__ import annotations

import json
import re
from typing import Any


_WRITE_TOOLS = {
    "craft_equipment", "purchase_upgrade", "allocate_experience", "recruit_adventurer",
    "dismiss_adventurer", "equip_item", "unequip_item", "configure_tactics", "end_turn",
    "refresh_monsters",
}


def _outcome(step: dict) -> bool | None:
    content = step.get("content")
    if isinstance(content, str):
        prefix = re.match(r"^(成功|失败|错误)\s+" + re.escape(str(step.get("name", ""))) + r"[:：]", content.lstrip())
        if prefix:
            return prefix[1] == "成功"
    result = step.get("result")
    if isinstance(result, dict) and isinstance(result.get("ok"), bool):
        return result["ok"]
    if isinstance(step.get("ok"), bool):
        return step["ok"]
    content = step.get("content")
    if not isinstance(content, str):
        return None
    text = content.lstrip()
    if text.startswith(("OK ", "成功 ")):
        return True
    if text.startswith(("FAIL ", "失败 ", "ERROR ", "错误 ")):
        return False
    if text.startswith("{"):
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(value, dict) and isinstance(value.get("ok"), bool):
            return value["ok"]
    return None


def _costs(before: dict | None, after: dict | None) -> dict:
    result: dict[str, Any] = {"gold": None, "experience": None, "materials": None}
    if before is None or after is None:
        return result
    for output, source in (("gold", "gold"), ("experience", "experience_pool")):
        a, b = before.get(source), after.get(source)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a >= b:
            result[output] = a - b
    a, b = before.get("materials"), after.get("materials")
    if isinstance(a, dict) and isinstance(b, dict):
        result["materials"] = {key: value - b.get(key, 0) for key, value in a.items()
                               if value > b.get(key, 0)}
    return result


def _recorded_costs(step: dict, before: dict | None, after: dict | None, *, reliable: bool = True) -> dict:
    # 旧归档有调用ID复用导致的快照错配；官方正文的变化段仍记录实际支出。
    content = step.get("content")
    if isinstance(content, str) and "变化:" in content:
        changes = re.search(r"(?m)^变化:\s*\n((?:- [^\n]*(?:\n|$))*)", content)
        if changes:
            complete = re.search(r"(?m)^预算[:：]", content) is not None
            costs = {"gold": 0 if complete else None, "experience": 0 if complete else None,
                     "materials": {} if complete else None}
            for label, first, last in re.findall(r"(?m)^- ([^:\n]+):\s*(-?\d+)\s*->\s*(-?\d+)\s*$", changes[1]):
                spent = int(first) - int(last)
                if spent < 0:
                    continue
                if label in {"金币", "经验池"}:
                    costs["gold" if label == "金币" else "experience"] = spent
                elif re.fullmatch(r"[a-z][a-z0-9_]*", label):
                    if costs["materials"] is None:
                        costs["materials"] = {}
                    costs["materials"][label] = spent
            return costs
    return _costs(before, after) if reliable else {"gold": None, "experience": None, "materials": None}


def _purchase(step: dict, before: dict | None, after: dict | None) -> dict | None:
    content = step.get("content")
    if isinstance(content, str):
        name = re.search(r"(?m)^成功 purchase_upgrade[:：]购买全局加成 (.+)$", content)
        if name:
            return {"upgrade_id": None, "name": name[1]}
    if (before is not None and after is not None and isinstance(before.get("global_upgrades"), list)
            and isinstance(after.get("global_upgrades"), list)):
        previous = _unlocked(before)
        additions = [u for identity, u in _unlocked(after).items() if identity not in previous]
        if len(additions) == 1:
            return additions[0]
    return None


def _unlocked(observation: dict) -> dict[str, dict]:
    return {u["upgrade_id"]: u for u in observation.get("global_upgrades", []) if u.get("unlocked")}


def _seals(observation: dict) -> dict[str, dict]:
    return {item["instance_id"]: item for item in observation.get("equipment_inventory", [])
            if item.get("slot") == "ultimate"}


def development_summary(turns: list[dict], final: dict) -> dict:
    challenges: dict[str, dict] = {}
    total = None
    events = []
    refresh = {"previews": 0, "paid": 0, "failed": 0, "unconfirmed": 0,
               "known_gold_spent": 0, "unknown_cost_count": 0}
    purchase_calls = purchase_events = 0

    def observe(observation: dict | None) -> None:
        nonlocal total
        if observation is None:
            return
        monsters = [m for m in observation.get("monsters", []) if m.get("tier") == "challenge"]
        for monster in monsters:
            identity = monster["archetype_id"]
            entry = challenges.setdefault(identity, {"challenge_id": identity, "turn": None,
                                                     "adventurer_name": None, "materials": None})
            entry["name"] = monster["name"]
        turn, start, end = observation.get("turn"), observation.get("challenge_start_turn"), observation.get("max_turns")
        if (isinstance(observation.get("monsters"), list) and "defeated_challenge_ids" in observation
                and isinstance(turn, int) and isinstance(start, int) and isinstance(end, int) and start <= turn < end):
            total = len(monsters) + len(observation.get("defeated_challenge_ids", []))

    for turn in turns:
        if turn.get("status") != "completed":
            continue
        number = turn["turn"]
        before = turn.get("observation_before")
        observe(before)
        for step in turn.get("steps", []):
            if step.get("type") != "tool_result":
                continue
            name, status = step.get("name"), _outcome(step)
            after = step.get("observation_after")
            observe(after)
            arguments = step.get("arguments", {})
            preview = name == "preview_monster_refresh" or arguments.get("preview") is True
            header = str(step.get("content", "")).splitlines()[0] if step.get("content") else ""
            if name == "refresh_monsters" and header.startswith("成功 refresh_monsters"):
                if "预览" in header:
                    preview = True
                elif "付费刷新" in header:
                    preview = False
            if name in {"refresh_monsters", "preview_monster_refresh"}:
                if status is False:
                    refresh["failed"] += 1
                elif status is None:
                    refresh["unconfirmed"] += 1
                elif preview:
                    refresh["previews"] += 1
                else:
                    refresh["paid"] += 1
                    prior_count = before.get("monster_refresh", {}).get("refreshes_used") if before else None
                    after_count = after.get("monster_refresh", {}).get("refreshes_used") if after else None
                    same_turn = before is not None and after is not None and before.get("turn") == after.get("turn")
                    consistent = same_turn and isinstance(prior_count, int) and after_count == prior_count + 1
                    # 有明确余额下降的旧快照也可使用；相同快照不能证明免费刷新。
                    if same_turn and before.get("gold", 0) > after.get("gold", 0):
                        consistent = True
                    cost = _recorded_costs(step, before, after, reliable=consistent)["gold"]
                    if cost is None:
                        refresh["unknown_cost_count"] += 1
                    else:
                        refresh["known_gold_spent"] += cost
                    locked = arguments.get("locked_monster_ids")
                    lock_text = re.search(r"锁定(\d+)个普通目标", str(step.get("content", "")))
                    events.append({"type": "refresh", "turn": number, "gold": cost,
                                   "locked_count": int(lock_text[1]) if lock_text else len(locked) if isinstance(locked, list) else None})
            if status is True and name == "purchase_upgrade":
                purchase_calls += 1
                upgrade = _purchase(step, before, after)
                if upgrade is not None:
                    purchase_events += 1
                    events.append({"type": "upgrade", "turn": number, "upgrade_id": upgrade["upgrade_id"],
                                   "name": upgrade["name"], "costs": _recorded_costs(step, before, after)})
            if (status is True and name == "craft_equipment" and before is not None and after is not None
                    and isinstance(before.get("equipment_inventory"), list) and isinstance(after.get("equipment_inventory"), list)):
                previous = _seals(before)
                for identity, item in _seals(after).items():
                    if identity not in previous:
                        events.append({"type": "seal_craft", "turn": number, "name": item["name"],
                                       "instance_id": identity, "costs": _recorded_costs(step, before, after)})
            if (status is True and name == "equip_item" and before is not None and after is not None
                    and isinstance(before.get("adventurers"), list) and isinstance(after.get("adventurers"), list)):
                for actor in after.get("adventurers", []):
                    for slot in actor.get("equipment_slots", []):
                        item = slot.get("item")
                        if slot.get("slot") != "ultimate" or not item or before is None:
                            continue
                        prior = next((a for a in before.get("adventurers", [])
                                      if a["adventurer_id"] == actor["adventurer_id"]), None)
                        previous_id = next((s["item"]["instance_id"] for s in prior.get("equipment_slots", [])
                                            if s.get("slot") == "ultimate" and s.get("item")), None) if prior else None
                        if previous_id != item["instance_id"]:
                            events.append({"type": "seal_equip", "turn": number, "name": item["name"],
                                           "adventurer_name": actor["name"]})
            if status is True and name == "end_turn":
                result = step.get("result", {})
                battles = result.get("turn_result", {}).get("battles", [])
                for battle in battles:
                    monster_id = battle.get("monster_id", "")
                    if not battle.get("won") or not str(monster_id).startswith("challenge_"):
                        continue
                    identity = monster_id.removeprefix("challenge_")
                    entry = challenges.setdefault(identity, {"challenge_id": identity})
                    if entry.get("turn") is None:
                        entry.update(name=battle.get("monster_name"), turn=number,
                                     adventurer_name=battle.get("adventurer_name"),
                                     materials=battle.get("reward", {}).get("materials"))
                        events.append({"type": "challenge", **entry})
                if before is not None and after is not None:
                    for identity in set(after.get("defeated_challenge_ids", [])) - set(before.get("defeated_challenge_ids", [])):
                        entry = challenges.setdefault(identity, {"challenge_id": identity})
                        if entry.get("turn") is None:
                            entry["turn"] = number
                            events.append({"type": "challenge", **entry})
            if after is not None:
                before = after
            elif name in _WRITE_TOOLS and not preview and status is not False:
                # 缺失写入快照时，不能把之前的余额当作下一次操作的余额。
                before = None

    defeated = set(final["defeated_challenge_ids"])
    for identity in defeated:
        challenges.setdefault(identity, {"challenge_id": identity, "turn": None})
    for identity, entry in challenges.items():
        entry["defeated"] = identity in defeated
    recipes = {r["output_template_id"]: r for r in final.get("crafting_recipes", [])
               if r.get("output_slot") == "ultimate"}
    owners = {}
    inventory = _seals(final)
    for actor in final.get("adventurers", []):
        for slot in actor.get("equipment_slots", []):
            item = slot.get("item")
            if slot.get("slot") == "ultimate" and item:
                inventory[item["instance_id"]] = item
                owners[item["instance_id"]] = actor["name"]
    seals = [{"name": item["name"], "adventurer_name": owners.get(identity),
              "recipe_materials": recipes.get(item["template_id"], {}).get("material_costs")}
             for identity, item in inventory.items()]
    return {
        "challenges": {"defeated": len(defeated), "total": total,
                       "entries": sorted(challenges.values(), key=lambda entry: entry["challenge_id"])},
        "refresh": refresh,
        "seals": seals,
        "events": events,
        "unlocated_purchases": purchase_calls - purchase_events,
    }
