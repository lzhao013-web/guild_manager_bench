from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from guild_manager_bench.game.equipment import EquipmentInstance


class CraftingError(ValueError):
    """合成失败。"""


@dataclass(frozen=True, slots=True)
class MaterialCost:
    """一项材料消耗。"""

    material_id: str
    quantity: int

    def __post_init__(self) -> None:
        _require_non_empty("material_id", self.material_id)
        _require_at_least("quantity", self.quantity, 1)


@dataclass(frozen=True, slots=True)
class CraftingRecipe:
    """装备合成配方。"""

    recipe_id: str
    name: str
    output_template_id: str
    material_costs: tuple[MaterialCost, ...] = ()
    gold_cost: int = 0

    def __post_init__(self) -> None:
        _require_non_empty("recipe_id", self.recipe_id)
        _require_non_empty("name", self.name)
        _require_non_empty("output_template_id", self.output_template_id)
        _require_at_least("gold_cost", self.gold_cost, 0)
        object.__setattr__(self, "material_costs", tuple(self.material_costs))

        seen_materials: set[str] = set()
        for cost in self.material_costs:
            if not isinstance(cost, MaterialCost):
                raise TypeError("material_costs must be MaterialCost")
            if cost.material_id in seen_materials:
                raise ValueError(f"duplicate material cost: {cost.material_id}")
            seen_materials.add(cost.material_id)

    @classmethod
    def from_mapping(
        cls,
        *,
        recipe_id: str,
        name: str,
        output_template_id: str,
        material_costs: Mapping[str, int],
        gold_cost: int = 0,
    ) -> CraftingRecipe:
        """从材料映射创建配方，方便后续对接 YAML。"""

        return cls(
            recipe_id=recipe_id,
            name=name,
            output_template_id=output_template_id,
            material_costs=tuple(
                MaterialCost(material_id=material_id, quantity=quantity)
                for material_id, quantity in material_costs.items()
            ),
            gold_cost=gold_cost,
        )


@dataclass(frozen=True, slots=True)
class CraftingInventory:
    """合成所需的背包资源视图。"""

    gold: int = 0
    materials: Mapping[str, int] = field(default_factory=dict)
    equipment: tuple[EquipmentInstance, ...] = ()

    def __post_init__(self) -> None:
        _require_at_least("gold", self.gold, 0)
        object.__setattr__(self, "materials", _freeze_materials(self.materials))
        object.__setattr__(self, "equipment", tuple(self.equipment))

        seen_instance_ids: set[str] = set()
        for item in self.equipment:
            if not isinstance(item, EquipmentInstance):
                raise TypeError("equipment must be EquipmentInstance")
            if item.instance_id in seen_instance_ids:
                raise ValueError(f"duplicate equipment instance: {item.instance_id}")
            seen_instance_ids.add(item.instance_id)


@dataclass(frozen=True, slots=True)
class CraftingResult:
    """一次合成的结算结果。"""

    recipe_id: str
    equipment: EquipmentInstance
    inventory: CraftingInventory


def can_craft(recipe: CraftingRecipe, inventory: CraftingInventory) -> bool:
    """判断当前资源是否足够执行配方。"""

    _validate_recipe_and_inventory(recipe, inventory)
    return not missing_requirements(recipe, inventory)


def missing_requirements(
    recipe: CraftingRecipe,
    inventory: CraftingInventory,
) -> dict[str, int]:
    """返回缺少的资源数量。

    金币不足时使用 `gold` 作为 key；材料不足时使用材料 id 作为 key。
    """

    _validate_recipe_and_inventory(recipe, inventory)
    missing: dict[str, int] = {}

    if inventory.gold < recipe.gold_cost:
        missing["gold"] = recipe.gold_cost - inventory.gold

    for cost in recipe.material_costs:
        available = inventory.materials.get(cost.material_id, 0)
        if available < cost.quantity:
            missing[cost.material_id] = cost.quantity - available

    return missing


def craft_equipment(
    recipe: CraftingRecipe,
    inventory: CraftingInventory,
    *,
    instance_id: str,
) -> CraftingResult:
    """执行装备合成，返回新的背包状态。"""

    _validate_recipe_and_inventory(recipe, inventory)
    _require_non_empty("instance_id", instance_id)
    if any(item.instance_id == instance_id for item in inventory.equipment):
        raise CraftingError(f"duplicate equipment instance: {instance_id}")

    missing = missing_requirements(recipe, inventory)
    if missing:
        raise CraftingError(f"not enough resources: {missing}")

    materials = dict(inventory.materials)
    for cost in recipe.material_costs:
        materials[cost.material_id] -= cost.quantity

    equipment = EquipmentInstance(
        instance_id=instance_id,
        template_id=recipe.output_template_id,
    )
    new_inventory = CraftingInventory(
        gold=inventory.gold - recipe.gold_cost,
        materials=materials,
        equipment=inventory.equipment + (equipment,),
    )
    return CraftingResult(
        recipe_id=recipe.recipe_id,
        equipment=equipment,
        inventory=new_inventory,
    )


def _freeze_materials(materials: Mapping[str, int]) -> Mapping[str, int]:
    frozen_materials: dict[str, int] = {}
    for material_id, quantity in materials.items():
        _require_non_empty("material_id", material_id)
        _require_at_least(f"materials[{material_id}]", quantity, 0)
        frozen_materials[material_id] = quantity
    return MappingProxyType(frozen_materials)


def _validate_recipe_and_inventory(
    recipe: CraftingRecipe,
    inventory: CraftingInventory,
) -> None:
    if not isinstance(recipe, CraftingRecipe):
        raise TypeError("recipe must be CraftingRecipe")
    if not isinstance(inventory, CraftingInventory):
        raise TypeError("inventory must be CraftingInventory")


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
