import { useMemo } from "react";
import type { ReactNode } from "react";
import { ArrowLeft } from "@phosphor-icons/react";
import type { ActionEntry } from "./actionModel";
import { actionResult } from "./actionModel";
import { describePlan } from "./actionPlan";
import type { ActionPlan, PlanPreviewLink, PlanStep } from "./actionPlan";
import { ActionChangeDetails } from "./ActionChangePanel";
import { changeDelta, changeValue } from "./actionChanges";
import type { ActionChange } from "./actionChanges";
import { ReturnedFields } from "./ActionReturnViews";
import { fmt } from "./model";

function PlanStatus({ entry, plan, previewLink }: { entry: ActionEntry; plan: ActionPlan; previewLink?: PlanPreviewLink }) {
    const r = entry.event.result;
    const empty = r?.operation_count === 0;
    const hasBudget = typeof r?.required_calls === "number" && typeof r?.remaining_calls === "number";
    const short = hasBudget && r.remaining_calls < r.required_calls;
    return <section className={`plan-result-status ${plan.preview ? "is-preview" : ""} ${entry.status === "failed" ? "is-failed" : ""}`} aria-label="执行结果">
        {entry.status !== "succeeded" ? <p className={entry.status === "failed" ? "action-detail-warning" : "action-detail-summary"}>{actionResult(entry)}</p>
            : plan.repeated ? <p>该方案已提交过，本次未重复执行。</p>
            : plan.preview ? <>
                <p className="plan-simulation-note">仅试算，未改变真实状态</p>
                {empty ? <p className="plan-eligibility">空方案预检，不签发执行凭据</p>
                    : <div className={`plan-eligibility ${r?.can_execute === false ? "is-blocked" : ""}`}>
                        <strong>{r?.can_execute === true ? "试算时可提交" : r?.can_execute === false ? short ? "预算不足，无法提交" : "试算时不可提交" : "提交资格未记录"}</strong>
                        {hasBudget && <span className="mono">需余额 ≥{fmt(r.required_calls)} 次 / 试算后剩 {fmt(r.remaining_calls)} 次</span>}
                    </div>}
            </> : <p className="plan-committed">已整批提交{typeof r?.operation_count === "number" ? ` ${fmt(r.operation_count)} 步` : ""}{r?.plan_id && <span>提交不扣调用预算</span>}</p>}
        {entry.status === "failed" && hasBudget && <p className="plan-budget-shortage mono">提交需余额 ≥{fmt(r.required_calls)} 次 / 当前剩 {fmt(r.remaining_calls)} 次</p>}
        {!plan.preview && typeof r?.tool_budget?.remaining === "number" && <p className="plan-budget-after">返回时剩余调用预算 <b className="mono">{fmt(r.tool_budget.remaining)}</b> 次</p>}
        {entry.event.name === "execute_plan" && previewLink !== undefined && <div className="plan-preview-link">
            {previewLink ? <button type="button" onClick={previewLink.onOpen}><ArrowLeft size={14} aria-hidden="true" />查看对应试算</button> : <span>对应试算未记录</span>}
        </div>}
    </section>;
}

const rankNumber = (value: number) => value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function PlanRank({ entry }: { entry: ActionEntry }) {
    const r = entry.event.result;
    const current = r?.current_rank_score, projected = r?.projected_rank_score, delta = r?.rank_score_delta;
    if (![current, projected, delta, r?.rank_score].some(value => typeof value === "number")) return null;
    const comparison = [current, projected, delta].some(value => typeof value === "number");
    return <section className="plan-rank-result" aria-label="正式战力试算"><h5>正式战力</h5>
        {comparison ? <dl>
            <div><dt>调用前</dt><dd className="mono">{typeof current === "number" ? rankNumber(current) : "未记录"}</dd></div>
            <div><dt>方案执行后（预计）</dt><dd className="mono">{typeof projected === "number" ? rankNumber(projected) : "未记录"}</dd></div>
            <div><dt>变化</dt><dd className={`mono ${typeof delta === "number" ? delta > 0 ? "is-gain" : delta < 0 ? "is-loss" : "" : ""}`}>{typeof delta === "number" ? `${delta > 0 ? "+" : ""}${rankNumber(delta)}` : "未记录"}</dd></div>
        </dl> : <p className="mono">已记录 Rank Score {rankNumber(r.rank_score)}</p>}
    </section>;
}

