import { fmt } from "./model";
import { prioritizeRecipes, prioritizeUpgrades } from "./previewOrder";
import { recentActions } from "./actionModel";
import { materialText, requirementLines, signed, STAT_NAMES } from "./sceneModel";
import { talentState, TALENT_STATES } from "./talentModel";
import type { Talent } from "./talentModel";
import type { GameEntity, Observation } from "./types";
import type { WatchFocus } from "./sceneNavigation";
import type { WatchFrame } from "./watchModel";

export interface BusinessRow {
    id: string;
    name: string;
    value: string;
    detail: string;
    target: WatchFocus;
    portrait?: string;
    slot?: string;
    equipmentTemplateId?: string;
    available: boolean;
}
export interface BusinessGroup {
    id: "recruitment" | "upgrades" | "equipment";
    title: string;
    note: string;
    target: WatchFocus;
    rows: BusinessRow[];
}
export interface Opportunity {
    label: string;
    count: number | null;
    target: WatchFocus;
}
const statsText = (stats: Record<string, number> | undefined) => Object.entries(stats || {})
    .filter(([, value]) => value !== 0).slice(0, 3).map(([key, value]) => `${STAT_NAMES[key] || key} ${signed(value)}`).join("、");
function upgradeEffect(entity: GameEntity): string {
    if (typeof entity.description === "string" && entity.description) return entity.description;
    return [statsText(entity.stats as Record<string, number> | undefined),
        ...(typeof entity.party_size_bonus === "number" && entity.party_size_bonus !== 0 ? [`编队容量 ${signed(entity.party_size_bonus)}`] : []),
        ...((entity.skills as GameEntity[] | undefined)?.map(skill => skill.name) || []),
    ].filter(Boolean).join("、");
}
export function purchaseCost(entity: GameEntity): string {
    return [
        `金币 ${fmt(entity.gold_cost as number | undefined)}`,
        ...(typeof entity.experience_cost === "number" && entity.experience_cost > 0 ? [`经验 ${fmt(entity.experience_cost)}`] : []),
        ...(entity.material_costs && Object.keys(entity.material_costs as object).length ? [materialText(entity.material_costs as Record<string, number>)] : []),
    ].join(" · ");
}

/** 资格、价格和缺口均来自所看快照，不按猜测的强弱或性价比排序。 */
export function businessPreviews(observation: Observation | null) {
    const candidates = observation?.recruit_candidates;
    const upgrades = observation?.global_upgrades;
    const inventory = observation?.equipment_inventory;
    const recipes = observation?.crafting_recipes;
    const recruitable = candidates?.filter(item => item.can_recruit === true).length ?? null;
    const unlockable = upgrades?.filter(item => !item.unlocked && item.can_purchase === true).length ?? null;
    const stored = inventory?.filter(item => item.equipped_by === null);
    const craftable = recipes?.filter(item => item.can_craft).length ?? null;
    const orderedRecipes = prioritizeRecipes(recipes || []);
    const firstRecipe = orderedRecipes[0];
    const recipeTarget: WatchFocus = firstRecipe ? { module: "inventory", selection: { kind: "recipe", id: firstRecipe.recipe_id } } : { module: "inventory" };
    const opportunities: Opportunity[] = [
        { label: "招募", count: recruitable, target: { module: "recruitment" } },
        { label: "天赋", count: unlockable, target: { module: "upgrades" } },
        { label: "闲装", count: stored?.length ?? null, target: { module: "inventory" } },
        { label: "配方", count: craftable, target: recipeTarget },
    ];
    const groups: BusinessGroup[] = [];
    if (candidates?.length && observation && observation.adventurers.length < observation.party_size_limit) groups.push({
        id: "recruitment", title: "招募候选", note: `${recruitable} 可招募 / ${candidates.length} 位`, target: { module: "recruitment" },
        rows: [...candidates].sort((a, b) => Number(b.can_recruit === true) - Number(a.can_recruit === true)).slice(0, 8).map(item => ({
            id: item.candidate_id as string, name: item.name!, value: `金币 ${fmt(item.recruit_gold as number)}`,
            detail: [item.can_recruit === true ? "当前满足招募条件" : requirementLines(item.missing as Record<string, unknown> | undefined).join(" · ") || "条件未记录", statsText(item.base_stats as Record<string, number> | undefined)].filter(Boolean).join(" · "),
            target: { module: "recruitment", selection: { kind: "candidate", id: item.candidate_id as string } },
            portrait: item.template_id, available: item.can_recruit === true,
        })),
    });
    if (upgrades?.length) {
        const locked = upgrades.filter(item => !item.unlocked);
        groups.push({
            id: "upgrades", title: "天赋与升级", note: `${unlockable} 可解锁`, target: { module: "upgrades" },
            rows: prioritizeUpgrades(locked.length ? locked : upgrades).slice(0, 8).map(item => {
                const state = talentState(item as Talent);
                const missing = requirementLines(item.missing as Record<string, unknown> | undefined, upgrades);
                return {
                    id: item.upgrade_id as string, name: item.name!, value: TALENT_STATES[state],
                    detail: [purchaseCost(item), upgradeEffect(item), ...missing].filter(Boolean).join(" · "),
                    target: { module: "upgrades", selection: { kind: "upgrade", id: item.upgrade_id as string } }, available: state === "available",
                };
            }),
        });
    }
    const items: BusinessRow[] = (stored || []).slice(0, 8).map(item => ({
        id: `item:${item.instance_id}`, name: item.name!, value: "未穿戴", detail: statsText(item.stats as Record<string, number> | undefined),
        target: { module: "inventory", selection: { kind: "equipment", id: item.instance_id! } }, slot: item.slot, equipmentTemplateId: item.template_id, available: true,
    }));
    const recipeRows: BusinessRow[] = orderedRecipes.slice(0, 8).map(item => ({
        id: `recipe:${item.recipe_id}`, name: item.output_name, value: item.can_craft ? "可制作" : "缺资源",
        detail: [purchaseCost(item), statsText(item.output_stats), ...requirementLines(item.missing)].filter(Boolean).join(" · "),
        target: { module: "inventory", selection: { kind: "recipe", id: item.recipe_id } }, slot: item.output_slot, equipmentTemplateId: item.output_template_id, available: item.can_craft,
    }));
    // 可制作的高费用配方优先占摘要，闲装仍有独立计数入口。
    const equipment = [...recipeRows.filter(row => row.available), ...items, ...recipeRows.filter(row => !row.available)].slice(0, 8);
    if (equipment.length) groups.push({ id: "equipment", title: "装备与制作", note: `${stored?.length ?? "未记录"} 闲装 · ${craftable ?? "未记录"} 可制作`, target: { module: "inventory" }, rows: equipment });
    return { groups, opportunities };
}

export function previewProgress(frames: WatchFrame[], frame: WatchFrame | undefined) {
    const visible = frame ? frames.slice(0, frames.indexOf(frame) + 1) : [];
    const recent = recentActions(visible);
    const actions = recent.actions;
    const query = recent.queries.at(-1);
    const queries = recent.queries.length;
    return { actions, query, queries, queryOnly: recent.queryOnly };
}
