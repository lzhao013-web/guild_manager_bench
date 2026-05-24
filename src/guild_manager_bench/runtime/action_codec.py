from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from guild_manager_bench.game.actions import (
    AllocateExperienceAction,
    CraftAction,
    EndTurnAction,
    EquipAction,
    HuntAction,
    PreparationAction,
    PurchaseUpgradeAction,
    UnequipAction,
)


class ActionCodecError(ValueError):
    """动作数据编解码失败。"""


def decode_preparation_action(payload: Mapping[str, Any]) -> PreparationAction:
    """把外部动作数据解析为回合内操作。"""

    action_type = _action_type(payload)
    if action_type == "craft":
        return CraftAction(recipe_id=_str(payload, "recipe_id"))
    if action_type == "purchase_upgrade":
        return PurchaseUpgradeAction(upgrade_id=_str(payload, "upgrade_id"))
    if action_type == "allocate_experience":
        return AllocateExperienceAction(
            adventurer_id=_str(payload, "adventurer_id"),
            amount=_int(payload, "amount"),
        )
    if action_type == "equip":
        return EquipAction(
            adventurer_id=_str(payload, "adventurer_id"),
            equipment_instance_id=_str(payload, "equipment_instance_id"),
        )
    if action_type == "unequip":
        return UnequipAction(
            adventurer_id=_str(payload, "adventurer_id"),
            slot=_str(payload, "slot"),
        )
    raise ActionCodecError(f"unknown preparation action type: {action_type}")


def decode_end_turn_action(payload: Mapping[str, Any]) -> EndTurnAction:
    """把外部动作数据解析为结束回合操作。"""

    action_type = _action_type(payload)
    if action_type != "end_turn":
        raise ActionCodecError(f"expected end_turn action, got: {action_type}")
    return EndTurnAction(hunts=_decode_hunts(payload.get("hunts", ())))


def encode_preparation_action(action: PreparationAction) -> dict[str, Any]:
    """把回合内操作转成可写入日志的数据。"""

    if isinstance(action, CraftAction):
        return {"type": "craft", "recipe_id": action.recipe_id}
    if isinstance(action, PurchaseUpgradeAction):
        return {"type": "purchase_upgrade", "upgrade_id": action.upgrade_id}
    if isinstance(action, AllocateExperienceAction):
        return {
            "type": "allocate_experience",
            "adventurer_id": action.adventurer_id,
            "amount": action.amount,
        }
    if isinstance(action, EquipAction):
        return {
            "type": "equip",
            "adventurer_id": action.adventurer_id,
            "equipment_instance_id": action.equipment_instance_id,
        }
    if isinstance(action, UnequipAction):
        return {
            "type": "unequip",
            "adventurer_id": action.adventurer_id,
            "slot": action.slot,
        }
    raise TypeError("action must be a preparation action")


def encode_end_turn_action(action: EndTurnAction) -> dict[str, Any]:
    """把结束回合操作转成可写入日志的数据。"""

    return {
        "type": "end_turn",
        "hunts": [
            {
                "adventurer_id": hunt.adventurer_id,
                "monster_id": hunt.monster_id,
            }
            for hunt in action.hunts
        ],
    }


def _decode_hunts(value: Any) -> tuple[HuntAction, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ActionCodecError("hunts must be a list")
    hunts = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ActionCodecError(f"hunts[{index}] must be an object")
        hunts.append(
            HuntAction(
                adventurer_id=_str(item, "adventurer_id"),
                monster_id=_str(item, "monster_id"),
            )
        )
    return tuple(hunts)


def _action_type(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        raise ActionCodecError("action payload must be an object")
    return _str(payload, "type")


def _str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ActionCodecError(f"{key} must be a non-empty string")
    return value


def _int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ActionCodecError(f"{key} must be an integer")
    return value

