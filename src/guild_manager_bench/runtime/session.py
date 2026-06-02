from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from uuid import uuid4

from guild_manager_bench.bench.metrics import rank_score_breakdown_from_final_observation
from guild_manager_bench.game.actions import (
    AllocateExperienceAction,
    CraftAction,
    DismissAction,
    EndTurnAction,
    EquipAction,
    PreparationAction,
    PurchaseUpgradeAction,
    RecruitAction,
    UnequipAction,
)
from guild_manager_bench.game.engine import TurnResult, apply_preparation_action, end_turn, new_game, preview_battle
from guild_manager_bench.game.state import GameDefinition, GameState
from guild_manager_bench.runtime.action_codec import (
    encode_end_turn_action,
    encode_preparation_action,
)
from guild_manager_bench.runtime.events import SessionEvent, event_to_dict
from guild_manager_bench.runtime.observation import build_observation


@dataclass(slots=True)
class GameSession:
    """一局可被外部操作和观察的游戏会话。"""

    definition: GameDefinition
    session_id: str = field(default_factory=lambda: uuid4().hex)
    state: GameState | None = None
    events: list[SessionEvent] = field(default_factory=list)
    _next_sequence: int = 1
    _lock: Lock = field(default_factory=Lock)

    def __post_init__(self) -> None:
        if self.state is None:
            self.state = new_game(self.definition)
        if not self.events:
            self._append_event("session_started", {"summary": "会话开始"})

    def observation(self) -> dict[str, Any]:
        """返回当前会话的可见状态。"""

        with self._lock:
            assert self.state is not None
            data = build_observation(self.definition, self.state)
        data["session_id"] = self.session_id
        return data

    def apply_preparation(self, action: PreparationAction) -> SessionEvent:
        """执行一个回合内操作并记录事件。"""

        with self._lock:
            assert self.state is not None
            turn = self.state.turn
            before_observation = build_observation(self.definition, self.state)
            self.state = apply_preparation_action(self.definition, self.state, action)
            after_observation = build_observation(self.definition, self.state)
            return self._append_event(
                "preparation_applied",
                {
                    "action": encode_preparation_action(action),
                    "summary": _preparation_summary(self.definition, before_observation, action),
                    "changes": _observation_changes(before_observation, after_observation),
                },
                turn=turn,
            )

    def end_turn(self, action: EndTurnAction) -> tuple[TurnResult, SessionEvent]:
        """提交交战列表，结算当前回合并记录事件。"""

        with self._lock:
            assert self.state is not None
            turn = self.state.turn
            before_observation = build_observation(self.definition, self.state)
            result = end_turn(self.definition, self.state, action)
            self.state = result.state
            after_observation = build_observation(self.definition, self.state)
            battles = [
                _battle_to_dict(battle, before_observation, after_observation)
                for battle in result.battles
            ]
            event = self._append_event(
                "turn_ended",
                {
                    "action": encode_end_turn_action(action),
                    "summary": _end_turn_summary(turn, battles),
                    "changes": _observation_changes(before_observation, after_observation),
                    "battles": battles,
                },
            turn=turn,
        )
        return result, event

    def reject_action(self, payload: dict[str, Any], error: str) -> SessionEvent:
        """记录一次被拒绝的外部动作。"""

        with self._lock:
            assert self.state is not None
            return self._append_event(
            "action_rejected",
            {
                "action": dict(payload),
                "summary": "动作被拒绝",
                "error": error,
            },
        )

    def preview_battle(self, adventurer_id: str, monster_id: str) -> dict[str, Any]:
        """预览一场 1v1 战斗，不改变游戏状态。"""

        with self._lock:
            assert self.state is not None
            before_observation = build_observation(self.definition, self.state)
            settlement = preview_battle(
                self.definition, self.state,
                adventurer_id=adventurer_id, monster_id=monster_id,
            )
            result = settlement.combat_result
            adventurer = _adventurer_by_id(before_observation, adventurer_id)
            monster = _monster_by_id(before_observation, monster_id)
            return {
                "won": settlement.won,
                "adventurer_id": adventurer_id,
                "adventurer_name": adventurer["name"],
                "monster_id": monster_id,
                "monster_name": monster["name"],
                "adventurer_before_resources": adventurer["resources"],
                "adventurer_after_resources": {
                    "current_hp": result.left_resources.current_hp,
                    "current_mp": result.left_resources.current_mp,
                },
                "monster_stats": monster["stats"],
                "combat": {
                    "outcome": result.outcome,
                    "winner_side": result.winner_side,
                    "reason": result.reason,
                    "actions_taken": result.actions_taken,
                    "time_elapsed": result.time_elapsed,
                },
            }

    def export_replay(self) -> dict[str, Any]:
        """将会话导出为可保存的 replay 格式。"""

        with self._lock:
            assert self.state is not None
            observation = build_observation(self.definition, self.state)
            events = [event_to_dict(e) for e in self.events]
            state_dict = _serialize_state(self.state)

        # Compute score from final observation
        try:
            breakdown = rank_score_breakdown_from_final_observation(
                self.definition, observation,
            )
            score: dict[str, Any] = {
                "rank_score": breakdown["rank_score"],
                "rank_score_source": "final_observation",
                "per_adventurer": breakdown.get("per_adventurer", []),
            }
        except Exception:
            score = {}

        # Group events into turns
        turns = _build_manual_turns(events)

        # Aggregate stats from events
        stats = _compute_manual_stats(events)

        return {
            "schema_version": 1,
            "kind": "manual_replay",
            "created_at": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"),
            "session_id": self.session_id,
            "status": "finished" if observation.get("finished") else "in_progress",
            "turns": turns,
            "final_observation": observation,
            "score": score,
            "stats": stats,
            "events": events,
            "_state": state_dict,
        }

    @classmethod
    def from_export(cls, definition: GameDefinition, data: dict[str, Any]) -> GameSession:
        """从导出的 JSON 恢复一个可操作的会话。"""

        state_dict = data.get("_state")
        if isinstance(state_dict, dict):
            state = _deserialize_state(state_dict)
        else:
            # Fallback: reconstruct from final_observation
            observation = data.get("final_observation")
            if not isinstance(observation, dict):
                raise ValueError("存档文件缺少内部状态和游戏状态数据，无法恢复")
            state = _restore_from_observation(definition, observation)

        session_id = data.get("session_id") or uuid4().hex
        session = cls(definition=definition, session_id=session_id, state=state)

        # Restore historical events from the export so timeline/battle log are intact
        _restore_events(session, data.get("events"))

        return session

    def _append_event(
        self,
        event_type,
        payload: dict[str, Any],
        *,
        turn: int | None = None,
    ) -> SessionEvent:
        assert self.state is not None
        event = SessionEvent(
            sequence=self._next_sequence,
            turn=self.state.turn if turn is None else turn,
            event_type=event_type,
            payload=payload,
        )
        self.events.append(event)
        self._next_sequence += 1
        return event