function PlanValueChanges({ entry, changes, label, leveled = false }: { entry: ActionEntry; changes: ActionChange[]; label: string; leveled?: boolean }) {
    return changes.length > 0 && <table className="plan-value-table" aria-label={label}>
        <thead><tr><th scope="col">项目</th><th scope="col">之前</th><th scope="col">之后</th><th scope="col">变化</th></tr></thead>
        <tbody>{changes.map((change, index) => {
            const rollover = leveled && change.label === "经验";
            const delta = !rollover && typeof change.before === "number" && typeof change.after === "number" ? change.after - change.before : null;
            return <tr key={index}><th scope="row">{change.label === "经验" ? "经验进度" : change.label}</th>
                <td className="mono plan-before-value">{changeValue(change.before, entry, change.label)}</td>
                <td className="mono">{changeValue(change.after, entry, change.label)}</td>
                <td className={`mono ${delta !== null ? delta > 0 ? "is-gain" : delta < 0 ? "is-loss" : "" : ""}`}>{rollover ? "跨级结转" : delta === null ? "未记录" : changeDelta(change, entry)}</td>
            </tr>;
        })}</tbody>
    </table>;
}

function PlanConfigurationChanges({ entry, changes, label }: { entry: ActionEntry; changes: ActionChange[]; label: string }) {
    return changes.length > 0 && <table className="plan-config-table" aria-label={label}>
        <thead><tr><th scope="col">配置</th><th scope="col">之前</th><th scope="col">之后</th></tr></thead>
        <tbody>{changes.map((change, index) => <tr key={index}><th scope="row">{change.label}</th>
            <td className="plan-before-value">{changeValue(change.before, entry, change.label)}</td>
            <td>{changeValue(change.after, entry, change.label)}</td>
        </tr>)}</tbody>
    </table>;
}

function PlanMemberChanges({ entry, plan }: { entry: ActionEntry; plan: ActionPlan }) {
    return plan.members.length > 0 && <section className="plan-member-results" aria-label={plan.preview ? "预计成员变化" : "实际成员变化"}>
        <h5>成员</h5>
        {plan.members.map(member => {
            const after = entry.frame.observation?.adventurers.find(item => item.name === member.name);
            const level = member.level?.after ?? after?.level;
            const stats = member.stats.filter(change => !["经验", "当前生命", "当前法力"].includes(change.label));
            const leveled = typeof member.level?.before === "number" && typeof member.level.after === "number" && member.level.after > member.level.before;
            return <article className="plan-member-result" aria-label={member.name} key={member.name}>
                <header><h6>{member.name}</h6>{level !== undefined && <span className="mono">Lv.{member.level && <><span className="plan-before-value">{changeValue(member.level.before, entry)}</span> → </>}{changeValue(level, entry)}</span>}</header>
                <PlanValueChanges entry={entry} changes={stats.length ? stats : member.stats} label={`${member.name}的属性变化`} leveled={leveled} />
                <PlanConfigurationChanges entry={entry} changes={member.configuration} label={`${member.name}的装备与战术变化`} />
            </article>;
        })}
    </section>;
}

function coveredOwnership(change: ActionChange, plan: ActionPlan, entry: ActionEntry) {
    if (change.kind !== "equipment" || !change.label.endsWith(" 装备者")) return false;
    const name = change.label.slice(0, -4);
    const owners = [change.before, change.after].map(value => changeValue(value, entry));
    return plan.members.some(member => owners.includes(member.name) && member.configuration.some(config => config.label === "装备"
        && [config.before, config.after].some(value => typeof value === "string" && changeValue(value, entry).split("、").some(slot => slot.slice(slot.indexOf("：") + 1) === name))));
}

function PlanAssets({ entry, plan }: { entry: ActionEntry; plan: ActionPlan }) {
    const labels = new Map([["新冒险者", "新增成员"], ["成员离队", "离队成员"], ["获得装备", "获得装备"], ["解锁加成", "解锁天赋"]]);
    const other = plan.other.filter(change => !coveredOwnership(change, plan, entry));
    return <>
        {other.some(change => labels.has(change.label)) && <section className="plan-assets" aria-label={plan.preview ? "预计资产变化" : "实际资产变化"}>
            <h5>对象变化</h5>
            <dl>{[...labels].map(([label, title]) => {
                const items = other.filter(change => change.label === label);
                return items.length > 0 && <div key={label}><dt>{title}</dt><dd>{items.map((change, index) => <span className="plan-asset-name" key={index}>{changeValue(change.after, entry, change.label).replace(/\s+\(试算(?:角色|装备)\d+\)$/, "")}</span>)}</dd></div>;
            })}</dl>
        </section>}
        {other.some(change => !labels.has(change.label)) && <section className="plan-other-results" aria-label="其他对象变化"><h5>其他变化</h5>
            <PlanConfigurationChanges entry={entry} changes={other.filter(change => !labels.has(change.label))} label="其他对象的前后变化" />
        </section>}
    </>;
}

