import type { ReactNode } from "react";
import type { ActionEntry } from "./actionModel";
import type { ReturnTable } from "./actionReturns";
import { describeChanges } from "./actionChanges";
import { ActionChangeDetails, ActionChangePanel, ActionResourceStrip } from "./ActionChangePanel";
import { ReturnedFields, RewardValues, Skills, StatValues } from "./ActionReturnViews";
import { MONSTER_ROLES } from "./monsterModel";
import { Portrait } from "./SceneParts";
import { fmt } from "./model";

type Monster = ReturnTable["records"][number];
type MonsterRow = { monster: Monster; index: number };
type Group = "refreshed" | "locked" | "challenge" | "unknown";

function MonsterResult({ monster, index, entry }: MonsterRow & { entry: ActionEntry }) {
    const preview = entry.returned.preview;
    const ref = entry.event.result?._llm_refs?.monster?.[monster.monster_id];
    const name = monster.name || "名称未记录";
    return <article className="refresh-monster-card" aria-label={name}>
        <header>
            <Portrait id={monster.archetype_id} name={name} monster />
            <div><strong>{name}</strong><span>{MONSTER_ROLES[monster.tier] || "身份未记录"}</span></div>
            <span className="refresh-monster-reference mono">{preview ? `候选 ${index + 1}` : ref != null ? `怪物 #${ref}` : monster.monster_id || "编号未记录"}</span>
        </header>
        {monster.stats != null ? <StatValues stats={monster.stats} /> : <p className="plan-missing">属性未记录</p>}
        <section className="refresh-monster-reward" aria-label={`${name}的获胜奖励`}><h5>获胜奖励</h5>
            {monster.reward != null ? <RewardValues reward={monster.reward} /> : <p className="plan-missing">奖励未记录</p>}
        </section>
        {monster.skills == null ? <p className="plan-missing">技能未记录</p> : monster.skills.length ? <Skills skills={monster.skills} /> : <p className="refresh-no-skills">无技能</p>}
    </article>;
}

function MonsterResults({ rows, entry }: { rows: MonsterRow[]; entry: ActionEntry }) {
    return <>
        {rows.slice(0, 3).map(row => <MonsterResult key={row.index} {...row} entry={entry} />)}
        {rows.length > 3 && <details className="plan-disclosure refresh-more-monsters" key={`more:${entry.frame.cursor}`}>
            <summary>其余 {rows.length - 3} 个{entry.returned.preview ? "候选" : "目标"}</summary>
            {rows.slice(3).map(row => <MonsterResult key={row.index} {...row} entry={entry} />)}
        </details>}
    </>;
}

