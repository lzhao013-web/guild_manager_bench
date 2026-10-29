import { describeAction } from "./actionModel";
import type { ActionEntry } from "./actionModel";
import { actionNames, describeInput, inputPreview, PLAN_TOOLS } from "./actionInputs";
import { describeChanges } from "./actionChanges";
import type { ActionChanges } from "./actionChanges";

export interface PlanStep { tool: string; title: string; detail: string }
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
    const names = actionNames(entry.frame);
    for (const op of operations || [])
        if (op.as) names.set(`$${op.as}`, `${op.tool === "recruit_adventurer" ? "方案成员" : "方案装备"} $${op.as}`);
    const steps = events?.map((event: Record<string, any>) => ({ name: PLAN_TOOLS[event.action.type] || event.action.type,
        args: Object.fromEntries(Object.entries(event.action).filter(([key]) => key !== "type")), event })) ||
        operations?.map((op: Record<string, any>) => ({ name: op.tool, args: op.arguments, event: undefined })) || [];
    for (const step of steps) {
        const frame = { ...entry.frame, before: null, observation: null, names: Object.fromEntries(names), record: { ...entry.frame.record, event: {
            type: "tool_result", name: step.name, arguments: step.args,
            result: { ok: true, event: step.event, _llm_refs: r?._llm_refs },
        } } };
        const action = describeAction(frame);
        const input = describeInput(frame, names);
        const extra = input.state === "recorded" ? { ...input, value: { ...input.value,
            children: input.value.children.filter(field => !["adventurer_id", "amount", "session_id"].includes(field.key)) } } : input;
        view.steps.push({ tool: step.name, title: action.title, detail: extra.state === "recorded" && extra.value.children.length ? inputPreview(extra) : "" });
    }
    return view;
}
