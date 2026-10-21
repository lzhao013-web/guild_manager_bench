import { describeAction } from "./actionModel";
import { matchesTool, OPERATION_CATEGORIES, operationCategory, summarizeToolCalls, toolCallStatus } from "./toolCalls";
import type { ToolCallStatus } from "./toolCalls";
import type { Selection } from "./sceneModel";
import type { WatchFrame, WatchPage } from "./watchModel";
export { OPERATION_CATEGORIES, operationCategory } from "./toolCalls";
export type { OperationCategory } from "./toolCalls";

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
    return { ...describeAction(frame), rolledBack: row.lifecycle === "rolled_back" };
}
export function subjectRows(rows: TrajectoryRow[], subject: Selection) {
    return rows.filter(row => row.subjects.some(item => item.kind === subject.kind && item.id === subject.id));
}
export function confirmed(row: TrajectoryRow) {
    return row.lifecycle === "confirmed" && toolCallStatus(row.event) === "succeeded";
}
export type TrajectoryState = "all" | "confirmed" | "uncommitted" | "rolled_back" | ToolCallStatus;
export function matchesOperation(row: TrajectoryRow, type: string): boolean {
    return matchesTool(row.event, type);
}
export function matchesTrajectoryState(row: TrajectoryRow, state: TrajectoryState): boolean {
    if (state === "all") return true;
    if (state === "confirmed") return confirmed(row);
    if (state === "failed" || state === "pending" || state === "succeeded" || state === "recorded") return toolCallStatus(row.event) === state;
    return row.lifecycle === state;
}
export function summarizeTrajectories(rows: TrajectoryRow[]) {
    const categories = OPERATION_CATEGORIES.map(category => ({ ...category, attempts: 0, confirmed: 0 }));
    const states = { confirmed: 0, uncommitted: 0, failed: 0, rolled_back: 0, succeeded: 0, pending: 0, recorded: 0 };
    let total = 0;
    for (const row of rows) {
        const id = operationCategory(row.event);
        total += 1;
        const category = categories.find(item => item.id === id)!;
        category.attempts += 1;
        if (confirmed(row)) category.confirmed += 1;
        for (const state of Object.keys(states) as (keyof typeof states)[])
            if (matchesTrajectoryState(row, state)) states[state] += 1;
    }
    return { total, categories, states, tools: summarizeToolCalls(rows.map(row => row.event)) };
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
    return [row.event.name, JSON.stringify(row.event.arguments || {}), entry.title, entry.summary, ...entry.effects, ...row.subjects.flatMap(s => [s.name, s.id])].join(" ").toLowerCase();
}
