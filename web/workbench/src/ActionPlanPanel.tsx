import { useMemo } from "react";
import type { ActionEntry } from "./actionModel";
import { describePlan } from "./actionPlan";
import { ActionChangePanel } from "./ActionChangePanel";
import { TOOLS } from "./model";

export function ActionPlanPanel({ entry, compact = false }: { entry: ActionEntry; compact?: boolean }) {
    const plan = useMemo(() => describePlan(entry), [entry]);
    const operations = new Map<string, number>();
    for (const step of plan.steps) operations.set(step.tool, (operations.get(step.tool) || 0) + 1);
    return <div className={`action-plan-panel ${compact ? "is-compact" : ""}`}>
        {!plan.repeated && <>
            {operations.size > 0 && <section className="plan-operation-summary" aria-label="方案操作概要">
                <h4>{plan.preview ? "计划操作" : "执行操作"}</h4><div>{[...operations].map(([tool, count]) => <span key={tool}>{TOOLS[tool] || tool} <b className="mono">×{count}</b></span>)}</div>
            </section>}
            {entry.status === "succeeded" && <ActionChangePanel entry={entry} changes={plan} showEmpty />}
            {plan.steps.length > 0 ? <details className="plan-disclosure" key={`steps:${entry.frame.cursor}`}>
                <summary>{plan.steps.length} 步{plan.preview ? "计划操作" : "已执行操作"}明细</summary>
                <ol className="plan-steps">{plan.steps.map((step, index) => <li key={index}><strong>{step.title}</strong>{step.detail && <p>{step.detail}</p>}</li>)}</ol>
            </details> : entry.status === "succeeded" && <p className="plan-missing">操作明细未记录</p>}
        </>}
    </div>;
}
