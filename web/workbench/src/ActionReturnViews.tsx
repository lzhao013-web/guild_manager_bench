import { useState } from "react";
import type { ActionEntry } from "./actionModel";
import { returnLabel, returnNames } from "./actionReturns";
import type { ReturnTable } from "./actionReturns";
import { fmt } from "./model";
import { MATERIALS, SLOTS, STAT_NAMES } from "./sceneModel";
import { ResizableTable } from "./ResizableTable";

const ENUMS: Record<string, string> = { left_win: "冒险者胜利", right_win: "怪物胜利", draw: "平局", actual: "真实战斗", preview: "模拟预览", left: "冒险者方", right: "怪物方" };
export function ReturnedValue({ value, fieldKey = "" }: { value: unknown; fieldKey?: string }) {
    if (value === null) return <span>空值（null）</span>;
    if (Array.isArray(value)) return value.length ? <ol className="return-value-list">{value.map((item, i) => <li key={i}><ReturnedValue value={item} /></li>)}</ol> : <span>空列表 []</span>;
    if (typeof value === "object") return Object.keys(value).length ? <dl className={`return-field-list ${Object.values(value).every(item => item === null || typeof item !== "object") ? "is-flat" : ""}`}>{Object.entries(value).map(([key, item]) => <div key={key}>
        <dt>{returnLabel(key)}</dt><dd><ReturnedValue value={item} fieldKey={key} /></dd>
    </div>)}</dl> : <span>空对象</span>;
    return <span className="return-text">{typeof value === "boolean" ? value ? "是（true）" : "否（false）" : (["source", "outcome", "actor_side", "target_side", "healing_target_side"].includes(fieldKey) && Object.hasOwn(ENUMS, String(value)) ? ENUMS[String(value)] : undefined) || String(value)}</span>;
}

