import { useState } from "react";
import { ActionInputs } from "./ActionInputPanel";
import { actionResult } from "./actionModel";
import type { ActionEntry } from "./actionModel";
import { returnLabel } from "./actionReturns";
import type { ReturnTable } from "./actionReturns";

const ENUMS: Record<string, string> = { left_win: "冒险者胜利", right_win: "怪物胜利", draw: "平局", actual: "真实战斗", preview: "模拟预览", left: "冒险者方", right: "怪物方" };
function ReturnedValue({ value, fieldKey = "" }: { value: unknown; fieldKey?: string }) {
    if (value === null) return <span>空值（null）</span>;
    if (Array.isArray(value)) return value.length ? <ol className="return-value-list">{value.map((item, i) => <li key={i}><ReturnedValue value={item} /></li>)}</ol> : <span>空列表 []</span>;
    if (typeof value === "object") return Object.keys(value).length ? <dl className={`return-field-list ${Object.values(value).every(item => item === null || typeof item !== "object") ? "is-flat" : ""}`}>{Object.entries(value).map(([key, item]) => <div key={key}>
        <dt>{returnLabel(key)}</dt><dd><ReturnedValue value={item} fieldKey={key} /></dd>
    </div>)}</dl> : <span>空对象</span>;
    return <span className="return-text">{typeof value === "boolean" ? value ? "是（true）" : "否（false）" : (["source", "outcome", "actor_side", "target_side", "healing_target_side"].includes(fieldKey) && Object.hasOwn(ENUMS, String(value)) ? ENUMS[String(value)] : undefined) || String(value)}</span>;
}
function ResultTable({ data }: { data: ReturnTable }) {
    const [page, setPage] = useState(0);
    const [search, setSearch] = useState("");
    const [expanded, setExpanded] = useState<number | null>(null);
    const count = 6;
    const matches = data.rows.map((row, index) => ({ row, index })).filter(({ row, index }) => `${row.join(" ")} ${JSON.stringify(data.records[index])}`.toLowerCase().includes(search.trim().toLowerCase()));
    const current = Math.min(page, Math.max(0, Math.ceil(matches.length / count) - 1));
    return <section className="action-return-table" aria-label={data.title}>
        <header><h4>{data.title}</h4>{data.rows.length > count && <input type="search" aria-label={`筛选${data.title}`} placeholder="筛选已返回内容" value={search} onChange={event => { setSearch(event.target.value); setPage(0); setExpanded(null); }} />}<span>{search ? `${matches.length} / ` : ""}{data.rows.length} 条</span></header>
        {matches.length ? <table><thead><tr>{data.columns.map(column => <th scope="col" key={column}>{column}</th>)}</tr></thead>
            <tbody>{matches.slice(current * count, (current + 1) * count).map(({ row, index }) => {
                return <ResultRow key={index} row={row} record={data.records[index]} columns={data.columns.length}
                    expanded={expanded === index} onExpand={() => setExpanded(expanded === index ? null : index)} />;
            })}</tbody></table> : <p className="muted">{data.rows.length ? "没有匹配的已返回内容。" : "本次实际返回空列表。"}</p>}
        {matches.length > count && <div className="return-pagination">
            <button type="button" disabled={current === 0} onClick={() => { setPage(current - 1); setExpanded(null); }}>上一页</button>
            <span>已记录返回 · {current + 1} / {Math.ceil(matches.length / count)}</span>
            <button type="button" disabled={(current + 1) * count >= matches.length} onClick={() => { setPage(current + 1); setExpanded(null); }}>下一页</button>
        </div>}
    </section>;
}
function ResultRow({ row, record, columns, expanded, onExpand }: { row: string[]; record: Record<string, unknown>; columns: number; expanded: boolean; onExpand: () => void }) {
    return <><tr>{row.map((cell, i) => <td key={i}>{i === 0 ? <button type="button" className="return-object" aria-expanded={expanded} title="查看此条记录的可读字段" onClick={onExpand}>{cell}</button> : cell}</td>)}</tr>
        {expanded && <tr className="return-record-fields"><td colSpan={columns}><ReturnedValue value={record} /></td></tr>}
    </>;
}

export function ActionResults({ entry }: { entry: ActionEntry }) {
    const returned = entry.returned;
    return <>
        <section className="action-result" aria-label="执行结果"><h4>执行结果</h4>
            <p className={entry.status === "failed" ? "action-detail-warning" : "action-detail-summary"}>{actionResult(entry)}</p>
            {returned.preview && <p className="action-preview-note">这是查询或模拟返回，不代表实际发生的讨伐、刷新或收益。</p>}
        </section>
        {entry.kind !== "system" && <ActionInputs input={entry.input} />}
        {(returned.tables.length > 0 || Object.keys(returned.fields).length > 0) && <section className="action-returns" aria-label="业务返回">
            {returned.tables.map((table, index) => <ResultTable key={`${index}:${entry.frame.cursor}`} data={table} />)}
            {Object.keys(returned.fields).length > 0 && <section className="action-return-extra"><h4>其他返回字段</h4><ReturnedValue value={returned.fields} /></section>}
        </section>}
        {entry.effects.length > 0 && <section className="action-effects"><h4>实际局面变化</h4><ul>{entry.effects.map((effect, i) => <li key={i}>{effect}</li>)}</ul></section>}
    </>;
}
