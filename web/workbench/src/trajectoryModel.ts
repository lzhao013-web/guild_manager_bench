import { describeAction, isRefreshPreview } from "./actionModel";
import type { Selection } from "./sceneModel";
import type { WatchFrame, WatchPage } from "./watchModel";

export interface TrajectoryRow {
    id: string;
    attempt: number;
    turn: number | null;
    ordinal: number;
    cursor: number;
    at: string;
    lifecycle: "confirmed" | "uncommitted" | "rolled_back";
    subjects: (Selection & { name: string; created?: boolean; ownership?: {
        before: { id: string; name: string } | null;
        after: { id: string; name: string } | null;
    } })[];
    names: Record<string, string>;
    event: Record<string, any> & { type: string };
}
export interface TrajectoryData {
    cursor: number;
    entries: TrajectoryRow[];
    origin: "archive" | "execution";
    warning: string | null;
}
export type TrajectoryPage = WatchPage & { origin: TrajectoryData["origin"]; warning: string | null };
export const LIFECYCLE = { confirmed: "回合已提交", uncommitted: "回合未提交", rolled_back: "已回滚，不再生效" };
export function historyAction(row: TrajectoryRow) {
    const frame: WatchFrame = {
        cursor: row.cursor, record: { cursor: row.cursor, after: 0, at: row.at, event: row.event },
        observation: null, before: null, score: null, call: null,
        failed: row.event.result?.ok === false, title: "", summary: "", names: row.names,
    };
    return describeAction(frame);
}
export function subjectRows(rows: TrajectoryRow[], subject: Selection) {
    return rows.filter(row => row.subjects.some(item => item.kind === subject.kind && item.id === subject.id));
}
export function confirmed(row: TrajectoryRow) {
    return row.lifecycle === "confirmed" && row.event.result?.ok === true;
}
export const OPERATION_CATEGORIES = [
    { id: "recruit", label: "招募", tools: ["recruit_adventurer"] },
    { id: "upgrade", label: "天赋购买", tools: ["purchase_upgrade"] },
    { id: "craft", label: "装备制作", tools: ["craft_equipment"] },
    { id: "equipment", label: "穿戴 / 卸下", tools: ["equip_item", "unequip_item"] },
    { id: "experience", label: "经验分配", tools: ["allocate_experience"] },
    { id: "tactics", label: "战术配置", tools: ["configure_tactics"] },
    { id: "turn", label: "结束回合", tools: ["end_turn"] },
    { id: "other", label: "其它操作", tools: [] },
] as const;
export type OperationCategory = typeof OPERATION_CATEGORIES[number]["id"];
export type TrajectoryState = "all" | "confirmed" | "uncommitted" | "failed" | "rolled_back";
export function operationCategory(event: TrajectoryRow["event"]): OperationCategory | null {
    // 当前索引按工具名前缀过滤查询，刷新预览则通过参数表达。
    if (isRefreshPreview(event)) return null;
    return OPERATION_CATEGORIES.find(category => category.tools.some(tool => tool === event.name))?.id || "other";
}
export function matchesOperation(row: TrajectoryRow, type: string): boolean {
    const category = operationCategory(row.event);
    if (category === null) return false;
    if (type === "all") return true;
    if (type.startsWith("category:")) return category === type.slice("category:".length);
    return row.event.name === type;
}
export function matchesTrajectoryState(row: TrajectoryRow, state: TrajectoryState): boolean {
    if (state === "all") return true;
    if (state === "confirmed") return confirmed(row);
    if (state === "failed") return row.event.result?.ok === false;
    return row.lifecycle === state;
}
export function summarizeTrajectories(rows: TrajectoryRow[]) {
    const categories = OPERATION_CATEGORIES.map(category => ({ ...category, attempts: 0, confirmed: 0 }));
    const states = { confirmed: 0, uncommitted: 0, failed: 0, rolled_back: 0 };
    let total = 0;
    for (const row of rows) {
        const id = operationCategory(row.event);
        if (id === null) continue;
        total += 1;
        const category = categories.find(item => item.id === id)!;
        category.attempts += 1;
        if (confirmed(row)) category.confirmed += 1;
        for (const state of Object.keys(states) as (keyof typeof states)[])
            if (matchesTrajectoryState(row, state)) states[state] += 1;
    }
    return { total, categories, states };
}
export function objectActionTitle(row: TrajectoryRow, subject: Selection | undefined): string | undefined {
    if (subject?.kind !== "equipment") return;
    const item = row.subjects.find(item => item.kind === subject.kind && item.id === subject.id);
    if (!item) return;
    if (item.created) return `${row.event.name === "craft_equipment" ? "制作" : "获得"} ${item.name}`;
    const ownership = item.ownership;
    if (ownership?.before && ownership.after) return `${item.name} 从 ${ownership.before.name} 转交给 ${ownership.after.name}`;
    if (ownership?.after) return `${ownership.after.name} 穿戴 ${item.name}`;
    if (ownership?.before) return `从 ${ownership.before.name} 身上卸下 ${item.name}`;
}
export function trajectorySearch(row: TrajectoryRow) {
    const entry = historyAction(row);
    return [entry.title, entry.summary, ...entry.effects, ...row.subjects.flatMap(s => [s.name, s.id])].join(" ").toLowerCase();
}
