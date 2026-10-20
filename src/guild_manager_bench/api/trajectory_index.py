"""从已记录事件建立轻量行动索引，不执行工具，也不重建游戏状态。"""
from __future__ import annotations

import json
import re
from typing import Any

from guild_manager_bench.api.action_output import action_output


COLLECTIONS = {
    "member": ("adventurers", "adventurer_id"),
    "equipment": ("equipment_inventory", "instance_id"),
    "upgrade": ("global_upgrades", "upgrade_id"),
    "recipe": ("crafting_recipes", "recipe_id"),
    "candidate": ("recruit_candidates", "candidate_id"),
    "monster": ("monsters", "monster_id"),
}
ARGUMENTS = {
    "adventurer_id": "member", "equipment_id": "equipment",
    "equipment_instance_id": "equipment", "instance_id": "equipment",
    "upgrade_id": "upgrade", "recipe_id": "recipe", "candidate_id": "candidate",
    "monster_id": "monster",
}
REF_KINDS = {"member": "adventurer", "candidate": "recruit"}


class TrajectoryIndex:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.observation: dict[str, Any] = {}
        self.attempt: int | None = None
        self.turn: int | None = None
        self.ordinal = 0
        self.pending: dict[tuple[str, str | None], int] = {}
        self.attempt_states: dict[int, str] = {}
        self.attempt_turns: dict[int, int] = {}
        self.by_attempt: dict[int, list[int]] = {}

    def _state(self, attempt: int, state: str) -> None:
        self.attempt_states[attempt] = state
        for index in self.by_attempt.get(attempt, []):
            self.rows[index]["lifecycle"] = state

    def accept(self, event: dict[str, Any], *, attempt: int, cursor: int, at: str) -> None:
        kind = event["type"]
        if kind == "run_resumed":
            restored = event.get("restored_observation") or {}
            turn = restored.get("turn")
            cutoff = turn + int(restored.get("finished") is True) if turn is not None else None
            for old, state in list(self.attempt_states.items()):
                # 日志中的回合完成事件不保证检查点已经落盘，恢复位置才决定哪些分支仍然有效。
                beyond_checkpoint = cutoff is not None and self.attempt_turns[old] >= cutoff
                if state == "uncommitted" or beyond_checkpoint:
                    self._state(old, "rolled_back")
            self.observation = restored
        if kind in {"run_started", "turn_started"}:
            self.observation = event.get("observation") or {}
        if kind == "turn_started":
            self.attempt, self.turn, self.ordinal = attempt, event["turn"], 0
            self.pending.clear()
            self.attempt_states[attempt] = "uncommitted"
            self.attempt_turns[attempt] = event["turn"]
        if kind == "turn_completed":
            self._state(attempt, "confirmed")
        elif kind == "turn_retry":
            self._state(attempt, "rolled_back")
        if kind not in {"tool_call", "tool_result"}:
            return
        name = event["name"]
        result = event.get("result") or {}
        after = result.get("_observation_after") or self.observation
        if name.startswith(("get_", "preview_")):
            return
        key = (name, event.get("call_id"))
        previous = self.pending.pop(key, None) if kind == "tool_result" else None
        if previous is None:
            self.ordinal += 1
        ordinal = self.rows[previous]["ordinal"] if previous is not None else self.ordinal
        # 部分旧日志只在调用时记录参数，结果合并时不能将其覆盖为空对象。
        recorded_args = "arguments" in event
        arguments = event.get("arguments")
        if not recorded_args and previous is not None:
            call = self.rows[previous]["event"]
            recorded_args = "arguments" in call
            arguments = call.get("arguments")
        args = arguments or {}
        subjects, names = self._subjects(name, args, result, self.observation, after)
        # 仅投影行动所需字段，不发送提示词、原始模型返回或完整局面。
        compact = {k: result[k] for k in ("ok", "error", "event", "_llm_refs", "memo", "monster_refresh") if k in result}
        output = action_output(result, self.observation, result.get("_observation_after") or {},
                               members=tuple(subject["id"] for subject in subjects if subject["kind"] == "member"))
        if output:
            compact["_action_output"] = output
        if "monsters" in result:
            compact["monsters"] = [{key: item[key] for key in ("monster_id", "name", "stats", "reward", "traits", "tier") if key in item}
                                   for item in result["monsters"]]
        if "turn_result" in result:
            compact["turn_result"] = {"battles": [
                {k: b[k] for k in ("adventurer_name", "monster_name", "won", "outcome", "reward") if k in b}
                for b in result["turn_result"].get("battles", [])
            ]}
        tokens = set(re.findall(r"[\w-]+", json.dumps([args, compact], ensure_ascii=False)))
        used_names = {key: value for key, value in names.items() if key in tokens}
        row = {
            "id": f"{attempt}:{ordinal}", "attempt": attempt,
            "turn": event.get("turn", self.turn), "ordinal": ordinal, "cursor": cursor,
            "at": at, "lifecycle": self.attempt_states.get(attempt, "uncommitted"),
            "subjects": subjects, "names": used_names,
            "event": {"type": kind, "name": name, "result": compact,
                      **({"arguments": arguments} if recorded_args else {})},
        }
        if previous is not None:
            self.rows[previous] = row
        else:
            self.by_attempt.setdefault(attempt, []).append(len(self.rows))
            self.rows.append(row)
            if kind == "tool_call":
                self.pending[key] = len(self.rows) - 1
        self.observation = after

    @staticmethod
    def _subjects(name, args, result, before, after):
        objects: dict[str, dict[str, dict[str, Any]]] = {kind: {} for kind in COLLECTIONS}
        names: dict[str, str] = {}
        for observation in (before, after):
            for kind, (collection, key) in COLLECTIONS.items():
                for item in observation.get(collection, []):
                    objects[kind][item[key]] = item
                    names[item[key]] = item["name"]
            for member in observation.get("adventurers", []):
                for skill in member.get("skills", []) + member.get("tactics", {}).get("available_active_skills", []):
                    names[skill["skill_id"]] = skill["name"]
        subjects: dict[tuple[str, str], dict[str, Any]] = {}

        def add(kind, identity):
            if identity is None:
                return
            if isinstance(identity, int):
                refs = result.get("_llm_refs", {}).get(REF_KINDS.get(kind, kind), {})
                identity = next((key for key, value in refs.items() if value == identity), None)
            if identity is None:
                return  # 旧日志未记录短引用映射，不能把数字当成真实对象 ID。
            item = objects[kind].get(identity)
            subjects[kind, identity] = {"kind": kind, "id": identity, "name": item["name"] if item else identity}

        for key, kind in ARGUMENTS.items():
            add(kind, args.get(key))
        if result.get("ok") is True:
            for kind in ("equipment", "member", "upgrade"):
                collection, key = COLLECTIONS[kind]
                old = {item[key]: item for item in before.get(collection, [])}
                for item in after.get(collection, []):
                    if item[key] not in old or kind == "upgrade" and item.get("unlocked") and not old[item[key]].get("unlocked"):
                        add(kind, item[key])
                        if kind == "equipment":
                            subjects[kind, item[key]]["created"] = True
                    elif kind == "equipment" and item.get("equipped_by") != old[item[key]].get("equipped_by"):
                        add(kind, item[key])
                        previous_owner = old[item[key]].get("equipped_by")
                        current_owner = item.get("equipped_by")
                        subjects[kind, item[key]]["ownership"] = {
                            "before": {"id": previous_owner, "name": names[previous_owner]} if previous_owner else None,
                            "after": {"id": current_owner, "name": names[current_owner]} if current_owner else None,
                        }
                        add("member", previous_owner)
                        add("member", current_owner)
        return list(subjects.values()), names