def _battle_to_dict(
    battle,
    before_observation: dict[str, Any],
    after_observation: dict[str, Any],
) -> dict[str, Any]:
    result = battle.combat_result
    adventurer_before = _adventurer_by_id(before_observation, battle.adventurer_id)
    adventurer_after = _adventurer_by_id(after_observation, battle.adventurer_id)
    monster = _monster_by_id(before_observation, battle.monster_id)
    return {
        "adventurer_id": battle.adventurer_id,
        "adventurer_name": adventurer_before["name"],
        "monster_id": battle.monster_id,
        "monster_name": monster["name"],
        "won": battle.won,
        "reward": {
            "gold": battle.reward.gold,
            "experience": battle.reward.experience,
            "materials": dict(battle.reward.materials),
        },
        "adventurer_before_resources": adventurer_before["resources"],
        "adventurer_after_resources": adventurer_after["resources"],
        "monster_stats": monster["stats"],
        "combat": {
            "outcome": result.outcome,
            "winner_side": result.winner_side,
            "reason": result.reason,
            "actions_taken": result.actions_taken,
            "time_elapsed": result.time_elapsed,
            "events": [
                {
                    "action_index": event.action_index,
                    "time_elapsed": event.time_elapsed,
                    "action_type": event.action_type,
                    "actor_side": event.actor_side,
                    "actor_id": event.actor_id,
                    "target_side": event.target_side,
                    "target_id": event.target_id,
                    "damage": event.damage,
                    "target_hp": event.target_hp,
                    "skill_id": event.skill_id,
                    "skill_name": event.skill_name,
                    "healing": event.healing,
                    "healing_target_side": event.healing_target_side,
                    "healing_target_hp": event.healing_target_hp,
                    "status_id": event.status_id,
                    "status_name": event.status_name,
                }
                for event in result.events
            ],
        },
    }


