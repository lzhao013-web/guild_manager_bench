"""战报专用事件投影。曲线仍使用原来的四类里程碑，不持久化派生数据。"""
from __future__ import annotations

from math import isfinite
from typing import Any

from guild_manager_bench.api.curve_milestones import EXPANSIONS, _succeeded, extract_milestones


_MUTATIONS = {"recruit_adventurer", "dismiss_adventurer", "allocate_experience", "purchase_upgrade",
              "craft_equipment", "equip_item", "unequip_item", "configure_tactics",
              "exchange_materials", "refresh_monsters", "execute_plan", "end_turn"}
_KEY_SLOTS = {"main_hand": "主手", "two_hand": "双手武器", "ultimate": "印记"}
_BRANCHES = {"common": "公共发展", "economy": "经济建设", "martial": "武备", "arcane": "奥术",
             "survival": "生存", "magic": "魔法", "defense": "防御"}
CATEGORIES = {
    "first_seen": "阵容", "recruit": "阵容", "departure": "阵容", "full_roster": "阵容", "expansion": "阵容",
    "level_up": "成长", "level_9": "成长", "upgrade": "公会", "craft": "配装", "equipment": "配装",
    "tactics": "战术", "gold_spent": "资源", "experience_spent": "资源", "exchange": "资源", "refresh": "资源",
    "first_victory": "战斗", "first_defeat": "战斗", "boss_victory": "战斗", "challenge_victory": "战斗",
    "challenge_defeat": "战斗", "rank_rise": "评分", "rank_drop": "评分",
}


def numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _number(value: int | float) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _name(item: dict[str, Any], key: str = "name") -> str:
    value = item.get(key)
    return value if isinstance(value, str) and value else "未记录"


def _index(state: dict[str, Any], field: str, key: str) -> dict[str, dict[str, Any]]:
    return {item[key]: item for item in state.get(field, [])}


