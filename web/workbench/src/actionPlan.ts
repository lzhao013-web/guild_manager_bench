import { describeAction } from "./actionModel";
import type { ActionEntry } from "./actionModel";
import { describeInput, inputPreview, PLAN_TOOLS } from "./actionInputs";
import { returnNames } from "./actionReturns";
import { describeChanges } from "./actionChanges";
import type { ActionChanges } from "./actionChanges";

export interface PlanStep { tool: string; title: string; detail: string; alias?: string }
export type PlanPreviewLink = { onOpen: () => void } | null;
export interface ActionPlan extends ActionChanges { repeated: boolean; steps: PlanStep[] }
export function isPlan(entry: ActionEntry): boolean {
    return entry.event.name === "preview_team_power" || entry.event.name === "execute_plan";
}
export function describePlan(entry: ActionEntry): ActionPlan {
    const preview = entry.event.name === "preview_team_power";
    const r = entry.event.result;
    const repeated = r?.already_executed === true;
    const view: ActionPlan = { ...describeChanges(entry), repeated, steps: [] };
    const successful = entry.status === "succeeded";
    const events = !preview && successful && !repeated ? r?.events : undefined;
    // 失败或待返回的试算输入尚未通过方案校验，保留在参数详情中。
    const operations = preview && successful ? entry.event.arguments?.operations : undefined;
    const names = returnNames(entry.frame);
    for (const op of operations || [])
        if (op.as) names.set(`$${op.as}`, `${op.tool === "recruit_adventurer" ? "方案成员" : "方案装备"} $${op.as}`);
    const steps = events?.map((event: Record<string, any>) => ({ name: PLAN_TOOLS[event.action.type] || event.action.type,
        args: Object.fromEntries(Object.entries(event.action).filter(([key]) => key !== "type")), event, alias: undefined as string | undefined })) ||
        operations?.map((op: Record<string, any>) => ({ name: op.tool, args: op.arguments, event: undefined, alias: op.as as string | undefined })) || [];
    for (const step of steps) {
        const frame = { ...entry.frame, before: null, observation: null, names: Object.fromEntries(names), record: { ...entry.frame.record, event: {
            type: "tool_result", name: step.name, arguments: step.args,
            result: { ok: true, event: step.event, _llm_refs: r?._llm_refs },
        } } };
        const action = describeAction(frame);
        const input = describeInput(frame, names);
        const inTitle = ["adventurer_id", "amount", "session_id",
            ...(step.name === "equip_item" ? ["equipment_id", "equipment_instance_id", "instance_id"] : []),
            ...(step.name === "craft_equipment" ? ["recipe_id"] : []),
            ...(step.name === "purchase_upgrade" ? ["upgrade_id"] : []),
            ...(step.name === "unequip_item" ? ["slot"] : [])];
        const extra = input.state === "recorded" ? { ...input, value: { ...input.value,
            children: input.value.children.filter(field => !inTitle.includes(field.key)) } } : input;
        const detail = step.name === "configure_tactics" && input.state === "recorded"
            ? input.value.children.filter(field => ["skill_priority", "disabled_skills"].includes(field.key)).map(field => {
                const priority = field.key === "skill_priority";
                const skills = field.value.children.map(item => item.value.text);
                return `${priority ? "优先" : "停用"}：${skills.length ? skills.join(priority ? " → " : "、") : priority ? "默认顺序" : "无"}`;
            }).join(" / ")
            : extra.state === "recorded" && extra.value.children.length ? inputPreview(extra) : "";
        view.steps.push({ tool: step.name, title: action.title, alias: step.alias, detail });
    }
    return view;
}

export function findPlanPreview(entry: ActionEntry, entries: ActionEntry[]): ActionEntry | undefined {
    if (entry.event.name !== "execute_plan") return;
    const id = entry.event.result?.plan_id ?? entry.event.arguments?.plan_id;
    if (typeof id !== "string" || !id) return;
    for (let index = entries.length - 1; index >= 0; index--) {
        const candidate = entries[index];
        if (candidate.frame.cursor >= entry.frame.cursor) continue;
        if (["turn_started", "run_resumed", "turn_retry"].includes(candidate.event.type)) break;
        if (candidate.event.name === "preview_team_power" && candidate.status === "succeeded" && candidate.event.result?.plan_id === id)
            return candidate;
    }
}
