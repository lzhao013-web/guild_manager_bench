import type { CraftingRecipe, GameEntity } from "./types";

/** 配方没有独立阶级字段，只按真实制作资格与当前金币费用排序。 */
export function prioritizeRecipes(recipes: CraftingRecipe[]): CraftingRecipe[] {
    return [...recipes].sort((a, b) => Number(b.can_craft) - Number(a.can_craft) || b.gold_cost - a.gold_cost);
}
export function prioritizeUpgrades(upgrades: GameEntity[]): GameEntity[] {
    return [...upgrades].sort((a, b) =>
        Number(b.can_purchase === true && !b.unlocked) - Number(a.can_purchase === true && !a.unlocked) ||
        ((b.tier as number | undefined) ?? 0) - ((a.tier as number | undefined) ?? 0) ||
        (b.gold_cost as number) - (a.gold_cost as number));
}
