import type { CraftingRecipe } from "./types";

export function testRecipe(patch: Partial<CraftingRecipe> = {}): CraftingRecipe {
    return {
        recipe_id: "fixture-recipe", name: "验收长剑配方", output_name: "验收长剑", output_slot: "main_hand",
        output_stats: { attack: 4 }, output_skills: [], output_allowed_class_names: [],
        gold_cost: 30, material_costs: { iron_ore: 2 }, can_craft: false,
        missing: { iron_ore: 1 }, ...patch,
    };
}
