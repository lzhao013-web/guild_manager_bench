import { TOOLS } from "./model";

export interface ToolCallEvent {
    type: string;
    name?: string;
    arguments?: Record<string, any>;
    result?: Record<string, any>;
    content?: unknown;
}
export type ToolCallStatus = "pending" | "succeeded" | "failed" | "recorded";
export const TOOL_CALL_STATUS = { pending: "执行中", succeeded: "成功", failed: "失败", recorded: "结果未明" };
export const OPERATION_CATEGORIES = [
    { id: "query", label: "只读查询", tools: [] },
    { id: "preview", label: "只读预览", tools: [] },
    { id: "recruit", label: "招募", tools: ["recruit_adventurer"] },
    { id: "dismiss", label: "解雇", tools: ["dismiss_adventurer"] },
    { id: "upgrade", label: "天赋购买", tools: ["purchase_upgrade"] },
    { id: "craft", label: "装备制作", tools: ["craft_equipment"] },
    { id: "exchange", label: "材料兑换", tools: ["exchange_materials"] },
    { id: "equipment", label: "穿戴 / 卸下", tools: ["equip_item", "unequip_item"] },
    { id: "experience", label: "经验分配", tools: ["allocate_experience"] },
    { id: "tactics", label: "战术配置", tools: ["configure_tactics"] },
    { id: "refresh", label: "付费刷新", tools: ["refresh_monsters"] },
    { id: "plan", label: "执行方案", tools: ["execute_plan"] },
    { id: "memo", label: "备忘录", tools: ["write_memo"] },
    { id: "turn", label: "结束回合", tools: ["end_turn"] },
    { id: "other", label: "其它工具", tools: [] },
] as const;
export type OperationCategory = typeof OPERATION_CATEGORIES[number]["id"];
export function isRefreshPreview(event: ToolCallEvent): boolean {
    return event.name === "refresh_monsters" && event.arguments?.preview === true;
}
export function operationCategory(event: ToolCallEvent): OperationCategory {
    if (event.name?.startsWith("preview_") || isRefreshPreview(event) || event.result?.simulation_only === true) return "preview";
    if (event.name?.startsWith("get_") || event.result?.forecast_only === true) return "query";
    return OPERATION_CATEGORIES.find(category => category.tools.some(tool => tool === event.name))?.id || "other";
}
export function isReadOnlyTool(event: ToolCallEvent): boolean {
    const category = operationCategory(event);
    return category === "query" || category === "preview";
}
export function toolCategoryLabel(event: ToolCallEvent): string {
    return OPERATION_CATEGORIES.find(category => category.id === operationCategory(event))!.label;
}
export function matchesTool(event: ToolCallEvent, type: string): boolean {
    if (event.type !== "tool_call" && event.type !== "tool_result") return false;
    if (type === "all") return true;
    if (type.startsWith("category:")) return operationCategory(event) === type.slice("category:".length);
    return event.name === type;
}
export function toolCallStatus(event: ToolCallEvent): ToolCallStatus {
    if (event.type === "tool_call") return "pending";
    if (event.result?.ok === false) return "failed";
    if (event.result?.ok === true) return "succeeded";
    // 部分历史查询没有 ok 字段，但工具文本显式记录了成功或失败。
    if (typeof event.content === "string") {
        if (/^(成功 |OK )/.test(event.content.trimStart())) return "succeeded";
        if (/^(失败 |FAIL )/.test(event.content.trimStart())) return "failed";
    }
    return "recorded";
}
export interface ToolCallCount {
    name: string;
    label: string;
    total: number;
    pending: number;
    succeeded: number;
    failed: number;
    recorded: number;
}
/** 输入已配对合并的调用，不按工具白名单筛选，也不按受影响对象重复计数。 */
export function summarizeToolCalls(events: ToolCallEvent[]): ToolCallCount[] {
    const tools = new Map<string, ToolCallCount>();
    for (const event of events) {
        if (!matchesTool(event, "all")) continue;
        const name = event.name!;
        let count = tools.get(name);
        if (!count) {
            count = { name, label: TOOLS[name] || name, total: 0, pending: 0, succeeded: 0, failed: 0, recorded: 0 };
            tools.set(name, count);
        }
        count.total += 1;
        count[toolCallStatus(event)] += 1;
    }
    return [...tools.values()];
}