def _preparation_summary(
    definition: GameDefinition,
    observation: dict[str, Any],
    action: PreparationAction,
) -> str:
    if isinstance(action, CraftAction):
        recipe = _recipe_by_id(observation, action.recipe_id)
        return f"合成 {recipe['name']}"
    if isinstance(action, PurchaseUpgradeAction):
        upgrade = _upgrade_by_id(observation, action.upgrade_id)
        return f"购买全局加成 {upgrade['name']}"
    if isinstance(action, AllocateExperienceAction):
        adventurer = _adventurer_by_id(observation, action.adventurer_id)
        return f"分配 {action.amount} 经验给 {adventurer['name']}"
    if isinstance(action, RecruitAction):
        candidate = _recruit_candidate_by_id(observation, action.candidate_id)
        return f"招募 {candidate['name']}"
    if isinstance(action, DismissAction):
        adventurer = _adventurer_by_id(observation, action.adventurer_id)
        return f"解散 {adventurer['name']}"
    if isinstance(action, EquipAction):
        adventurer = _adventurer_by_id(observation, action.adventurer_id)
        equipment = _equipment_by_instance_id(observation, action.equipment_instance_id)
        return f"{adventurer['name']} 装备 {equipment['name']}"
    if isinstance(action, UnequipAction):
        adventurer = _adventurer_by_id(observation, action.adventurer_id)
        return f"{adventurer['name']} 卸下 {_slot_name(action.slot)}"
    raise TypeError("action must be a preparation action")


def _end_turn_summary(turn: int, battles: list[dict[str, Any]]) -> str:
    wins = sum(1 for battle in battles if battle["won"])
    losses = len(battles) - wins
    return f"结束第 {turn} 回合：{len(battles)} 场战斗，{wins} 胜 {losses} 负"


