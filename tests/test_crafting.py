import pytest

from guild_manager_bench.game.crafting import (
    CraftingError,
    CraftingInventory,
    CraftingRecipe,
    MaterialCost,
    can_craft,
    craft_equipment,
    missing_requirements,
)
from guild_manager_bench.game.equipment import EquipmentInstance


def test_can_craft_when_gold_and_materials_are_enough() -> None:
    recipe = CraftingRecipe.from_mapping(
        recipe_id="iron_sword_recipe",
        name="铁剑配方",
        output_template_id="iron_sword",
        material_costs={"iron_ore": 3, "wood": 1},
        gold_cost=10,
    )
    inventory = CraftingInventory(
        gold=12,
        materials={"iron_ore": 3, "wood": 2},
    )

    assert can_craft(recipe, inventory)


def test_missing_requirements_reports_gold_and_material_shortage() -> None:
    recipe = CraftingRecipe.from_mapping(
        recipe_id="iron_sword_recipe",
        name="铁剑配方",
        output_template_id="iron_sword",
        material_costs={"iron_ore": 3, "wood": 1},
        gold_cost=10,
    )
    inventory = CraftingInventory(
        gold=7,
        materials={"iron_ore": 1},
    )

    assert missing_requirements(recipe, inventory) == {
        "gold": 3,
        "iron_ore": 2,
        "wood": 1,
    }
    assert not can_craft(recipe, inventory)


def test_craft_equipment_consumes_resources_and_adds_instance() -> None:
    recipe = CraftingRecipe.from_mapping(
        recipe_id="iron_sword_recipe",
        name="铁剑配方",
        output_template_id="iron_sword",
        material_costs={"iron_ore": 3, "wood": 1},
        gold_cost=10,
    )
    inventory = CraftingInventory(
        gold=12,
        materials={"iron_ore": 5, "wood": 1},
    )

    result = craft_equipment(recipe, inventory, instance_id="eq_001")

    assert result.recipe_id == "iron_sword_recipe"
    assert result.equipment == EquipmentInstance(instance_id="eq_001", template_id="iron_sword")
    assert result.inventory.gold == 2
    assert dict(result.inventory.materials) == {"iron_ore": 2, "wood": 0}
    assert result.inventory.equipment == (result.equipment,)


def test_craft_equipment_does_not_mutate_input_inventory() -> None:
    recipe = CraftingRecipe.from_mapping(
        recipe_id="iron_sword_recipe",
        name="铁剑配方",
        output_template_id="iron_sword",
        material_costs={"iron_ore": 3},
        gold_cost=10,
    )
    inventory = CraftingInventory(
        gold=12,
        materials={"iron_ore": 5},
    )

    craft_equipment(recipe, inventory, instance_id="eq_001")

    assert inventory.gold == 12
    assert dict(inventory.materials) == {"iron_ore": 5}
    assert inventory.equipment == ()


def test_craft_equipment_rejects_insufficient_resources() -> None:
    recipe = CraftingRecipe.from_mapping(
        recipe_id="iron_sword_recipe",
        name="铁剑配方",
        output_template_id="iron_sword",
        material_costs={"iron_ore": 3},
        gold_cost=10,
    )
    inventory = CraftingInventory(
        gold=12,
        materials={"iron_ore": 2},
    )

    with pytest.raises(CraftingError):
        craft_equipment(recipe, inventory, instance_id="eq_001")


def test_craft_equipment_rejects_duplicate_instance_id() -> None:
    recipe = CraftingRecipe.from_mapping(
        recipe_id="iron_sword_recipe",
        name="铁剑配方",
        output_template_id="iron_sword",
        material_costs={"iron_ore": 3},
        gold_cost=10,
    )
    inventory = CraftingInventory(
        gold=12,
        materials={"iron_ore": 3},
        equipment=(EquipmentInstance(instance_id="eq_001", template_id="old_sword"),),
    )

    with pytest.raises(CraftingError):
        craft_equipment(recipe, inventory, instance_id="eq_001")


def test_recipe_rejects_duplicate_material_costs() -> None:
    with pytest.raises(ValueError):
        CraftingRecipe(
            recipe_id="bad_recipe",
            name="错误配方",
            output_template_id="bad_item",
            material_costs=(
                MaterialCost(material_id="iron_ore", quantity=1),
                MaterialCost(material_id="iron_ore", quantity=2),
            ),
        )


def test_inventory_rejects_negative_material_quantity() -> None:
    with pytest.raises(ValueError):
        CraftingInventory(gold=0, materials={"iron_ore": -1})
