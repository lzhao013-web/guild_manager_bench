import { expect, it } from "vitest";
import { businessPreviews, previewProgress, purchaseCost } from "./businessModel";
import { openSceneView } from "./sceneNavigation";
import { requirementLines } from "./sceneModel";
import { testRecipe } from "./test-recipes";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchRecord } from "./watchModel";

const observation: Observation = {
    gold: 175, experience_pool: 0, materials: { iron_ore: 1 }, turn: 2, max_turns: 8, party_size_limit: 3, adventurers: [],
    recruit_candidates: [
        { candidate_id: "costly", name: "验收昂贵候选", recruit_gold: 200, can_recruit: false, missing: { gold: 25 } },
        { candidate_id: "ready", name: "验收可招募候选", recruit_gold: 60, can_recruit: true, missing: {} },
    ],
    global_upgrades: [{ upgrade_id: "forge", name: "验收铁匠铺", gold_cost: 80, can_purchase: true, unlocked: false, required_upgrade_ids: [], stats: { attack: 4 }, missing: {} }],
    equipment_inventory: [{ instance_id: "boot", name: "验收闲置靴", slot: "boots", stats: { speed: 2 }, equipped_by: null }],
    crafting_recipes: [testRecipe(), testRecipe({ recipe_id: "free", output_name: "验收免费配方", gold_cost: 0, material_costs: {}, can_craft: true, missing: {} })],
};

it("优先预览满足条件的具体对象，并同时保留未穿戴装备与制作配方", () => {
    const result = businessPreviews(observation);
    expect(result.opportunities.map(item => item.count)).toEqual([1, 1, 1, 1]);
    expect(result.groups.map(group => group.id)).toEqual(["recruitment", "upgrades", "equipment"]);
    expect(result.groups[0].rows[0]).toMatchObject({ name: "验收可招募候选", value: "金币 60", target: { module: "recruitment", selection: { id: "ready" } } });
    expect(result.groups[0].rows[1].detail).toBe("缺金币 25");
    expect(result.groups[2].rows.slice(0, 2).map(row => row.target.selection?.kind)).toEqual(["recipe", "equipment"]);
    expect(result.groups[2].rows[0].detail).toContain("金币 0");
    expect(result.groups[1].rows[0].detail).toContain("攻击 +4");
    expect(observation.recruit_candidates![0].candidate_id).toBe("costly");
});

it("编队已满时不重复展开招募卡，把详细预览空间让给升级与装备", () => {
    const full: Observation = { ...observation, party_size_limit: 1,
        adventurers: [{ adventurer_id: "one", name: "验收成员", template_id: "foot_knight", level: 1, resources: { current_hp: 100, current_mp: 0 }, effective_stats: { hp: 100, mp: 0 } }],
        recruit_candidates: observation.recruit_candidates!.map(candidate => ({ ...candidate, can_recruit: false, missing: { party_size_limit: { current: 1, limit: 1 } } })),
    };
    const result = businessPreviews(full);
    expect(result.opportunities[0].count).toBe(0);
    expect(result.groups.map(group => group.id)).toEqual(["upgrades", "equipment"]);
});

it("缺失旧字段不假装为零，也不为已知空列表制造空白预览卡", () => {
    const { recruit_candidates, global_upgrades, equipment_inventory, crafting_recipes, ...older } = observation;
    expect(businessPreviews(older).opportunities.every(item => item.count === null)).toBe(true);
    const empty = businessPreviews({ ...older, recruit_candidates: [], global_upgrades: [], equipment_inventory: [], crafting_recipes: [] });
    expect(empty.groups).toEqual([]);
    expect(empty.opportunities.every(item => item.count === 0)).toBe(true);
});

