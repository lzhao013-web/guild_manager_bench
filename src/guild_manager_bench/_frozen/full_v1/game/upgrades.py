from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping

from guild_manager_bench._frozen.full_v1.game.crafting import MaterialCost

from guild_manager_bench._frozen.full_v1.game.models import (
    CombatStatModifier,
    CombatStats,
    apply_stat_modifier,
)
from guild_manager_bench._frozen.full_v1.game.skills import Skill


class UpgradeError(ValueError):
    """全局加成购买失败。"""


@dataclass(frozen=True, slots=True)
class GlobalUpgrade:
    """全局加成定义。

    天赋一次性消耗金币、材料和公共经验，可授予属性、技能或经营收益。
    """

    upgrade_id: str
    name: str
    gold_cost: int
    description: str = ""
    stat_modifier: CombatStatModifier = field(default_factory=CombatStatModifier)
    skills: tuple[Skill, ...] = ()
    required_upgrade_ids: tuple[str, ...] = ()
    party_size_bonus: int = 0
    branch: str = "common"
    tier: int = 1
    experience_cost: int = 0
    material_costs: tuple[MaterialCost, ...] = ()
    exclusive_upgrade_ids: tuple[str, ...] = ()
    craft_gold_discount_percent: int = 0
    hunt_experience_bonus_percent: int = 0

    def __post_init__(self) -> None:
        _require_non_empty("upgrade_id", self.upgrade_id)
        _require_non_empty("name", self.name)
        _require_at_least("gold_cost", self.gold_cost, 0)
        _require_at_least("party_size_bonus", self.party_size_bonus, 0)
        _require_non_empty("branch", self.branch)
        _require_at_least("tier", self.tier, 1)
        _require_at_least("experience_cost", self.experience_cost, 0)
        _require_at_least("craft_gold_discount_percent", self.craft_gold_discount_percent, 0)
        if self.craft_gold_discount_percent > 100:
            raise ValueError("craft_gold_discount_percent must be <= 100")
        _require_at_least("hunt_experience_bonus_percent", self.hunt_experience_bonus_percent, 0)
        object.__setattr__(self, "material_costs", tuple(self.material_costs))
        seen_materials: set[str] = set()
        for cost in self.material_costs:
            if not isinstance(cost, MaterialCost):
                raise TypeError("material_costs must contain MaterialCost")
            if cost.material_id in seen_materials:
                raise ValueError(f"duplicate material cost: {cost.material_id}")
            seen_materials.add(cost.material_id)
        object.__setattr__(self, "exclusive_upgrade_ids", tuple(self.exclusive_upgrade_ids))
        seen_exclusive: set[str] = set()
        for upgrade_id in self.exclusive_upgrade_ids:
            _require_non_empty("exclusive_upgrade_id", upgrade_id)
            if upgrade_id == self.upgrade_id or upgrade_id in seen_exclusive:
                raise ValueError(f"invalid exclusive upgrade: {upgrade_id}")
            seen_exclusive.add(upgrade_id)
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
    experience_pool: int = 0
    materials: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_at_least("gold", self.gold, 0)
        _require_at_least("experience_pool", self.experience_pool, 0)
        materials = dict(self.materials)
        for material_id, quantity in materials.items():
            _require_non_empty("material_id", material_id)
            _require_at_least(f"materials[{material_id}]", quantity, 0)
        object.__setattr__(self, "materials", MappingProxyType(materials))
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
) -> dict[str, int | dict[str, int] | tuple[str, ...]]:
    """返回资源缺口、缺少的前置和已选中的互斥天赋。"""

    _validate_upgrade_and_inventory(upgrade, inventory)
    missing: dict[str, int | dict[str, int] | tuple[str, ...]] = {}

    if upgrade.upgrade_id in inventory.unlocked_upgrade_ids:
        missing["already_unlocked"] = (upgrade.upgrade_id,)

    if inventory.gold < upgrade.gold_cost:
        missing["gold"] = upgrade.gold_cost - inventory.gold

    if inventory.experience_pool < upgrade.experience_cost:
        missing["experience"] = upgrade.experience_cost - inventory.experience_pool
    material_deficits = {
        cost.material_id: cost.quantity - inventory.materials.get(cost.material_id, 0)
        for cost in upgrade.material_costs
        if inventory.materials.get(cost.material_id, 0) < cost.quantity
    }
    if material_deficits:
        missing["materials"] = material_deficits
    conflicts = tuple(uid for uid in upgrade.exclusive_upgrade_ids if uid in inventory.unlocked_upgrade_ids)
    if conflicts:
        missing["exclusive_upgrade_ids"] = conflicts

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

    materials = dict(inventory.materials)
    for cost in upgrade.material_costs:
        materials[cost.material_id] -= cost.quantity
    new_inventory = UpgradeInventory(
        gold=inventory.gold - upgrade.gold_cost,
        experience_pool=inventory.experience_pool - upgrade.experience_cost,
        materials=materials,
        unlocked_upgrade_ids=inventory.unlocked_upgrade_ids | {upgrade.upgrade_id},
    )
    return UpgradePurchaseResult(upgrade_id=upgrade.upgrade_id, inventory=new_inventory)


def validate_upgrade_tree(upgrades: tuple[GlobalUpgrade, ...]) -> None:
    """在内容加载边界检查引用、互斥对称性和前置可达性。"""

    by_id = {u.upgrade_id: u for u in upgrades}
    if sum(u.craft_gold_discount_percent for u in upgrades) > 100:
        raise ValueError("combined crafting discount must be <= 100%")
    for upgrade in upgrades:
        for uid in upgrade.required_upgrade_ids + upgrade.exclusive_upgrade_ids:
            if uid not in by_id:
                raise ValueError(f"unknown upgrade reference: {upgrade.upgrade_id} -> {uid}")
        for uid in upgrade.exclusive_upgrade_ids:
            if upgrade.upgrade_id not in by_id[uid].exclusive_upgrade_ids:
                raise ValueError(f"asymmetric upgrade exclusion: {upgrade.upgrade_id} / {uid}")

    ancestors: dict[str, set[str]] = {}
    visiting: set[str] = set()

    def visit(uid: str) -> set[str]:
        if uid in visiting:
            raise ValueError(f"cyclic upgrade requirements: {uid}")
        if uid in ancestors:
            return ancestors[uid]
        visiting.add(uid)
        required: set[str] = set()
        for parent in by_id[uid].required_upgrade_ids:
            required.add(parent)
            required.update(visit(parent))
        path = required | {uid}
        for ancestor in path:
            if path.intersection(by_id[ancestor].exclusive_upgrade_ids):
                raise ValueError(f"incompatible upgrade requirements: {uid}")
        visiting.remove(uid)
        ancestors[uid] = required
        return required

    for uid in by_id:
        visit(uid)


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
