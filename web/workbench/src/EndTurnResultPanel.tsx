import type { ReactNode } from "react";
import { ArrowRight } from "@phosphor-icons/react";
import type { ActionEntry } from "./actionModel";
import { describeEndTurn } from "./endTurn";
import type { EndTurnMember } from "./endTurn";
import { describeChanges, changeValue } from "./actionChanges";
import { ActionChangeDetails } from "./ActionChangePanel";
import { battleResult, summarizeBattles } from "./actionReturns";
import type { ReturnTable } from "./actionReturns";
import { ReturnedFields, RewardValues } from "./ActionReturnViews";
import { MATERIALS } from "./sceneModel";
import { fmt } from "./model";

function EndTurnRewards({ entry, data }: { entry: ActionEntry; data: ReturnTable }) {
    const summary = summarizeBattles(data.records);
    const changes = describeChanges(entry);
    const before = entry.frame.before;
    const after = entry.frame.observation !== before ? entry.frame.observation : null;
    const rows = [
        { label: "金币", value: summary.gold, resource: "金币", before: before?.gold, after: after?.gold },
        { label: "经验", value: summary.experience, resource: "经验池", before: before?.experience_pool, after: after?.experience_pool },
        ...(summary.materialTotals || []).map(({ id, value }) => ({ label: MATERIALS[id] || id, value, resource: MATERIALS[id] || id,
            before: before ? before.materials[id] ?? 0 : undefined, after: after ? after.materials[id] ?? 0 : undefined })),
    ];
    return <section className="end-turn-rewards" aria-label="讨伐奖励"><h5>讨伐奖励</h5>
        <table className="end-turn-reward-table"><thead><tr><th scope="col">奖励</th><th scope="col">获得</th><th scope="col">余额（结算前 → 后）</th></tr></thead>
            <tbody>{rows.map(row => {
                const change = changes.resources.find(change => change.label === row.resource);
                const previous = change?.before ?? row.before, next = change?.after ?? row.after;
                return <tr key={row.resource}><th scope="row">{row.label}</th>
                    <td className={`mono ${row.value !== null && row.value > 0 ? "is-gain" : ""}`}>{row.value === null ? "未记录" : `${row.value >= 0 ? "+" : ""}${fmt(row.value)}`}</td>
                    <td className="mono">{previous !== undefined || next !== undefined ? <><span className="end-turn-before">{changeValue(previous, entry)}</span> → {changeValue(next, entry)}</> : "未记录"}</td>
                </tr>;
            })}</tbody>
        </table>
        {summary.materialTotals === null ? <p className="plan-missing">材料奖励未完整记录</p> : !summary.materialTotals.length && <p className="end-turn-note">无材料奖励</p>}
    </section>;
}

function EndTurnBattles({ data }: { data: ReturnTable }) {
    const summary = summarizeBattles(data.records);
    const draws = summary.battles.filter(battle => battle.result === "平局").length;
    const unknown = summary.unknown - draws;
    return <section className="end-turn-battles" aria-label="战斗结果">
        <h4>战斗结果</h4>
        <dl className="end-turn-metrics" aria-label="战果概览">
            <div><dt>场数</dt><dd className="mono">{data.records.length}</dd></div>
            <div><dt>胜</dt><dd className="mono">{summary.wins}</dd></div>
            <div><dt>负</dt><dd className="mono">{summary.losses}</dd></div>
            {draws > 0 && <div><dt>平</dt><dd className="mono">{draws}</dd></div>}
            {unknown > 0 && <div><dt>未记录</dt><dd className="mono">{unknown}</dd></div>}
        </dl>
        {!data.records.length && <p className="end-turn-note">本轮无战斗</p>}
        {data.records.map((battle, index) => {
            const outcome = battleResult(battle);
            const actions = battle.actions_taken ?? battle.combat?.actions_taken;
            const duration = battle.time_elapsed ?? battle.combat?.time_elapsed;
            return <article className="end-turn-battle" aria-label={summary.battles[index].name} key={index}>
                <div className="end-turn-match"><span className="end-turn-index mono" aria-hidden="true">{index + 1}</span><strong>{battle.adventurer_name || "成员未记录"}</strong><ArrowRight size={13} aria-hidden="true" /><span>{battle.monster_name || "目标未记录"}</span></div>
                <span className={`action-status ${outcome === "胜利" ? "is-succeeded" : outcome === "失败" ? "is-failed" : ""}`}>{outcome}</span>
                <div className="end-turn-battle-reward">{battle.reward == null ? <span className="muted">奖励未记录</span> : <RewardValues reward={battle.reward} />}</div>
                {(actions != null || duration != null) && <p className="end-turn-battle-timing">{actions != null && <span>行动 <b className="mono">{fmt(actions)}</b> 次</span>}{duration != null && <span>耗时 <b className="mono">{fmt(duration)}</b></span>}</p>}
            </article>;
        })}
    </section>;
}