export function MonsterRefreshResultPanel({ entry, inspection }: { entry: ActionEntry; inspection: ReactNode }) {
    const preview = entry.returned.preview;
    const result = entry.event.result!;
    const changes = describeChanges(entry);
    const costs = { ...changes, resources: changes.resources.filter(change => change.label === "金币") };
    const monsters: Monster[] | undefined = entry.returned.tables.find(table => table.kind === "monsters")?.records
        ?? (!preview && entry.frame.observation !== entry.frame.before ? entry.frame.observation?.monsters : undefined);
    const before = entry.frame.before?.monsters;
    const previousIds = new Set(before?.map(monster => monster.monster_id));
    const groups: Record<Group, MonsterRow[]> = { refreshed: [], locked: [], challenge: [], unknown: [] };
    monsters?.forEach((monster, index) => {
        let group: Group = "unknown";
        if (monster.tier === "challenge") group = "challenge";
        else if (preview && typeof monster.preserved === "boolean") group = monster.preserved ? "locked" : "refreshed";
        else if (before !== undefined && monster.monster_id != null) group = previousIds.has(monster.monster_id) ? "locked" : "refreshed";
        groups[group].push({ monster, index });
    });
    const refreshNumber = preview ? result.refresh_number : result.monster_refresh?.refreshes_used
        ?? changes.resources.find(change => change.label === "本回合刷新次数")?.after;
    const preservedGroups = [
        { key: "locked" as const, title: "锁定保留" },
        { key: "challenge" as const, title: "固定挑战", note: "自动保留" },
    ];
    return <section className="monster-refresh-result action-returns" aria-label="业务返回">
        {preview ? <>
            <p className="refresh-preview-note">仅预览，未扣费，真实怪池未改变。</p>
            <section className="result-resource-strip" aria-label="预计刷新费用"><h4>预计费用</h4>
                {typeof result.gold_cost === "number" ? <dl><div><dt>金币</dt><dd><strong className="mono">{fmt(result.gold_cost)}</strong></dd></div></dl> : <span className="plan-missing">费用未记录</span>}
                {typeof result.can_afford === "boolean" && <span className={`refresh-affordability ${result.can_afford ? "" : "is-short"}`}>{result.can_afford ? "金币足够" : "金币不足，暂不能实际刷新"}</span>}
            </section>
        </> : costs.resources.length ? <ActionResourceStrip entry={entry} changes={costs} /> : <p className="plan-missing">实际消耗未记录</p>}
        {(typeof refreshNumber === "number" || result.monster_refresh != null) && <div className="refresh-result-meta">
            {typeof refreshNumber === "number" && <span>{preview ? "预览本回合第" : "本回合第"} <b className="mono">{fmt(refreshNumber)}</b> 次刷新</span>}
            {!preview && (typeof result.monster_refresh?.next_gold_cost === "number" ? <span>下次费用 <b className="mono">金币 {fmt(result.monster_refresh.next_gold_cost)}</b></span>
                : result.monster_refresh?.available === false && <span>下次刷新暂不可用</span>)}
        </div>}
        {monsters === undefined ? <p className="plan-missing">{preview ? "刷新候选未记录" : "刷新后的怪池未记录"}</p> : !monsters.length ? <p className="plan-missing">返回的怪池为空</p> : <>
            {preservedGroups.map(({ key, title, note }) => groups[key].length > 0 && <section className="refresh-preserved-group" aria-label={title} key={key}>
                <header><h4>{title}</h4><span>{note || `${groups[key].length} 个`}</span></header>
                <ul>{groups[key].map(({ monster, index }) => <li key={index}>
                    <Portrait id={monster.archetype_id} name={monster.name || "名称未记录"} monster />
                    <strong>{monster.name || "名称未记录"}</strong>
                    <span>{key === "locked" ? MONSTER_ROLES[monster.tier] || "身份未记录" : "固定挑战"}</span>
                </li>)}</ul>
            </section>)}
            {groups.refreshed.length > 0 && <section className="refresh-monster-group" aria-label={preview ? "待替换候选" : "本次新刷出的怪物"}>
                <header><h4>{preview ? "待替换候选" : "本次新刷出"}</h4><span>{groups.refreshed.length} 个</span></header>
                <MonsterResults rows={groups.refreshed} entry={entry} />
            </section>}
            {groups.unknown.length > 0 && <section className="refresh-monster-group" aria-label="未分类的返回怪物">
                <header><h4>返回的怪物</h4><span>{groups.unknown.length} 个</span></header>
                <p className="plan-missing">原怪池或保留标记未记录，无法区分保留与替换。</p>
                <MonsterResults rows={groups.unknown} entry={entry} />
            </section>}
            {preview && <p className="refresh-reference-note">候选仅供预览，不能直接用于锁定或讨伐。</p>}
        </>}
        <section className="action-inspection" aria-label="排查详情">
            <ActionChangeDetails entry={entry} changes={changes} />
            {changes.other.length > 0 && <details className="plan-disclosure"><summary>其他实际变化</summary><ActionChangePanel entry={entry} changes={{ ...changes, resources: [], members: [] }} showDetails={false} /></details>}
            {monsters !== undefined && <ReturnedFields key={`monsters:${entry.frame.cursor}`} value={monsters} label={preview ? "完整候选字段" : "完整怪池字段"} />}
            {entry.returned.tables.filter(table => table.kind !== "monsters").map((table, index) => <ReturnedFields key={`${entry.frame.cursor}:${index}`} value={table.records} label={`${table.title}完整字段`} />)}
            {Object.keys(entry.returned.fields).length > 0 && <ReturnedFields key={`extra:${entry.frame.cursor}`} value={entry.returned.fields} label="其他返回字段" />}
            {inspection}
        </section>
    </section>;
}
