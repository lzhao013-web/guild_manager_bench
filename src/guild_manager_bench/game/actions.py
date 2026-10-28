from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from guild_manager_bench.game.equipment import EQUIPMENT_SLOTS, EquipmentSlot


@dataclass(frozen=True, slots=True)
class CraftAction:
    """合成一个装备配方。"""

    recipe_id: str

    def __post_init__(self) -> None:
        _require_non_empty("recipe_id", self.recipe_id)


@dataclass(frozen=True, slots=True)
class ExchangeMaterialsAction:
    """按固定费用兑换指定数量的一种材料。"""

    material_id: str
    quantity: int

    def __post_init__(self) -> None:
        _require_non_empty("material_id", self.material_id)
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity < 1:
            raise ValueError("quantity 必须为正整数")


@dataclass(frozen=True, slots=True)
class PurchaseUpgradeAction:
    """购买一个全局加成。"""

    upgrade_id: str

    def __post_init__(self) -> None:
        _require_non_empty("upgrade_id", self.upgrade_id)


@dataclass(frozen=True, slots=True)
class AllocateExperienceAction:
    """从经验池分配经验给一个冒险者。"""

    adventurer_id: str
    amount: int

    def __post_init__(self) -> None:
        _require_non_empty("adventurer_id", self.adventurer_id)
        _require_at_least("amount", self.amount, 0)


@dataclass(frozen=True, slots=True)
class RecruitAction:
    """招募一个候选冒险者。"""

    candidate_id: str

    def __post_init__(self) -> None:
        _require_non_empty("candidate_id", self.candidate_id)


@dataclass(frozen=True, slots=True)
class DismissAction:
    """解散一个冒险者，腾出队伍名额；装备归还库存，经验返还经验池。"""

    adventurer_id: str

    def __post_init__(self) -> None:
        _require_non_empty("adventurer_id", self.adventurer_id)


@dataclass(frozen=True, slots=True)
class EquipAction:
    """把一件装备穿戴到指定冒险者身上。"""

    adventurer_id: str
    equipment_instance_id: str

    def __post_init__(self) -> None:
        _require_non_empty("adventurer_id", self.adventurer_id)
        _require_non_empty("equipment_instance_id", self.equipment_instance_id)


@dataclass(frozen=True, slots=True)
class UnequipAction:
    """卸下指定冒险者某个槽位上的装备。"""

    adventurer_id: str
    slot: EquipmentSlot

    def __post_init__(self) -> None:
        _require_non_empty("adventurer_id", self.adventurer_id)
        if self.slot not in EQUIPMENT_SLOTS:
            raise ValueError(f"unknown equipment slot: {self.slot}")


@dataclass(frozen=True, slots=True)
class ConfigureTacticsAction:
    """替换角色的主动技能优先顺序和停用列表。"""

    adventurer_id: str
    skill_priority: tuple[str, ...]
    disabled_skills: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty("adventurer_id", self.adventurer_id)
        for name in ("skill_priority", "disabled_skills"):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)):
                raise ValueError(f"{name} 必须是技能 ID 列表")
            for value in values:
                _require_non_empty(name, value)
            if len(set(values)) != len(values):
                raise ValueError(f"{name} 不能包含重复技能")
            object.__setattr__(self, name, tuple(values))
        if set(self.skill_priority) & set(self.disabled_skills):
            raise ValueError("同一个技能不能同时排序和停用")


@dataclass(frozen=True, slots=True)
class RefreshMonstersAction:
    """付费刷新本回合未锁定的普通怪物。"""

    locked_monster_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.locked_monster_ids, (list, tuple)):
            raise ValueError("locked_monster_ids 必须是列表")
        for monster_id in self.locked_monster_ids:
            _require_non_empty("locked_monster_id", monster_id)
        if len(set(self.locked_monster_ids)) != len(self.locked_monster_ids):
            raise ValueError("锁定怪物不能重复")
        object.__setattr__(self, "locked_monster_ids", tuple(self.locked_monster_ids))


@dataclass(frozen=True, slots=True)
class HuntAction:
    """指定一个冒险者与一个怪物交战。"""

    adventurer_id: str
    monster_id: str

    def __post_init__(self) -> None:
        _require_non_empty("adventurer_id", self.adventurer_id)
        _require_non_empty("monster_id", self.monster_id)


PreparationAction: TypeAlias = (
    CraftAction
    | PurchaseUpgradeAction
    | AllocateExperienceAction
    | RecruitAction
    | DismissAction
    | UnequipAction
    | EquipAction
    | ConfigureTacticsAction
    | RefreshMonstersAction
    | ExchangeMaterialsAction
)
PREPARATION_ACTION_TYPES = (
    CraftAction,
    PurchaseUpgradeAction,
    AllocateExperienceAction,
    RecruitAction,
    DismissAction,
    UnequipAction,
    EquipAction,
    ConfigureTacticsAction,
    RefreshMonstersAction,
    ExchangeMaterialsAction,
)


@dataclass(frozen=True, slots=True)
class EndTurnAction:
    """提交交战列表并结束当前回合。"""

    hunts: tuple[HuntAction, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "hunts", tuple(self.hunts))
        _validate_tuple_items("hunts", self.hunts, HuntAction)


@dataclass(frozen=True, slots=True)
class TurnAction:
    """按给定顺序执行回合内操作，然后提交交战列表结束回合。"""

    operations: tuple[PreparationAction, ...] = ()
    hunts: tuple[HuntAction, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "operations", tuple(self.operations))
        object.__setattr__(self, "hunts", tuple(self.hunts))
        _validate_tuple_items("operations", self.operations, PREPARATION_ACTION_TYPES)
        _validate_tuple_items("hunts", self.hunts, HuntAction)


def _validate_tuple_items(
    name: str,
    values: tuple[object, ...],
    item_type: type | tuple[type, ...],
) -> None:
    for value in values:
        if not isinstance(value, item_type):
            raise TypeError(f"{name} contains invalid item type")


def _require_at_least(name: str, value: int, minimum: int) -> None:
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    if not value:
        raise ValueError(f"{name} must not be empty")
