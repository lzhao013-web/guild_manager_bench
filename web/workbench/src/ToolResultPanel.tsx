import { useMemo, useState } from "react";
import type { ActionEntry } from "./actionModel";
import { ActionChangePanel } from "./ActionChangePanel";
import { ActionPlanPanel } from "./ActionPlanPanel";
import { describeChanges } from "./actionChanges";
import { isPlan } from "./actionPlan";
import { summarizeBattles } from "./actionReturns";
import type { ReturnTable } from "./actionReturns";
import { ResultTable, ReturnCollection, ReturnedValue, RewardValues } from "./ActionReturnViews";
import { fmt } from "./model";

function BattleResults({ data, entry }: { data: ReturnTable; entry: ActionEntry }) {
    const showRewards = entry.event.name !== "get_battle_log" || data.records.some(record => record.reward != null);
    const battle = summarizeBattles(data.records);
    const reward = (value: number | null) => value === null ? "未记录" : `+${fmt(value)}`;
    return <section className="return-battles" aria-label={data.title}>
        <h4>{data.title}</h4>
        <dl className="return-stat-grid return-battle-metrics" aria-label="战果概览">
            <div><dt>场数</dt><dd className="mono">{data.records.length}</dd></div><div><dt>胜</dt><dd className="mono">{battle.wins}</dd></div><div><dt>负</dt><dd className="mono">{battle.losses}</dd></div>
            {battle.unknown > 0 && <div><dt>其它结果</dt><dd className="mono">{battle.unknown}</dd></div>}
            {showRewards && <><div><dt>奖励金币</dt><dd className="mono">{reward(battle.gold)}</dd></div><div><dt>奖励经验</dt><dd className="mono">{reward(battle.experience)}</dd></div></>}
        </dl>
        {data.records.slice(0, 3).map((record, index) => <article className="return-battle" aria-label={battle.battles[index].name} key={index}>
            <header><strong>{battle.battles[index].name}</strong><span className={`action-status ${battle.battles[index].result === "胜利" ? "is-succeeded" : battle.battles[index].result === "失败" ? "is-failed" : ""}`}>{battle.battles[index].result}</span></header>
            <dl className="return-stat-grid">{(record.actions_taken ?? record.combat?.actions_taken) != null && <div><dt>行动数</dt><dd className="mono">{record.actions_taken ?? record.combat.actions_taken}</dd></div>}
                {(record.time_elapsed ?? record.combat?.time_elapsed) != null && <div><dt>耗时</dt><dd className="mono">{record.time_elapsed ?? record.combat.time_elapsed}</dd></div>}</dl>
            {showRewards && (record.reward ? <RewardValues reward={record.reward} /> : <span className="muted">奖励未记录</span>)}
        </article>)}
        <details className="plan-disclosure"><summary>完整战果（{data.records.length} 场）</summary><ResultTable data={data} /></details>
    </section>;
}

function ExtraFields({ value }: { value: Record<string, unknown> }) {
    const [open, setOpen] = useState(false);
    return <details className="plan-disclosure" onToggle={event => setOpen(event.currentTarget.open)}><summary>其他返回字段</summary>{open && <ReturnedValue value={value} />}</details>;
}

/** 所有工具共用紧凑结果与可展开明细，原始输入由外层单独展示。 */
export function ToolResultPanel({ entry, compact = false }: { entry: ActionEntry; compact?: boolean }) {
    const changes = useMemo(() => describeChanges(entry), [entry]);
    if (isPlan(entry)) return <ActionPlanPanel entry={entry} compact={compact} />;
    const returned = entry.returned;
    const memo = entry.event.name === "write_memo" && entry.status === "succeeded" ? entry.event.result?.memo?.content : undefined;
    const statistics = entry.event.result?.statistics;
    const tables = returned.tables;
    const extra = Object.fromEntries(Object.entries(returned.fields).filter(([key]) => !(key === "memo" && memo != null) && !(key === "statistics" && statistics != null)));
    const changed = changes.changesRecorded && (changes.resources.length > 0 || changes.members.length > 0 || changes.other.length > 0);
    const extras = Object.keys(extra).length > 0;
    const primary = tables.length > 0 || changed || memo != null || statistics != null;
    return <div className={`action-output-panel ${compact ? "is-compact" : ""}`}>
        {primary || extras ? <section className="action-returns" aria-label="业务返回">
            {changed && (entry.event.name === "end_turn" ? <details className="plan-disclosure"><summary>结算变化</summary><ActionChangePanel entry={entry} changes={changes} /></details>
                : <ActionChangePanel entry={entry} changes={changes} />)}
            {tables.map((table, index) => table.kind === "battles" ? <BattleResults key={index} data={table} entry={entry} />
                : table.kind === "events" ? <details className="plan-disclosure" key={index}><summary>{table.title}（{table.rows.length} 条）</summary><ResultTable data={table} /></details>
                : changed && table.kind === "members" && !["recruit_adventurer", "dismiss_adventurer"].includes(entry.event.name) ? <details className="plan-disclosure" key={index}><summary>成员详情（{table.rows.length} 位）</summary><ReturnCollection data={table} entry={entry} /></details>
                : <ReturnCollection key={index} data={table} entry={entry} />)}
            {statistics != null && <section className="return-statistics" aria-label="战斗统计"><h4>战斗统计</h4><ReturnedValue value={statistics} /></section>}
            {memo != null && <section className="return-memo" aria-label="备忘正文"><h4>备忘正文</h4>{compact && memo.length > 160 ? <><p>{memo.slice(0, 160)}…</p><details className="plan-disclosure"><summary>完整正文</summary><pre>{memo}</pre></details></> : <pre>{memo}</pre>}</section>}
            {extras && (primary ? <ExtraFields key={entry.frame.cursor} value={extra} /> : <ReturnedValue value={extra} />)}
        </section> : null}
    </div>;
}