it("资格只信当前快照，金币增加不会被误认为补齐了材料缺口", () => {
    const result = businessPreviews({ ...observation, gold: 9999, crafting_recipes: [testRecipe()] });
    expect(result.opportunities[3].count).toBe(0);
    expect(result.groups[2].rows.find(row => row.target.selection?.kind === "recipe")).toMatchObject({ value: "缺资源", available: false });
    expect(purchaseCost(testRecipe())).toBe("金币 30 · 铁矿石 2");
});

it("制作的扁平材料缺口和升级的嵌套缺口都显示中文及真实数量", () => {
    expect(requirementLines({ wood: 2, materials: { iron_ore: 1 } })).toEqual(["缺木材 2", "缺材料：铁矿石 1"]);
    const value = businessPreviews({ ...observation, global_upgrades: [{ ...observation.global_upgrades![0], name: "验收扩容", party_size_bonus: 1, stats: {} }] });
    expect(value.groups.find(group => group.id === "upgrades")!.rows[0].detail).toContain("编队容量 +1");
});

it("从配方入口直达装备模块的配方详情，返回库存时清除配方选择", () => {
    const recipe = observation.crafting_recipes![0];
    const view = openSceneView({}, "inventory", observation, { kind: "recipe", id: recipe.recipe_id });
    expect(view.inventory).toMatchObject({ inventoryMode: "recipes", selection: { kind: "recipe", id: recipe.recipe_id } });
    expect(openSceneView(view, "inventory", observation, undefined, "main_hand").inventory).toMatchObject({ inventoryMode: "items", selection: null, slot: "main_hand" });
});

it("经营进展只使用观看位置之前的行动，不再携带局部评分曲线", () => {
    const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-10-18T10:00:00Z", event });
    const frames = watchFrames([rec(1, { type: "turn_started", turn: 2, observation }),
        rec(2, { type: "tool_result", name: "purchase_upgrade", result: { ok: true, event: { summary: "解锁验收升级" } } })]);
    expect(previewProgress(frames, frames[0]).actions).toEqual([]);
    expect(previewProgress(frames, frames[1]).actions[0].title).toBe("解锁验收升级");
    expect(previewProgress(frames, frames[1])).not.toHaveProperty("scores");
});

it("制作优先展示能做且费用最高的配方，昂贵但缺资源的不能挤掉可制作项", () => {
    const result = businessPreviews({ ...observation, crafting_recipes: [
        testRecipe({ recipe_id: "cheap", gold_cost: 20, can_craft: true }),
        testRecipe({ recipe_id: "locked", gold_cost: 900, can_craft: false }),
        testRecipe({ recipe_id: "expensive", gold_cost: 200, can_craft: true }),
    ] });
    const recipes = result.groups.find(group => group.id === "equipment")!.rows.filter(row => row.target.selection?.kind === "recipe");
    expect(recipes.map(row => row.target.selection?.id)).toEqual(["expensive", "cheap", "locked"]);
    expect(result.groups.find(group => group.id === "equipment")!.rows.slice(0, 2).map(row => row.target.selection?.id)).toEqual(["expensive", "cheap"]);
    expect(result.opportunities.find(item => item.label === "配方")!.target.selection?.id).toBe("expensive");
});

it("天赋按可解锁、真实阶级、费用排序，不修改原清单", () => {
    const upgrades = [
        { upgrade_id: "low", name: "低阶", can_purchase: true, tier: 1, gold_cost: 900 },
        { upgrade_id: "high", name: "高阶", can_purchase: true, tier: 3, gold_cost: 100 },
        { upgrade_id: "locked", name: "锁定", can_purchase: false, tier: 4, gold_cost: 1000 },
        { upgrade_id: "high-cost", name: "高阶昂贵", can_purchase: true, tier: 3, gold_cost: 200 },
    ];
    const result = businessPreviews({ ...observation, global_upgrades: upgrades });
    expect(result.groups.find(group => group.id === "upgrades")!.rows.map(row => row.id)).toEqual(["high-cost", "high", "low", "locked"]);
    expect(upgrades[0].upgrade_id).toBe("low");
});