function MemberVital({ member, stat }: { member: EndTurnMember; stat: "current_hp" | "current_mp" }) {
    const before = member.before?.[stat], after = member.after?.[stat];
    return <span className="end-turn-vital mono"><span className="end-turn-before">{before == null ? "未记录" : fmt(before)}</span><span aria-hidden="true"> → </span><strong>{after == null ? "未记录" : fmt(after)}</strong></span>;
}

export function EndTurnResultPanel({ entry, inspection }: { entry: ActionEntry; inspection: ReactNode }) {
    const view = describeEndTurn(entry);
    const changes = describeChanges(entry);
    const fields = entry.returned.fields;
    return <section className="end-turn-result action-returns" aria-label="业务返回">
        <section className="end-turn-status" aria-label="执行结果"><p>本轮已结算</p>
            {view.finished ? <span>经营阶段已结束</span> : view.nextTurn !== null && <span>进入第 <b className="mono">{view.nextTurn}</b> 回合</span>}
        </section>
        <section className="end-turn-orders" aria-label="本轮讨伐安排">
            <header><h4>本轮安排</h4>{view.orders !== null && <span>{view.orders.length} 场讨伐</span>}</header>
            {view.orders === null ? <p className="plan-missing">讨伐安排未记录</p> : !view.orders.length ? <p className="end-turn-note">本轮未安排讨伐</p>
                : <ol>{view.orders.map((order, index) => <li key={index}><span className="end-turn-index mono" aria-hidden="true">{index + 1}</span><strong>{order.member}</strong><ArrowRight size={13} aria-hidden="true" /><span>{order.target}</span></li>)}</ol>}
            {view.members.some(member => member.activity === "休整") && <div className="end-turn-resting"><span>休整</span><ul>{view.members.filter(member => member.activity === "休整").map(member => <li key={member.id}>{member.name}</li>)}</ul></div>}
        </section>
        <section className="end-turn-settlement" aria-label="本轮结算结果">
            {view.battles ? <><EndTurnBattles data={view.battles} /><EndTurnRewards entry={entry} data={view.battles} /></> : <p className="plan-missing">战斗结果与奖励未记录</p>}
            <section className="end-turn-party" aria-label="成员结算状态"><h4>成员状态</h4>
                <p className="end-turn-note">结算前 → 结算后，后者包含回合末恢复。</p>
                {view.members.length ? <table className="end-turn-party-table"><thead><tr><th scope="col">成员</th><th scope="col">生命</th><th scope="col">法力</th></tr></thead>
                    <tbody>{view.members.map(member => <tr key={member.id}><th scope="row" title={member.id}>{member.name}<span>{member.activity || "安排未记录"}</span></th>
                        <td><MemberVital member={member} stat="current_hp" /></td><td><MemberVital member={member} stat="current_mp" /></td>
                    </tr>)}</tbody>
                </table> : <p className="plan-missing">{view.afterPartyComplete ? "结算后队伍为空" : "结算后成员状态未记录"}</p>}
            </section>
        </section>
        <section className="action-inspection" aria-label="排查详情">
            <ActionChangeDetails entry={entry} changes={changes} includeOther />
            {view.battles && <ReturnedFields key={`battles:${entry.frame.cursor}`} value={view.battles.records} label={view.battles.records.some(battle => battle.combat?.events != null || battle.events != null) ? "完整战果与战报" : "完整战果字段"} />}
            {entry.returned.tables.filter(table => table.kind !== "battles").map((table, index) => <ReturnedFields key={`table:${entry.frame.cursor}:${index}`} value={table.records} label={`${table.title}完整字段`} />)}
            {Object.keys(fields).length > 0 && <ReturnedFields key={`fields:${entry.frame.cursor}`} value={fields} label="其他返回字段" />}
            {inspection}
        </section>
    </section>;
}
