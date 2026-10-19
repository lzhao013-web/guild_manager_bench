import type { Observation } from "./types";
import type { Selection } from "./sceneModel";
import { subjectId, subjects } from "./sceneModel";

export type SceneModule = "party" | "monsters" | "inventory" | "recruitment" | "upgrades";
export type RunModule = SceneModule | "actions" | "models";
export interface WatchFocus {
    module: RunModule;
    selection?: Selection;
}
export const MODULE_NAMES: Record<RunModule, string> = {
    party: "编队",
    monsters: "怪物",
    inventory: "装备库",
    recruitment: "招募",
    upgrades: "公会发展",
    actions: "行动时间线",
    models: "模型输出",
};
export const KIND_MODULE: Record<Selection["kind"], SceneModule> = {
    member: "party",
    monster: "monsters",
    equipment: "inventory",
    recipe: "inventory",
    candidate: "recruitment",
    upgrade: "upgrades",
};
export const MODULE_KIND: Record<SceneModule, Selection["kind"]> = {
    party: "member",
    monsters: "monster",
    inventory: "equipment",
    recruitment: "candidate",
    upgrades: "upgrade",
};
export interface SceneView {
    selection: Selection | null;
    search: string;
    slot: string;
    stock: string;
    inventoryMode: "items" | "recipes";
}
export const emptySceneView: SceneView = { selection: null, search: "", slot: "all", stock: "all", inventoryMode: "items" };
export type SceneViews = Partial<Record<SceneModule, SceneView>>;
export function openSceneView(
    views: SceneViews,
    module: SceneModule,
    observation: Observation | null,
    selection?: Selection,
    slot?: string,
): SceneViews {
    const previous = views[module];
    const kind = MODULE_KIND[module];
    const first = observation ? subjects(observation, kind)?.[0] : undefined;
    const next = previous || {
        ...emptySceneView,
        selection: first && module === "party" ? { kind, id: subjectId(kind, first) } : null,
    };
    return {
        ...views,
        [module]: {
            ...next,
            ...(selection
                ? {
                      selection,
                      ...(module === "inventory" ? { search: "", slot: "all", stock: "all", inventoryMode: selection.kind === "recipe" ? "recipes" as const : "items" as const } : {}),
                  }
                : {}),
            ...(slot !== undefined ? { slot, stock: "all", search: "", selection: null, inventoryMode: "items" as const } : {}),
        },
    };
}