def extract_report_events(replay: dict[str, Any]) -> list[dict[str, Any]]:
    """只读已完成主线。差分只证明变化，不把缺快照期间的变化归到某个动作。"""
    turns = [turn for turn in replay["turns"] if turn.get("status") == "completed"]
    events = extract_milestones({"turns": turns}, {turn["turn"] for turn in turns}, include_evidence=True)
    by_turn = {turn["turn"]: turn for turn in turns}
    for event in events:
        members = []
        if event["kind"] == "level_9":
            members = [event["id"].split(":", 2)[2]]
        elif event["kind"] in ("boss_victory", "challenge_victory"):
            source = event["evidence"]
            battle = by_turn[event["turn"]]["steps"][source["step"] - 1]["result"]["turn_result"]["battles"][source["battle"] - 1]
            if battle.get("adventurer_id"):
                members = [battle["adventurer_id"]]
        event["members"] = members
    final = replay.get("final_observation") or {}
    final_equipment = {hero["adventurer_id"]: {
        slot["item"]["instance_id"] for slot in hero.get("equipment_slots") or []
        if slot.get("item") and slot["item"].get("instance_id")
    } for hero in final.get("adventurers", [])}
    final_items = {identity for items in final_equipment.values() for identity in items}
    milestones = {event["id"]: event for event in events}
    battle_marks = {(event["turn"], event["evidence"]["step"], event["evidence"]["battle"]): event
                    for event in events if event["kind"] in ("boss_victory", "challenge_victory")}
    for event in events:
        if event["kind"] == "level_9":
            event["final_member"] = event["members"][0] in final_equipment
    seen: set[str] = set()
    first_battles: set[bool] = set()
    filled: set[int | float] = set()

    def add(turn: int, kind: str, label: str, detail: str, evidence: dict[str, Any],
            members: list[str] | None = None, **extra: Any) -> None:
        events.append({"id": f"report-{len(events)}", "turn": turn, "kind": kind, "label": label,
                       "detail": detail, "evidence": evidence, "members": members or [], **extra})

    def transitions(number: int, before: dict[str, Any], after: dict[str, Any], source: dict[str, Any]) -> None:
        old = _index(before, "adventurers", "adventurer_id")
        new = _index(after, "adventurers", "adventurer_id")
        if "adventurers" in before and "adventurers" in after:
            for identity, hero in new.items():
                name = _name(hero)
                previous = old.get(identity)
                seen.add(identity)
                if previous is None:
                    add(number, "recruit", f"{name}加入", f"确认快照中 Lv{hero['level']}" if numeric(hero.get("level")) else "快照等级未记录", source, [identity])
                else:
                    level, current = previous.get("level"), hero.get("level")
                    if numeric(level) and numeric(current) and level < current and not level < 9 <= current:
                        add(number, "level_up", f"{name}升级", f"Lv{level} → Lv{current}", source, [identity], level=current)
                    a, b = previous.get("tactics"), hero.get("tactics")
                    if a is not None and b is not None and any(
                        key in a and key in b and a[key] != b[key] for key in ("skill_priority", "disabled_skills")
                    ):
                        skills = {skill["skill_id"]: _name(skill) for skill in
                                  [*hero.get("skills", []), *b.get("available_active_skills", [])]}
                        def describe(key: str, empty: str) -> str:
                            values = b.get(key)
                            return "未记录" if values is None else " → ".join(skills.get(value, value) for value in values) or empty
                        add(number, "tactics", f"{name}战术调整",
                            f"优先：{describe('skill_priority', '默认顺序')}。停用：{describe('disabled_skills', '无')}", source, [identity])
                if (previous is None or "equipment_slots" in previous) and "equipment_slots" in hero:
                    old_slots = _index(previous or {}, "equipment_slots", "slot")
                    changes, retained_items = [], []
                    for slot in hero["equipment_slots"]:
                        item = slot.get("item")
                        former = old_slots.get(slot["slot"], {}).get("item")
                        if item == former:
                            continue
                        # 按实例辨识穿戴变更，不把装备属性变化当作换装。
                        if item and former and item.get("instance_id") == former.get("instance_id"):
                            continue
                        if item:
                            changes.append(f"穿戴{_name(item)}")
                            if item.get("instance_id") in final_equipment.get(identity, ()):
                                retained_items.append({"slot": slot["slot"], "instance_id": item["instance_id"], "name": _name(item)})
                        elif former:
                            changes.append(f"卸下{_name(former)}")
                    if changes:
                        add(number, "equipment", f"{name}配装", "、".join(changes), source, [identity],
                            final_equipment=bool(retained_items), final_items=retained_items, member_name=name)
            for identity, hero in old.items():
                if identity not in new:
                    add(number, "departure", f"{_name(hero)}离队", "不再出现在队伍快照中。", source, [identity])
            limit = after.get("party_size_limit")
            if numeric(limit) and limit not in filled and len(new) == limit and len(old) < limit:
                filled.add(limit)
                add(number, "full_roster", f"首次达成 {_number(limit)} 人满编", f"首次在已保存快照中确认 {len(new)}/{limit} 人。", source)
        old_upgrades = _index(before, "global_upgrades", "upgrade_id")
        expansions = [item for item in after.get("global_upgrades", []) if item["upgrade_id"] in EXPANSIONS
                      and item.get("unlocked") is True and old_upgrades.get(item["upgrade_id"], {}).get("unlocked") is False]
        old_limit, new_limit = before.get("party_size_limit"), after.get("party_size_limit")
        # 同批多次扩编的总变化不能分别归给其中一次，保留各自已确认的解锁标题。
        if len(expansions) == 1 and numeric(old_limit) and numeric(new_limit) and new_limit > old_limit:
            item = expansions[0]
            event = milestones.get(f'T{number}:expansion:{item["upgrade_id"]}')
            if event is not None:
                name = _name(item) if item.get("name") else EXPANSIONS[item["upgrade_id"]]
                event.update(label=f"编队容量 {_number(old_limit)} → {_number(new_limit)} 人：{name}",
                             detail=f"解锁{name}，扩编后的容量来自已保存快照。", evidence=source)
        for item in after.get("global_upgrades", []):
            identity = item["upgrade_id"]
            if identity not in EXPANSIONS and item.get("unlocked") is True and old_upgrades.get(identity, {}).get("unlocked") is False:
                branch = item.get("branch")
                tier = item.get("tier")
                description = _BRANCHES.get(branch, branch) if isinstance(branch, str) else ""
                if numeric(tier):
                    description += f"第 {_number(tier)} 阶"
                exclusive = item.get("exclusive_upgrade_ids") or []
                label = ("选择互斥天赋：" if exclusive else "解锁") + _name(item)
                if description:
                    label += f"（{description}）"
                detail = "公会升级已解锁。"
                if exclusive:
                    choices = [_name(old_upgrades[key]) if key in old_upgrades else key for key in exclusive]
                    detail = "与" + "、".join(choices) + "互斥，按存档记录的选择关系展示。"
                add(number, "upgrade", label, detail, source, tier=tier if numeric(tier) else 0,
                    exclusive=bool(exclusive), upgrade_id=identity)
        if "equipment_inventory" in before and "equipment_inventory" in after:
            inventory = _index(before, "equipment_inventory", "instance_id")
            for item in after["equipment_inventory"]:
                if item["instance_id"] not in inventory:
                    label = "制作装备" if source.get("tool") in ("craft_equipment", "execute_plan") and source["source"] == "observation_after" else "获得装备"
                    add(number, "craft", label, _name(item), source, final_equipment=item["instance_id"] in final_items)
        # 只有紧邻成功准备动作的快照能给出单次提交的净投入。下一开局含战斗收入，不能当作支出。
        if source["source"] == "observation_after" and source.get("tool") != "end_turn":
            for field, kind, label in (("gold", "gold_spent", "金币投入"), ("experience_pool", "experience_spent", "经验投入")):
                a, b = before.get(field), after.get(field)
                if numeric(a) and numeric(b) and a > b:
                    add(number, kind, label, f"本次提交净减少 {a - b:,}。整批方案按一次提交统计。", source, amount=a - b)
        for field, key, kind, label in (("material_exchange", "used_points", "exchange", "兑换材料"),
                                        ("monster_refresh", "refreshes_used", "refresh", "付费刷新怪物")):
            a, b = (before.get(field) or {}).get(key), (after.get(field) or {}).get(key)
            if numeric(a) and numeric(b) and b > a:
                detail = f"使用 {b - a} 点兑换额度" if kind == "exchange" else f"刷新次数增加 {b - a}"
                add(number, kind, label, detail, source)

    for turn in turns:
        number = turn["turn"]
        state = turn.get("observation_before") or {}
        if numeric(state.get("party_size_limit")) and len(state.get("adventurers", [])) == state["party_size_limit"]:
            filled.add(state["party_size_limit"])
        for hero in state.get("adventurers", []):
            identity = hero["adventurer_id"]
            if identity not in seen:
                seen.add(identity)
                add(number, "first_seen", f"{_name(hero)}已在队伍中", "首次可见于开局快照，招募时间未记录。",
                    {"source": "opening", "turn": number}, [identity])
        monsters = _index(state, "monsters", "monster_id")
        gap = False
        for step_number, step in enumerate(turn.get("steps", []), 1):
            name = step.get("name")
            result = step.get("result") or {}
            if (step.get("type") != "tool_result" or name not in _MUTATIONS or not _succeeded(step)
                    or result.get("simulation_only") is True or result.get("already_executed") is True
                    or name == "refresh_monsters" and step.get("arguments", {}).get("preview") is True):
                continue
            source = {"step": step_number, "tool": name, "source": "result"}
            if name == "end_turn":
                heroes = _index(state, "adventurers", "adventurer_id")
                for index, battle in enumerate(result.get("turn_result", {}).get("battles", []), 1):
                    won = battle.get("won")
                    if not isinstance(won, bool):
                        continue
                    enemy = monsters.get(battle.get("monster_id"), {})
                    identity = battle.get("adventurer_id")
                    actor = _name(battle, "adventurer_name") if battle.get("adventurer_name") else _name(heroes.get(identity, {}))
                    enemy_name = _name(enemy) if enemy.get("name") else _name(battle, "monster_name")
                    mark = battle_marks.get((number, step_number, index))
                    if mark is not None:
                        label = f"击败首领：{enemy_name}" if mark["kind"] == "boss_victory" else f"通关挑战：{enemy_name}"
                        mark.update(label=label, detail=f"{actor}战胜{enemy_name}。", enemy_name=enemy_name,
                                    enemy_key=enemy.get("archetype_id") or battle["monster_id"])
                    kind = "first_victory" if won else "first_defeat"
                    label = "首次胜利" if won else "首次战败"
                    if won not in first_battles:
                        first_battles.add(won)
                        # 首次胜利已由首领或挑战节点覆盖时，不重复列出同一场胜利。
                        if not won or enemy.get("tier") not in ("boss", "challenge"):
                            add(number, kind, label, f"{actor}对阵{enemy_name}。首次按已保存结算记录判断。", {**source, "battle": index}, [identity] if identity else [])
                    elif not won and enemy.get("tier") == "challenge":
                        add(number, "challenge_defeat", "挑战未通关", f"{actor}对阵{enemy_name}", {**source, "battle": index}, [identity] if identity else [])
            after = step.get("observation_after")
            if isinstance(after, dict):
                if gap:
                    # 原曲线节点范围不变，战报补充缺快照的证据限制。
                    for event in events:
                        evidence = event["evidence"]
                        if event["turn"] == number and evidence.get("step") == step_number and evidence["source"] == "observation_after":
                            event["evidence"] = {**evidence, "source": "snapshot_range"}
                transitions(number, state, after, {**source, "source": "snapshot_range" if gap else "observation_after"})
                state = after
                monsters.update(_index(state, "monsters", "monster_id"))
                gap = False
            else:
                gap = True
        opening = by_turn.get(number + 1, {}).get("observation_before")
        if isinstance(opening, dict):
            transitions(number, state, opening, {"source": "next_opening", "turn": number + 1})
    # 跨版本运行无法确认两端评分条件一致，不把差值包装成成长或回落。
    if (replay.get("data") or {}).get("protocol_status") != "mixed":
        changes = []
        for previous, current in zip(turns, turns[1:]):
            before, after = previous.get("rank_score"), current.get("rank_score")
            if current["turn"] == previous["turn"] + 1 and numeric(before) and numeric(after):
                delta = after - before
                if numeric(delta) and _number(abs(delta)) != "0":
                    changes.append((previous["turn"], current["turn"], before, after, delta))
        for positive, kind, label in ((True, "rank_rise", "Rank 最大上升"), (False, "rank_drop", "Rank 最大回落")):
            candidates = [change for change in changes if (change[4] > 0) == positive]
            if candidates:
                start, end, before, after, delta = max(candidates, key=lambda change: abs(change[4]))
                signed = ("+" if delta > 0 else "-") + _number(abs(delta))
                add(end, kind, f"{label}：{signed}",
                    f"T{start}：{_number(before)} → T{end}：{_number(after)}。只比较连续已记录的正式 Rank，不作操作因果解释。",
                    {"source": "rank_score", "from_turn": start, "turn": end})
    return sorted(events, key=lambda event: (event["turn"],
                  0 if event["evidence"]["source"] == "opening" else event["evidence"].get("step", float("inf"))))