def _observation_changes(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    _append_value_change(changes, "resource", "金币", before["gold"], after["gold"])
    _append_value_change(
        changes,
        "resource",
        "队伍人数上限",
        before.get("party_size_limit"),
        after.get("party_size_limit"),
    )
    _append_value_change(
        changes,
        "resource",
        "经验池",
        before["experience_pool"],
        after["experience_pool"],
    )
    if before["turn"] != after["turn"]:
        changes.append(
            {
                "kind": "turn",
                "label": "回合",
                "before": before["turn"],
                "after": after["turn"],
            }
        )

    material_ids = sorted(set(before["materials"]) | set(after["materials"]))
    for material_id in material_ids:
        _append_value_change(
            changes,
            "material",
            material_id,
            before["materials"].get(material_id, 0),
            after["materials"].get(material_id, 0),
        )

    _append_adventurer_changes(changes, before, after)
    _append_equipment_changes(changes, before, after)
    _append_upgrade_changes(changes, before, after)
    return changes


def _append_adventurer_changes(
    changes: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    before_by_id = {
        adventurer["adventurer_id"]: adventurer
        for adventurer in before["adventurers"]
    }
    for adventurer in after["adventurers"]:
        adventurer_id = adventurer["adventurer_id"]
        prefix = adventurer["name"]
        previous = before_by_id.get(adventurer_id)
        if previous is None:
            changes.append(
                {
                    "kind": "adventurer",
                    "label": "新冒险者",
                    "after": f"{prefix} ({adventurer_id})",
                }
            )
            continue
        for key, label in (("level", "等级"), ("experience", "经验")):
            _append_value_change(
                changes,
                "adventurer",
                f"{prefix} {label}",
                previous[key],
                adventurer[key],
            )
        for key, label in (("current_hp", "HP"), ("current_mp", "MP")):
            _append_value_change(
                changes,
                "adventurer",
                f"{prefix} {label}",
                previous["resources"][key],
                adventurer["resources"][key],
            )
        for key, label in (
            ("hp", "最大 HP"),
            ("mp", "最大 MP"),
            ("attack", "攻击"),
            ("defense", "防御"),
            ("speed", "速度"),
            ("recovery", "战后回血"),
            ("mp_recovery", "战后回魔"),
        ):
            _append_value_change(
                changes,
                "adventurer",
                f"{prefix} {label}",
                previous["effective_stats"][key],
                adventurer["effective_stats"][key],
            )


def _append_equipment_changes(
    changes: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    before_items = {
        item["instance_id"]: item
        for item in before["equipment_inventory"]
    }
    after_items = {
        item["instance_id"]: item
        for item in after["equipment_inventory"]
    }
    for instance_id, item in after_items.items():
        if instance_id not in before_items:
            changes.append(
                {
                    "kind": "equipment",
                    "label": "获得装备",
                    "after": f"{item['name']} ({instance_id})",
                }
            )
            continue
        previous_owner = before_items[instance_id].get("equipped_by")
        current_owner = item.get("equipped_by")
        if previous_owner != current_owner:
            changes.append(
                {
                    "kind": "equipment",
                    "label": f"{item['name']} 装备者",
                    "before": previous_owner or "未装备",
                    "after": current_owner or "未装备",
                }
            )


def _append_upgrade_changes(
    changes: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    before_unlocked = {
        upgrade["upgrade_id"]
        for upgrade in before["global_upgrades"]
        if upgrade["unlocked"]
    }
    for upgrade in after["global_upgrades"]:
        if upgrade["unlocked"] and upgrade["upgrade_id"] not in before_unlocked:
            changes.append(
                {
                    "kind": "upgrade",
                    "label": "解锁加成",
                    "after": upgrade["name"],
                }
            )


def _append_value_change(
    changes: list[dict[str, Any]],
    kind: str,
    label: str,
    before: Any,
    after: Any,
) -> None:
    if before == after:
        return
    changes.append(
        {
            "kind": kind,
            "label": label,
            "before": before,
            "after": after,
        }
    )


def _adventurer_by_id(observation: dict[str, Any], adventurer_id: str) -> dict[str, Any]:
    for adventurer in observation["adventurers"]:
        if adventurer["adventurer_id"] == adventurer_id:
            return adventurer
    raise ValueError(f"未找到冒险者: {adventurer_id}")


def _monster_by_id(observation: dict[str, Any], monster_id: str) -> dict[str, Any]:
    for monster in observation["monsters"]:
        if monster["monster_id"] == monster_id:
            return monster
    raise ValueError(f"未找到怪物: {monster_id}")


def _recipe_by_id(observation: dict[str, Any], recipe_id: str) -> dict[str, Any]:
    for recipe in observation["crafting_recipes"]:
        if recipe["recipe_id"] == recipe_id:
            return recipe
    raise ValueError(f"未找到制作配方: {recipe_id}")


def _upgrade_by_id(observation: dict[str, Any], upgrade_id: str) -> dict[str, Any]:
    for upgrade in observation["global_upgrades"]:
        if upgrade["upgrade_id"] == upgrade_id:
            return upgrade
    raise ValueError(f"未找到全局升级: {upgrade_id}")


def _recruit_candidate_by_id(observation: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    for candidate in observation["recruit_candidates"]:
        if candidate["candidate_id"] == candidate_id:
            return candidate
    raise ValueError(f"未找到招募候选: {candidate_id}")


def _equipment_by_instance_id(
    observation: dict[str, Any],
    instance_id: str,
) -> dict[str, Any]:
    for equipment in observation["equipment_inventory"]:
        if equipment["instance_id"] == instance_id:
            return equipment
    raise ValueError(f"未找到装备实例: {instance_id}")


def _slot_name(slot: str) -> str:
    return {
        "main_hand": "右手",
        "off_hand": "左手",
        "two_hand": "双手",
        "boots": "鞋子",
        "helmet": "头盔",
        "armor": "护甲",
        "accessory": "饰品",
    }.get(slot, slot)


def _build_manual_turns(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将事件列表按回合分组，构建 turns 数组。"""

    turns_map: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        turn_num = event.get("turn", 0)
        turns_map.setdefault(turn_num, []).append(event)

    turns = []
    for turn_num in sorted(turns_map):
        turn_events = turns_map[turn_num]
        steps = []
        battles = []
        summary = ""

        for event in turn_events:
            event_type = event.get("type", "")
            payload = event.get("payload", {})

            if event_type == "session_started":
                continue

            if event_type == "preparation_applied":
                steps.append({
                    "type": "manual_action",
                    "action": payload.get("action", {}),
                    "summary": payload.get("summary", ""),
                    "changes": payload.get("changes", []),
                })

            if event_type == "turn_ended":
                summary = payload.get("summary", "")
                for battle in payload.get("battles", []):
                    battles.append(battle)

            if event_type == "action_rejected":
                steps.append({
                    "type": "action_rejected",
                    "action": payload.get("action", {}),
                    "error": payload.get("error", ""),
                })

        turns.append({
            "turn": turn_num,
            "status": "completed",
            "steps": steps,
            "battles": battles,
            "summary": summary,
        })

    return turns


def _compute_manual_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    """从事件列表汇总统计数据。"""

    battles_total = 0
    battles_won = 0
    gold_earned = 0
    exp_earned = 0
    crafted = 0
    upgrades = 0
    recruited = 0

    for event in events:
        payload = event.get("payload", {})
        action = payload.get("action", {})
        action_type = action.get("type", "")

        if action_type == "craft":
            crafted += 1
        elif action_type == "purchase_upgrade":
            upgrades += 1
        elif action_type == "recruit":
            recruited += 1

        for battle in payload.get("battles", []):
            battles_total += 1
            if battle.get("won"):
                battles_won += 1
            reward = battle.get("reward", {})
            gold_earned += reward.get("gold", 0)
            exp_earned += reward.get("experience", 0)

    return {
        "game_actions": {
            "battles_total": battles_total,
            "battles_won": battles_won,
            "battles_lost": battles_total - battles_won,
            "total_gold_earned": gold_earned,
            "total_experience_earned": exp_earned,
            "total_equipment_crafted": crafted,
            "total_upgrades_purchased": upgrades,
            "total_recruits": recruited,
        },
    }


# ============================================================================
# State Serialization / Deserialization
# ============================================================================

def _serialize_state(state: GameState) -> dict[str, Any]:
    """Serialize GameState to a JSON-compatible dict."""
    from dataclasses import asdict
    raw = asdict(state)
    return _jsonify(raw)


def _jsonify(obj: Any) -> Any:
    """Recursively convert non-JSON types (frozenset, MappingProxyType, tuple) to JSON-safe types."""
    from collections.abc import Mapping
    if isinstance(obj, frozenset):
        return sorted(_jsonify(v) for v in obj) if all(isinstance(v, str) for v in obj) else [_jsonify(v) for v in obj]
    if isinstance(obj, tuple):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, Mapping):
        return {str(k): _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


# Type registry for deserialization: fully-qualified class name -> constructor
_DESER_TYPES: dict[str, type] = {}


def _deserialize_state(data: dict[str, Any]) -> GameState:
    """Recursively deserialize a dict back into a GameState."""
    import dataclasses
    from guild_manager_bench.game import (
        equipment as _eq_mod,
        models as _models_mod,
        progression as _prog_mod,
        skills as _skills_mod,
        state as _state_mod,
    )

    # Auto-discover all frozen dataclass types from relevant modules
    for mod in (_state_mod, _models_mod, _skills_mod, _eq_mod, _prog_mod):
        for name in dir(mod):
            obj = getattr(mod, name, None)
            if dataclasses.is_dataclass(obj) and isinstance(obj, type):
                _DESER_TYPES[f"{obj.__module__}.{obj.__qualname__}"] = obj

    return _reconstruct(data, GameState)


def _reconstruct(data: Any, hint: type | None = None) -> Any:
    """Recursively reconstruct a dataclass instance from a dict."""
    if data is None:
        return None

    # Primitives
    if isinstance(data, (str, int, float, bool)):
        return data

    # List or tuple → reconstruct elements with item type hint
    if isinstance(data, (list, tuple)):
        if hint is not None:
            origin = getattr(hint, "__origin__", None)
            args = getattr(hint, "__args__", None)
            if origin is tuple and args:
                item_type = args[0]
                items = tuple(_reconstruct(item, item_type) for item in data)
                return items
            if origin is frozenset and args:
                item_type = args[0]
                return frozenset(_reconstruct(item, item_type) for item in data)
        return data

    # Dict → reconstruct dataclass or plain dict
    if isinstance(data, dict):
        cls = None
        if hint is not None:
            cls = _extract_dataclass_from_hint(hint)
        if cls is None:
            return data

        # Get resolved field types
        field_types = _get_field_types(cls)

        kwargs = {}
        for key, value in data.items():
            field_hint = field_types.get(key)
            kwargs[key] = _reconstruct(value, field_hint)

        return cls(**kwargs)

    return data


def _extract_dataclass_from_hint(hint: Any) -> type | None:
    """Extract a dataclass type from a hint, handling Union/Optional."""
    if _is_dataclass_type(hint):
        return hint
    # Handle Union types like StatusDefinition | None
    args = getattr(hint, "__args__", None)
    if args:
        for arg in args:
            if _is_dataclass_type(arg):
                return arg
    return None


def _get_field_types(cls: type) -> dict[str, type | None]:
    """Get resolved field types for a dataclass, resolving forward refs."""
    import typing
    try:
        hints = typing.get_type_hints(cls)
    except Exception:
        hints = {}
    return {f.name: hints.get(f.name) for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]


def _restore_events(session: GameSession, events_data: Any) -> None:
    """从导出的事件列表恢复到会话中，保留时间线和战斗日志。"""
    if not isinstance(events_data, list) or not events_data:
        return

    session.events.clear()
    max_seq = 0
    for event_data in events_data:
        if not isinstance(event_data, dict):
            continue
        seq = event_data.get("sequence", 0)
        if not isinstance(seq, int) or seq < 1:
            continue
        turn = event_data.get("turn", 1)
        if not isinstance(turn, int) or turn < 1:
            turn = 1
        event = SessionEvent(
            sequence=seq,
            turn=turn,
            event_type=event_data.get("type", "session_started"),
            payload=event_data.get("payload", {}),
        )
        session.events.append(event)
        if seq > max_seq:
            max_seq = seq
    session._next_sequence = max(max_seq + 1, session._next_sequence)


def _max_trailing_number(items: list[dict], key: str) -> int:
    """Extract the trailing number from IDs like 'eq_0001', 'adv_3'."""
    import re
    nums = []
    for item in items:
        raw = item.get(key, "")
        m = re.search(r"(\d+)(?:\D|$)", str(raw))
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0)


def _restore_from_observation(definition: GameDefinition, obs: dict[str, Any]) -> GameState:
    """从 observation dict 反向构建 GameState（兼容无 _state 的旧存档）。"""
    from guild_manager_bench.game.equipment import EquippedItem, EquipmentLoadout
    from guild_manager_bench.game.models import CombatResources, CombatStatModifier, CombatStats
    from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect, StatusDefinition
    from guild_manager_bench.game.state import (
        AdventurerState,
        EquipmentInstance,
        LevelSkillUnlock,
        RecruitCandidate,
        RewardBundle,
        SpawnedMonster,
    )

    def _stats(d: dict) -> CombatStats:
        return CombatStats(**{k: d.get(k, 0) for k in ("hp", "mp", "attack", "defense", "speed", "recovery", "mp_recovery")})

    def _modifier(d: dict) -> CombatStatModifier:
        return CombatStatModifier(**{k: d.get(k, 0.0) for k in ("hp", "mp", "attack", "defense", "speed", "recovery", "mp_recovery")})

    def _resources(d: dict) -> CombatResources:
        return CombatResources(current_hp=d.get("current_hp", 1), current_mp=d.get("current_mp", 0))

    def _condition(d: dict | None) -> SkillCondition:
        if not d:
            return SkillCondition(condition_type="always", value=None, conditions=())
        children = tuple(_condition(c) for c in (d.get("conditions") or []))
        return SkillCondition(condition_type=d.get("type", "always"), value=d.get("value"), conditions=children)

    def _status(d: dict | None) -> StatusDefinition | None:
        if not d:
            return None
        return StatusDefinition(
            status_id=d.get("status_id", ""),
            name=d.get("name", ""),
            duration=d.get("duration", 0),
            polarity=d.get("polarity", "neutral"),
            stack_mode=d.get("stack_mode", "replace"),
            effects=tuple(_effect(e) for e in (d.get("effects") or [])),
        )

    def _effect(d: dict) -> SkillEffect:
        return SkillEffect(
            effect_type=d.get("type", "damage_multiplier"),
            value=d.get("value"),
            stat=d.get("stat"),
            target=d.get("target"),
            status=_status(d.get("status")),
        )

    def _skill(d: dict) -> Skill:
        return Skill(
            skill_id=d.get("skill_id", ""),
            name=d.get("name", ""),
            kind=d.get("kind", "active"),
            condition=_condition(d.get("condition")),
            effects=tuple(_effect(e) for e in (d.get("effects") or [])),
            mp_cost=d.get("mp_cost", 0),
            priority=d.get("priority", 0),
            once_per_battle=d.get("once_per_battle", False),
            free=d.get("free", False),
        )

    def _reward(d: dict) -> RewardBundle:
        return RewardBundle(gold=d.get("gold", 0), experience=d.get("experience", 0), materials=d.get("materials", {}))

    def _adventurer(a: dict, excluded_ids: set[str]) -> AdventurerState:
        equipment_slots = a.get("equipment_slots") or []
        equipped_items = []
        for sl in equipment_slots:
            item = sl.get("item")
            if item:
                equipped_items.append(EquippedItem(slot=sl.get("slot", "main_hand"), instance_id=item.get("instance_id", "")))
        growth = a.get("stat_growth_per_level")
        # Filter effective skills to get only base skills
        base_skills = tuple(
            _skill(s) for s in (a.get("skills") or [])
            if s.get("skill_id", "") not in excluded_ids
        )
        return AdventurerState(
            adventurer_id=a.get("adventurer_id", ""),
            name=a.get("name", ""),
            base_stats=_stats(a.get("base_stats", {})),
            resources=_resources(a.get("resources", {})),
            skills=base_skills,
            level_skill_unlocks=tuple(
                LevelSkillUnlock(
                    level=u.get("level", 1),
                    skills=tuple(_skill(s) for s in (u.get("skills") or [])),
                )
                for u in (a.get("level_skill_unlocks") or [])
            ),
            stat_growth_per_level=_modifier(growth) if growth else None,
            level=a.get("level", 1),
            experience=a.get("experience", 0),
            equipment=EquipmentLoadout(items=tuple(equipped_items)),
            template_id=a.get("template_id", ""),
        )

    def _monster(m: dict) -> SpawnedMonster:
        return SpawnedMonster(
            monster_id=m.get("monster_id", ""),
            archetype_id=m.get("archetype_id", ""),
            name=m.get("name", ""),
            stats=_stats(m.get("stats", {})),
            reward=_reward(m.get("reward", {})),
            tier=m.get("tier", "normal"),
            skills=tuple(_skill(s) for s in (m.get("skills") or [])),
        )

    def _candidate(c: dict) -> RecruitCandidate:
        growth = c.get("stat_growth_per_level")
        return RecruitCandidate(
            candidate_id=c.get("candidate_id", ""),
            template_id=c.get("template_id", ""),
            name=c.get("name", ""),
            recruit_gold=c.get("recruit_gold", 0),
            base_stats=_stats(c.get("base_stats", {})),
            skills=tuple(_skill(s) for s in (c.get("skills") or [])),
            level_skill_unlocks=tuple(
                LevelSkillUnlock(
                    level=u.get("level", 1),
                    skills=tuple(_skill(s) for s in (u.get("skills") or [])),
                )
                for u in (c.get("level_skill_unlocks") or [])
            ),
            stat_growth_per_level=_modifier(growth) if growth else None,
        )

    def _equipment(eq: dict) -> EquipmentInstance:
        return EquipmentInstance(instance_id=eq.get("instance_id", ""), template_id=eq.get("template_id", ""))

    # Compute next instance/adventurer numbers from existing data
    equip_list = obs.get("equipment_inventory") or []
    adv_list = obs.get("adventurers") or []
    max_instance = _max_trailing_number(equip_list, "instance_id")
    max_adv = _max_trailing_number(adv_list, "adventurer_id")

    # Collect skill IDs that come from non-base sources (level unlocks, equipment, upgrades).
    # These must be excluded from AdventurerState.skills to avoid duplication when
    # effective_adventurer_skills re-derives the full list.
    excluded_skill_ids: set[str] = set()
    for a in adv_list:
        adv_level = a.get("level", 1)
        # Already-unlocked level skills
        for u in (a.get("level_skill_unlocks") or []):
            if u.get("level", 0) <= adv_level:
                for s in (u.get("skills") or []):
                    excluded_skill_ids.add(s.get("skill_id", ""))
        # Equipment skills
        for sl in (a.get("equipment_slots") or []):
            item = sl.get("item")
            if item:
                for s in (item.get("skills") or []):
                    excluded_skill_ids.add(s.get("skill_id", ""))
    # Unlocked upgrade skills
    for u in (obs.get("global_upgrades") or []):
        if u.get("unlocked"):
            for s in (u.get("skills") or []):
                excluded_skill_ids.add(s.get("skill_id", ""))

    # Get unlocked upgrade IDs, filtered to those that exist in the definition
    valid_upgrade_ids = {u.upgrade_id for u in definition.content.global_upgrades}
    upgrades = obs.get("global_upgrades") or []
    unlocked_ids = frozenset(
        uid for uid in (u.get("upgrade_id") for u in upgrades if u.get("unlocked"))
        if uid in valid_upgrade_ids
    )

    return GameState(
        turn=obs.get("turn", 1),
        max_turns=obs.get("max_turns", 10),
        seed=obs.get("seed", 0),
        gold=obs.get("gold", 0),
        materials=obs.get("materials", {}),
        experience_pool=obs.get("experience_pool", 0),
        adventurers=tuple(_adventurer(a, excluded_skill_ids) for a in adv_list),
        equipment_inventory=tuple(_equipment(eq) for eq in equip_list),
        unlocked_upgrade_ids=unlocked_ids,
        current_monsters=tuple(_monster(m) for m in (obs.get("monsters") or [])),
        recruit_candidates=tuple(_candidate(c) for c in (obs.get("recruit_candidates") or [])),
        next_equipment_instance_number=max_instance + 1,
        next_adventurer_number=max_adv + 1,
    )


def _is_dataclass_type(t: Any) -> bool:
    if t is None:
        return False
    return isinstance(t, type) and hasattr(t, "__dataclass_fields__")
