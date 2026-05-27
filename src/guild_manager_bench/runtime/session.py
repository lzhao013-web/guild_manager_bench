from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from guild_manager_bench.game.actions import (
    AllocateExperienceAction,
    CraftAction,
    EndTurnAction,
    EquipAction,
    PreparationAction,
    PurchaseUpgradeAction,
    UnequipAction,
)
from guild_manager_bench.game.engine import TurnResult, apply_preparation_action, end_turn, new_game
from guild_manager_bench.game.state import GameDefinition, GameState
from guild_manager_bench.runtime.action_codec import (
    encode_end_turn_action,
    encode_preparation_action,
)
from guild_manager_bench.runtime.events import SessionEvent
from guild_manager_bench.runtime.observation import build_observation


@dataclass(slots=True)
class GameSession:
    """一局可被外部操作和观察的游戏会话。"""

    definition: GameDefinition
    session_id: str = field(default_factory=lambda: uuid4().hex)
    state: GameState | None = None
    events: list[SessionEvent] = field(default_factory=list)
    _next_sequence: int = 1

    def __post_init__(self) -> None:
        if self.state is None:
            self.state = new_game(self.definition)
        if not self.events:
            self._append_event("session_started", {"summary": "会话开始"})

    def observation(self) -> dict[str, Any]:
        """返回当前会话的可见状态。"""

        assert self.state is not None
        data = build_observation(self.definition, self.state)
        data["session_id"] = self.session_id
        return data

    def apply_preparation(self, action: PreparationAction) -> SessionEvent:
        """执行一个回合内操作并记录事件。"""

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

        assert self.state is not None
        return self._append_event(
            "action_rejected",
            {
                "action": dict(payload),
                "summary": "动作被拒绝",
                "error": error,
            },
        )

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
        previous = before_by_id[adventurer_id]
        prefix = adventurer["name"]
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
    raise ValueError(f"unknown adventurer: {adventurer_id}")


def _monster_by_id(observation: dict[str, Any], monster_id: str) -> dict[str, Any]:
    for monster in observation["monsters"]:
        if monster["monster_id"] == monster_id:
            return monster
    raise ValueError(f"unknown monster: {monster_id}")


def _recipe_by_id(observation: dict[str, Any], recipe_id: str) -> dict[str, Any]:
    for recipe in observation["crafting_recipes"]:
        if recipe["recipe_id"] == recipe_id:
            return recipe
    raise ValueError(f"unknown recipe: {recipe_id}")


def _upgrade_by_id(observation: dict[str, Any], upgrade_id: str) -> dict[str, Any]:
    for upgrade in observation["global_upgrades"]:
        if upgrade["upgrade_id"] == upgrade_id:
            return upgrade
    raise ValueError(f"unknown upgrade: {upgrade_id}")


def _equipment_by_instance_id(
    observation: dict[str, Any],
    instance_id: str,
) -> dict[str, Any]:
    for equipment in observation["equipment_inventory"]:
        if equipment["instance_id"] == instance_id:
            return equipment
    raise ValueError(f"unknown equipment instance: {instance_id}")


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