def select_highlights(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按事实条件筛选，不设类别配额或条数上限。只合并同一成果的重复记录。"""
    selected: dict[str, dict[str, Any]] = {}
    seen_upgrades, challenges = set(), set()
    boss_seen = False
    equipment = {}
    for event in events:
        kind = event["kind"]
        keep = kind in ("expansion", "full_roster", "rank_rise", "rank_drop")
        if kind == "level_9":
            keep = event["final_member"]
        elif kind == "upgrade":
            identity = event["upgrade_id"]
            keep = (event["tier"] >= 3 or event["exclusive"]) and identity not in seen_upgrades
            seen_upgrades.add(identity)
        elif kind == "boss_victory" and not boss_seen:
            boss_seen = True
            selected[event["id"]] = {**event, "label": f'首次击败首领：{event["enemy_name"]}',
                                       "detail": event["detail"] + "首次按已保存结算记录判断。"}
        elif kind == "challenge_victory":
            keep = event["enemy_key"] not in challenges
            challenges.add(event["enemy_key"])
        elif kind == "equipment":
            for item in event["final_items"]:
                if item["slot"] in _KEY_SLOTS:
                    # 同一终局装备反复穿脱，只保留最终持有人最后一次确认穿戴的位置。
                    equipment[(event["members"][0], item["slot"], item["instance_id"])] = (event, item)
        if keep:
            selected[event["id"]] = event
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event, item in equipment.values():
        grouped.setdefault(event["id"], []).append(item)
    for event in events:
        items = grouped.get(event["id"])
        if items:
            names = "、".join(f'{_KEY_SLOTS[item["slot"]]}「{item["name"]}」' for item in items)
            verb = "装上" if event["evidence"]["source"] == "observation_after" else "已装备"
            selected[event["id"]] = {**event, "label": f'{event["member_name"]}{verb}终局{names}',
                                       "detail": f"{names}在终局仍由该成员穿戴。"}
    return [selected[event["id"]] for event in events if event["id"] in selected]
