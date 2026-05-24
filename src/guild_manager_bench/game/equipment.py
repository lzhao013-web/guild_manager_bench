from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

from guild_manager_bench.game.models import (
    CombatStatModifier,
    CombatStats,
    apply_stat_modifier,
)
from guild_manager_bench.game.skills import Skill


EquipmentSlot = Literal[
    "main_hand",
    "off_hand",
    "two_hand",
    "boots",
    "helmet",
    "armor",
    "accessory",
]
EQUIPMENT_SLOTS: set[EquipmentSlot] = {
    "main_hand",
    "off_hand",
    "two_hand",
    "boots",
    "helmet",
    "armor",
    "accessory",
}


@dataclass(frozen=True, slots=True)
class EquipmentTemplate:
    """装备模板。

    模板定义装备的固定属性加成和附带技能；运行时背包里的装备实例引用模板。
    """

    equipment_id: str
    name: str
    slot: EquipmentSlot
    stat_modifier: CombatStatModifier = field(default_factory=CombatStatModifier)
    skills: tuple[Skill, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("equipment_id", self.equipment_id)
        _require_non_empty("name", self.name)
        _validate_slot(self.slot)
        if not isinstance(self.stat_modifier, CombatStatModifier):
            raise TypeError("stat_modifier must be CombatStatModifier")
        object.__setattr__(self, "skills", tuple(self.skills))
        for skill in self.skills:
            if not isinstance(skill, Skill):
                raise TypeError("skills must be Skill")


@dataclass(frozen=True, slots=True)
class EquipmentInstance:
    """背包中的一件装备实例。"""

    instance_id: str
    template_id: str

    def __post_init__(self) -> None:
        _require_non_empty("instance_id", self.instance_id)
        _require_non_empty("template_id", self.template_id)


@dataclass(frozen=True, slots=True)
class EquippedItem:
    """装备在某个槽位上的实例引用。"""

    slot: EquipmentSlot
    instance_id: str

    def __post_init__(self) -> None:
        _validate_slot(self.slot)
        _require_non_empty("instance_id", self.instance_id)


@dataclass(frozen=True, slots=True)
class EquipmentLoadout:
    """一个角色当前穿戴的装备。"""

    items: tuple[EquippedItem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        seen_slots: set[EquipmentSlot] = set()
        for item in self.items:
            if not isinstance(item, EquippedItem):
                raise TypeError("items must be EquippedItem")
            if item.slot in seen_slots:
                raise ValueError(f"duplicate equipment slot: {item.slot}")
            seen_slots.add(item.slot)
        _validate_hand_slots(seen_slots)

    def equipped_instance_ids(self) -> tuple[str, ...]:
        """返回当前穿戴的装备实例 id。"""

        return tuple(item.instance_id for item in self.items)


def combine_equipment_modifier(
    equipment: Iterable[EquipmentTemplate],
) -> CombatStatModifier:
    """合并一组装备的属性加成。"""

    total = CombatStatModifier()
    seen_slots: set[EquipmentSlot] = set()
    for item in equipment:
        if not isinstance(item, EquipmentTemplate):
            raise TypeError("equipment must contain EquipmentTemplate")
        if item.slot in seen_slots:
            raise ValueError(f"duplicate equipment slot: {item.slot}")
        seen_slots.add(item.slot)
        total += item.stat_modifier
    _validate_hand_slots(seen_slots)
    return total


def apply_equipment_stats(
    base_stats: CombatStats,
    equipment: Iterable[EquipmentTemplate],
) -> CombatStats:
    """把装备属性加成应用到基础战斗属性上。"""

    return apply_stat_modifier(base_stats, combine_equipment_modifier(equipment))


def combine_equipment_skills(
    base_skills: Iterable[Skill],
    equipment: Iterable[EquipmentTemplate],
) -> tuple[Skill, ...]:
    """合并角色基础技能和装备附带技能。"""

    skills = list(base_skills)
    seen_slots: set[EquipmentSlot] = set()
    for item in equipment:
        if not isinstance(item, EquipmentTemplate):
            raise TypeError("equipment must contain EquipmentTemplate")
        if item.slot in seen_slots:
            raise ValueError(f"duplicate equipment slot: {item.slot}")
        seen_slots.add(item.slot)
        skills.extend(item.skills)
    _validate_hand_slots(seen_slots)
    return tuple(skills)


def _validate_slot(slot: str) -> None:
    if slot not in EQUIPMENT_SLOTS:
        raise ValueError(f"unknown equipment slot: {slot}")


def _validate_hand_slots(slots: set[EquipmentSlot]) -> None:
    if "two_hand" in slots and ("main_hand" in slots or "off_hand" in slots):
        raise ValueError("two_hand equipment cannot be combined with main_hand or off_hand")


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    if not value:
        raise ValueError(f"{name} must not be empty")
