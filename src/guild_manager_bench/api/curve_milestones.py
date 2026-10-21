"""从权威回放提取已结算关键节点，不解析战报正文、不运行游戏或评分。"""
from __future__ import annotations

from typing import Any


EXPANSIONS = {"roster_expansion_1": "扩编 1", "roster_expansion_2": "扩编 2"}


def _succeeded(step: dict[str, Any]) -> bool:
    ok = step.get("result", {}).get("ok")
    if isinstance(ok, bool):
        return ok
    return str(step.get("content", "")).lstrip().startswith(("成功 ", "OK "))


def _purchase_id(step: dict[str, Any]) -> str | None:
    value = step.get("arguments", {}).get("upgrade_id")
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        refs = step.get("result", {}).get("_llm_refs", {}).get("upgrade", {})
        return next((identity for identity, ref in refs.items() if ref == value), None)
    return None


def extract_milestones(replay: dict[str, Any], completed_turns: set[int]) -> list[dict[str, Any]]:
    """节点归属发生回合，与传入的已结算曲线回合保持一致。"""
    marks: list[dict[str, Any]] = []
    expansions: set[str] = set()
    level9: set[str] = set()
    # 原生续跑可能只保留紧邻下一回合开局，不使用可能已被准备动作改变的最终局面。
    next_openings = {turn["turn"] - 1: turn["observation_before"] for turn in replay["turns"]
                     if isinstance(turn.get("observation_before"), dict)}

    def add(number: int, kind: str, identity: str, label: str, detail: str) -> None:
        marks.append({"id": f"T{number}:{kind}:{identity}", "turn": number, "kind": kind,
                      "label": label, "detail": detail})

    def expansion(number: int, identity: str) -> None:
        if identity in EXPANSIONS and identity not in expansions:
            expansions.add(identity)
            label = f"解锁{EXPANSIONS[identity]}"
            add(number, "expansion", identity, label, label)

    def transitions(number: int, before: dict[str, Any], after: dict[str, Any]) -> None:
        old_upgrades = {item["upgrade_id"]: item for item in before.get("global_upgrades", [])}
        for item in after.get("global_upgrades", []):
            identity = item["upgrade_id"]
            if item.get("unlocked") is True and old_upgrades.get(identity, {}).get("unlocked") is False:
                expansion(number, identity)
        old_heroes = {item["adventurer_id"]: item for item in before.get("adventurers", [])}
        for item in after.get("adventurers", []):
            identity = item["adventurer_id"]
            previous = old_heroes.get(identity, {}).get("level")
            current = item.get("level")
            if (identity not in level9 and isinstance(previous, int) and not isinstance(previous, bool)
                    and isinstance(current, int) and not isinstance(current, bool) and previous < 9 <= current):
                level9.add(identity)
                name = item.get("name") or identity
                add(number, "level_9", identity, f"{name}达到 Lv9", f"{name}：Lv{previous} → Lv{current}")

    for turn in replay["turns"]:
        number = turn["turn"]
        if turn["status"] != "completed" or number not in completed_turns:
            continue
        state = turn.get("observation_before") or {}
        monsters = {item["monster_id"]: item for item in state.get("monsters", [])}
        for step in turn["steps"]:
            if step.get("type") != "tool_result" or not _succeeded(step):
                continue
            name = step.get("name", "")
            result = step.get("result", {})
            if (name.startswith(("get_", "preview_")) or result.get("simulation_only") is True
                    or name == "refresh_monsters" and step.get("arguments", {}).get("preview") is True):
                continue
            if name == "purchase_upgrade":
                identity = _purchase_id(step)
                if identity is not None:
                    expansion(number, identity)
            if name == "end_turn":
                for index, battle in enumerate(result.get("turn_result", {}).get("battles", []), 1):
                    enemy = monsters.get(battle.get("monster_id"), {})
                    tier = enemy.get("tier")
                    if battle.get("won") is not True or tier not in ("boss", "challenge"):
                        continue
                    kind = "boss_victory" if tier == "boss" else "challenge_victory"
                    label = "战胜首领" if tier == "boss" else "完成挑战"
                    actor = battle.get("adventurer_name")
                    if not actor:
                        actor = next((hero.get("name") for hero in state.get("adventurers", [])
                                      if hero["adventurer_id"] == battle.get("adventurer_id")), None)
                    enemy_name = enemy.get("name") or battle.get("monster_name") or battle["monster_id"]
                    add(number, kind, str(index), label, f"{actor or '出战角色未记录'} · {label}：{enemy_name}")
            after = step.get("observation_after")
            if isinstance(after, dict):
                transitions(number, state, after)
                state = after
                monsters.update((item["monster_id"], item) for item in after.get("monsters", []))
        opening = next_openings.get(number)
        if opening is not None:
            transitions(number, state, opening)
    return marks
