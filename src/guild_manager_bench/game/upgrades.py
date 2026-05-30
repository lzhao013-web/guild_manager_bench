from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from guild_manager_bench.game.models import (
    CombatStatModifier,
    CombatStats,
    apply_stat_modifier,
)
from guild_manager_bench.game.skills import Skill


class UpgradeError(ValueError):
    """全局加成购买失败。"""


@dataclass(frozen=True, slots=True)
class GlobalUpgrade:
    """全局加成定义。

    全局加成花费金币解锁一次；解锁后可为所有适用角色提供属性加成和技能。
    """

    upgrade_id: str
    name: str
    gold_cost: int
    description: str = ""
    stat_modifier: CombatStatModifier = field(default_factory=CombatStatModifier)
    skills: tuple[Skill, ...] = ()
    required_upgrade_ids: tuple[str, ...] = ()
    party_size_bonus: int = 0

    def __post_init__(self) -> None:
        _require_non_empty("upgrade_id", self.upgrade_id)
        _require_non_empty("name", self.name)
        _require_at_least("gold_cost", self.gold_cost, 0)
        _require_at_least("party_size_bonus", self.party_size_bonus, 0)
        if not isinstance(self.stat_modifier, CombatStatModifier):
            raise TypeError("stat_modifier must be CombatStatModifier")

        object.__setattr__(self, "skills", tuple(self.skills))
        for skill in self.skills:
            if not isinstance(skill, Skill):
                raise TypeError("skills must be Skill")

        object.__setattr__(self, "required_upgrade_ids", tuple(self.required_upgrade_ids))
        seen_required_ids: set[str] = set()
        for required_id in self.required_upgrade_ids:
            _require_non_empty("required_upgrade_id", required_id)
            if required_id in seen_required_ids:
                raise ValueError(f"duplicate required upgrade: {required_id}")
            if required_id == self.upgrade_id:
                raise ValueError("upgrade cannot require itself")
            seen_required_ids.add(required_id)


@dataclass(frozen=True, slots=True)
class UpgradeInventory:
    """购买全局加成所需的资源视图。"""

    gold: int = 0
    unlocked_upgrade_ids: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        _require_at_least("gold", self.gold, 0)
        unlocked_ids = frozenset(self.unlocked_upgrade_ids)
        for upgrade_id in unlocked_ids:
            _require_non_empty("unlocked_upgrade_id", upgrade_id)
        object.__setattr__(self, "unlocked_upgrade_ids", unlocked_ids)


@dataclass(frozen=True, slots=True)
class UpgradePurchaseResult:
    """一次全局加成购买结果。"""

    upgrade_id: str
    inventory: UpgradeInventory


def can_purchase_upgrade(upgrade: GlobalUpgrade, inventory: UpgradeInventory) -> bool:
    """判断当前资源是否可以购买指定全局加成。"""

    _validate_upgrade_and_inventory(upgrade, inventory)
    return not missing_upgrade_requirements(upgrade, inventory)


def missing_upgrade_requirements(
    upgrade: GlobalUpgrade,
    inventory: UpgradeInventory,
) -> dict[str, int | tuple[str, ...]]:
    """返回购买全局加成缺少的条件。

    金币不足时返回 `gold`，前置加成不足时返回 `required_upgrade_ids`。
    """

    _validate_upgrade_and_inventory(upgrade, inventory)
    missing: dict[str, int | tuple[str, ...]] = {}

    if upgrade.upgrade_id in inventory.unlocked_upgrade_ids:
        missing["already_unlocked"] = (upgrade.upgrade_id,)

    if inventory.gold < upgrade.gold_cost:
        missing["gold"] = upgrade.gold_cost - inventory.gold

    missing_required_ids = tuple(
        required_id
        for required_id in upgrade.required_upgrade_ids
        if required_id not in inventory.unlocked_upgrade_ids
    )
    if missing_required_ids:
        missing["required_upgrade_ids"] = missing_required_ids

    return missing


def purchase_upgrade(
    upgrade: GlobalUpgrade,
    inventory: UpgradeInventory,
) -> UpgradePurchaseResult:
    """购买全局加成，返回新的资源视图。"""

    _validate_upgrade_and_inventory(upgrade, inventory)
    missing = missing_upgrade_requirements(upgrade, inventory)
    if missing:
        raise UpgradeError(f"cannot purchase upgrade: {missing}")

    new_inventory = UpgradeInventory(
        gold=inventory.gold - upgrade.gold_cost,
        unlocked_upgrade_ids=inventory.unlocked_upgrade_ids | {upgrade.upgrade_id},
    )
    return UpgradePurchaseResult(upgrade_id=upgrade.upgrade_id, inventory=new_inventory)


def combine_upgrade_modifier(upgrades: Iterable[GlobalUpgrade]) -> CombatStatModifier:
    """合并多个已解锁全局加成的属性修正。"""

    total = CombatStatModifier()
    seen_upgrade_ids: set[str] = set()
    for upgrade in upgrades:
        if not isinstance(upgrade, GlobalUpgrade):
            raise TypeError("upgrades must contain GlobalUpgrade")
        if upgrade.upgrade_id in seen_upgrade_ids:
            raise ValueError(f"duplicate upgrade: {upgrade.upgrade_id}")
        seen_upgrade_ids.add(upgrade.upgrade_id)
        total += upgrade.stat_modifier
    return total


def apply_upgrade_stats(
    base_stats: CombatStats,
    upgrades: Iterable[GlobalUpgrade],
) -> CombatStats:
    """把全局加成属性修正应用到基础战斗属性上。"""

    return apply_stat_modifier(base_stats, combine_upgrade_modifier(upgrades))


def combine_upgrade_skills(
    base_skills: Iterable[Skill],
    upgrades: Iterable[GlobalUpgrade],
) -> tuple[Skill, ...]:
    """合并角色基础技能和全局加成授予的技能。"""

    skills = list(base_skills)
    seen_upgrade_ids: set[str] = set()
    for upgrade in upgrades:
        if not isinstance(upgrade, GlobalUpgrade):
            raise TypeError("upgrades must contain GlobalUpgrade")
        if upgrade.upgrade_id in seen_upgrade_ids:
            raise ValueError(f"duplicate upgrade: {upgrade.upgrade_id}")
        seen_upgrade_ids.add(upgrade.upgrade_id)
        skills.extend(upgrade.skills)
    return tuple(skills)


def combine_party_size_bonus(upgrades: Iterable[GlobalUpgrade]) -> int:
    """合并多个已解锁全局加成提供的队伍人数上限。"""

    total = 0
    seen_upgrade_ids: set[str] = set()
    for upgrade in upgrades:
        if not isinstance(upgrade, GlobalUpgrade):
            raise TypeError("upgrades must contain GlobalUpgrade")
        if upgrade.upgrade_id in seen_upgrade_ids:
            raise ValueError(f"duplicate upgrade: {upgrade.upgrade_id}")
        seen_upgrade_ids.add(upgrade.upgrade_id)
        total += upgrade.party_size_bonus
    return total


def _validate_upgrade_and_inventory(
    upgrade: GlobalUpgrade,
    inventory: UpgradeInventory,
) -> None:
    if not isinstance(upgrade, GlobalUpgrade):
        raise TypeError("upgrade must be GlobalUpgrade")
    if not isinstance(inventory, UpgradeInventory):
        raise TypeError("inventory must be UpgradeInventory")


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