export function ResultTable({ data, dense = false }: { data: ReturnTable; dense?: boolean }) {
    const [page, setPage] = useState(0);
    const [search, setSearch] = useState("");
    const [expanded, setExpanded] = useState<number | null>(null);
    const count = dense ? 8 : 6;
    const label = (column: string) => dense ? column.replace(/ \/ (编号|实例|引用)$/, "") : column;
    const matches = data.rows.map((row, index) => ({ row, index })).filter(({ row, index }) => `${row.join(" ")} ${JSON.stringify(data.records[index])}`.toLowerCase().includes(search.trim().toLowerCase()));
    const current = Math.min(page, Math.max(0, Math.ceil(matches.length / count) - 1));
    return <section className={`action-return-table ${dense ? "is-dense" : ""}`} aria-label={dense ? data.title : `${data.title}明细`}>
        <header><h4>{data.title}</h4>{data.rows.length > count && <input type="search" aria-label={`筛选${data.title}`} placeholder="筛选内容" value={search} onChange={event => { setSearch(event.target.value); setPage(0); setExpanded(null); }} />}<span>{search ? `${matches.length} / ` : ""}{data.rows.length} 条</span></header>
        {matches.length ? <ResizableTable scroll storageKey={`action-return:${dense ? "dense:" : ""}${data.title}:${JSON.stringify([...data.columns].sort())}`}
            columns={data.columns.map(column => ({ id: column, label: label(column), minWidth: dense ? 70 : 100 }))}>
            <thead><tr>{data.columns.map(column => <th scope="col" key={column}>{label(column)}</th>)}</tr></thead>
            <tbody>{matches.slice(current * count, (current + 1) * count).map(({ row, index }) => <ResultRow key={index} row={row} record={data.records[index]} columns={data.columns.length} dense={dense}
                expanded={expanded === index} onExpand={() => setExpanded(expanded === index ? null : index)} />)}</tbody>
        </ResizableTable> : <p className="muted">{data.rows.length ? "没有匹配结果" : "无条目"}</p>}
        {matches.length > count && <div className="return-pagination">
            <button type="button" disabled={current === 0} onClick={() => { setPage(current - 1); setExpanded(null); }}>上一页</button>
            <span>{current + 1} / {Math.ceil(matches.length / count)}</span>
            <button type="button" disabled={(current + 1) * count >= matches.length} onClick={() => { setPage(current + 1); setExpanded(null); }}>下一页</button>
        </div>}
    </section>;
}
function ResultRow({ row, record, columns, expanded, onExpand, dense }: { row: string[]; record: Record<string, unknown>; columns: number; expanded: boolean; onExpand: () => void; dense: boolean }) {
    return <><tr>{row.map((cell, i) => <td key={i}>{i === 0 ? <button type="button" className="return-object" aria-expanded={expanded} title={dense ? cell : undefined} onClick={onExpand}>{dense ? cell.split("\n")[0] : cell}</button> : dense ? <span className="return-cell-summary" title={cell}>{cell.replace(/\n/g, "，")}</span> : cell}</td>)}</tr>
        {expanded && <tr className="return-record-fields"><td colSpan={columns}><ReturnedValue value={record} /></td></tr>}
    </>;
}
export function StatValues({ stats }: { stats: Record<string, number> | undefined }) {
    if (stats == null) return null;
    const fields = Object.entries(stats);
    const nonzero = fields.filter(([, value]) => value !== 0);
    return <dl className="return-stat-grid">{(nonzero.length ? nonzero : fields).map(([key, value]) => <div key={key}><dt>{STAT_NAMES[key] || key}</dt><dd className="mono">{fmt(value)}</dd></div>)}</dl>;
}
function Cost({ item }: { item: Record<string, any> }) {
    const gold = item.gold_cost ?? item.recruit_gold;
    return <dl className="return-stat-grid return-cost">
        {gold != null && <div><dt>金币</dt><dd className="mono">{fmt(gold)}</dd></div>}
        {item.experience_cost != null && <div><dt>经验</dt><dd className="mono">{fmt(item.experience_cost)}</dd></div>}
        {Object.entries(item.material_costs || {}).map(([key, value]) => <div key={key}><dt>{MATERIALS[key] || key}</dt><dd className="mono">{String(value)}</dd></div>)}
    </dl>;
}
function Skills({ skills }: { skills: Record<string, any>[] | undefined }) {
    return skills && skills.length > 0 && <div className="return-skills"><span>技能</span><ul>{skills.slice(0, 3).map((skill, i) => <li key={i}>{skill.name || skill.skill_id}</li>)}</ul>{skills.length > 3 && <details className="plan-disclosure"><summary>其余 {skills.length - 3} 个技能</summary><ul>{skills.slice(3).map((skill, i) => <li key={i}>{skill.name || skill.skill_id}</li>)}</ul></details>}</div>;
}
function EntityResult({ item, data, index, names }: { item: Record<string, any>; data: ReturnTable; index: number; names: Map<string, string> }) {
    const [expanded, setExpanded] = useState(false);
    const kind = data.kind;
    const name = item.name || item.output_name || data.rows[index][0].split("\n")[0];
    const level = item.level;
    const status = kind === "upgrades" ? data.rows[index][1] : kind === "recruits" || kind === "recipes" ? data.rows[index][2] : "";
    const stats = kind === "members" ? item.effective_stats : kind === "recruits" ? item.effective_stats || item.base_stats : kind === "recipes" ? item.output_stats : item.stats || item.base_stats;
    const skills = kind === "recipes" ? item.output_skills : item.skills;
    const slot = item.slot || item.output_slot;
    const owner = item.equipped_by;
    return <article className="return-entity" aria-label={name}>
        <header><strong>{name}</strong><span>{level != null && <b className="mono">Lv.{level}</b>}{slot && <span>{SLOTS[slot] || slot}</span>}{status && <span>{status}</span>}{item.turn != null && <span className="mono">T{item.turn}</span>}</span></header>
        {kind === "equipment" && <p className="return-entity-owner">{owner === null ? "未穿戴" : owner == null ? "穿戴者未记录" : names.get(String(owner)) || String(owner)}</p>}
        {item.description && <p className="return-description">{item.description}</p>}
        {kind === "recruits" && <span className="muted return-stat-caption">{item.effective_stats ? "入队属性" : "基础属性"}</span>}
        {kind === "members" && item.resources && <dl className="return-stat-grid"><div><dt>当前生命</dt><dd className="mono">{item.resources.current_hp ?? "未记录"} / {item.effective_stats?.hp ?? "未记录"}</dd></div><div><dt>当前法力</dt><dd className="mono">{item.resources.current_mp ?? "未记录"} / {item.effective_stats?.mp ?? "未记录"}</dd></div></dl>}
        <StatValues stats={stats} />
        {typeof item.party_size_bonus === "number" && item.party_size_bonus !== 0 && <p>编队容量 +{item.party_size_bonus}</p>}
        <Skills skills={skills} />
        <Cost item={item} />
        {(item.reward || item.base_reward) && <div className="return-reward"><span>{item.base_reward ? "基础奖励" : "目标奖励"}</span><RewardValues reward={item.reward || item.base_reward} /></div>}
        <details className="plan-disclosure" onToggle={event => setExpanded(event.currentTarget.open)}><summary>完整字段</summary>{expanded && <ReturnedValue value={item} />}</details>
    </article>;
}
export function RewardValues({ reward }: { reward: Record<string, any> }) {
    return <dl className="return-stat-grid"><div><dt>金币</dt><dd className="mono">{reward.gold == null ? "未记录" : fmt(reward.gold)}</dd></div><div><dt>经验</dt><dd className="mono">{reward.experience == null ? "未记录" : fmt(reward.experience)}</dd></div>
        {Object.entries(reward.materials || {}).map(([key, value]) => <div key={key}><dt>{MATERIALS[key] || key}</dt><dd className="mono">{String(value)}</dd></div>)}
    </dl>;
}
export function ReturnCollection({ data, entry }: { data: ReturnTable; entry: ActionEntry }) {
    const names = returnNames(entry.frame);
    if (data.kind === "tactics") return <section className="return-tactics" aria-label={data.title}><h4>{data.title}</h4>{data.records.map((pair, index) => <div key={pair.key}>
        <span>{data.rows[index][0]}</span>{Array.isArray(pair.value) && pair.value.length ? pair.key === "skill_priority" ? <ol>{pair.value.map((id: string, i: number) => <li key={i}>{names.get(id) || id}</li>)}</ol>
            : <ul>{pair.value.map((id: string, i: number) => <li key={i}>{names.get(id) || id}</li>)}</ul> : <p>{data.rows[index][1]}</p>}
    </div>)}</section>;
    if (data.kind === "battles" || data.kind === "events") return <ResultTable data={data} />;
    if (entry.kind === "query") return <ResultTable data={data} dense />;
    return <section className="return-collection" aria-label={data.title}>
        <header><h4>{data.title}</h4><span>{data.records.length} 条</span></header>
        {data.records.length ? data.records.slice(0, 3).map((item, index) => <EntityResult key={index} item={item} data={data} index={index} names={names} />) : <p className="muted">无条目</p>}
        {data.records.length > 3 && <details className="plan-disclosure"><summary>完整列表（{data.records.length} 条）</summary><ResultTable data={data} /></details>}
    </section>;
}
