import { ActionInputs } from "./ActionInputPanel";
import { actionBrief } from "./actionModel";
import type { ActionEntry } from "./actionModel";
import { isPlan } from "./actionPlan";
import { ToolResultPanel } from "./ToolResultPanel";
import { ReturnedValue, ResultTable } from "./ActionReturnViews";

export function ActionResults({ entry }: { entry: ActionEntry }) {
    const summary = actionBrief(entry);
    const plan = isPlan(entry);
    const queryList = entry.kind === "query" && entry.returned.tables.some(table => !["battles", "events", "tactics"].includes(table.kind));
    return <>
        {!queryList && <section className="action-result" aria-label="执行结果"><h4>{entry.returned.preview ? "预览结果" : "执行结果"}</h4>
            {summary && <p className={entry.status === "failed" ? "action-detail-warning" : "action-detail-summary"}>{summary}</p>}
        </section>}
        <ToolResultPanel entry={entry} />
        {entry.kind !== "system" && <details className="plan-disclosure plan-raw-details" key={entry.frame.cursor}>
            <summary>{plan ? "原始参数与业务返回" : "输入参数"}</summary>
            <ActionInputs input={entry.input} />
            {plan && <>
                {entry.returned.tables.map((table, index) => <ResultTable key={index} data={table} />)}
                {Object.keys(entry.returned.fields).length > 0 && <ReturnedValue value={entry.returned.fields} />}
            </>}
        </details>}
    </>;
}