function StepList({ steps, start = 1 }: { steps: PlanStep[]; start?: number }) {
    return <ol className="plan-step-list" start={start}>{steps.map((step, index) => <li key={index}>
        <span className="plan-step-index mono" aria-hidden="true">{start + index}</span>
        <div className="plan-step-content" title={`${step.title}${step.detail ? `，${step.detail}` : ""}`}><strong>{step.title}</strong>{step.detail && <span>{step.detail}</span>}</div>
        {step.alias && <code className="plan-step-alias" title={`本步命名：$${step.alias}`}>${step.alias}</code>}
    </li>)}</ol>;
}

export function ActionPlanPanel({ entry, compact = false, inspection, previewLink }: { entry: ActionEntry; compact?: boolean; inspection?: ReactNode; previewLink?: PlanPreviewLink }) {
    const plan = useMemo(() => describePlan(entry), [entry]);
    const successful = entry.status === "succeeded";
    const r = entry.event.result;
    const extra = Object.fromEntries(Object.entries(entry.returned.fields).filter(([key]) => key !== "plan_entities"));
    return <section className={`action-plan-panel action-returns ${compact ? "is-compact" : ""}`} aria-label="业务返回">
        <PlanStatus entry={entry} plan={plan} previewLink={previewLink} />
        {successful && !plan.repeated && <>
            {plan.steps.length > 0 ? <section className="plan-step-section" aria-label={plan.preview ? "计划步骤明细" : "已执行步骤明细"}>
                <header className="plan-step-heading"><h4>{plan.preview ? "计划操作" : "已执行操作"}</h4><span>{plan.steps.length} 步</span></header>
                <StepList steps={plan.steps.slice(0, 6)} />
                {plan.steps.length > 6 && <details className="plan-disclosure" key={`steps:${entry.frame.cursor}`}><summary>其余 {plan.steps.length - 6} 步</summary><StepList steps={plan.steps.slice(6)} start={7} /></details>}
            </section> : r?.operation_count !== 0 && <p className="plan-missing">操作明细未记录</p>}
            <section className="plan-changes-section" aria-label={plan.preview ? "方案预计变化" : "方案实际变化"}>
                <h4>{plan.preview ? "预计变化" : "带来的变化"}</h4>
                {plan.resources.length > 0 && <section className="plan-resource-results" aria-label={plan.preview ? "预计资源变化" : "实际资源变化"}>
                    <h5>资源与额度</h5><PlanValueChanges entry={entry} changes={plan.resources} label="资源与额度的前后变化" />
                </section>}
                <PlanMemberChanges entry={entry} plan={plan} />
                <PlanAssets entry={entry} plan={plan} />
                {plan.preview && <PlanRank entry={entry} />}
                {!plan.changesRecorded && <p className="plan-missing">{plan.preview ? "预计变化未记录" : "实际变化未记录"}</p>}
                {plan.changesRecorded && !plan.resources.length && !plan.members.length && !plan.other.length && <p className="plan-missing">无变化</p>}
            </section>
        </>}
        <section className="action-inspection" aria-label="排查详情">
            {successful && !plan.repeated && <ActionChangeDetails entry={entry} changes={plan} includeOther />}
            {r?.plan_entities != null && <ReturnedFields key={`projection:${entry.frame.cursor}`} value={r.plan_entities} label="方案投影编号（仅用于该方案讨伐预览）" />}
            {entry.returned.tables.map((table, index) => <ReturnedFields key={`table:${entry.frame.cursor}:${index}`} value={table.records} label={`${table.title}完整字段`} />)}
            {Object.keys(extra).length > 0 && <ReturnedFields key={`extra:${entry.frame.cursor}`} value={extra} label="其他返回字段" />}
            {inspection}
        </section>
    </section>;
}
