import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { TrajectoryOverview } from "./TrajectoryOverview";
import { ToolCallSummary } from "./ToolCallSummary";
import { ArrowSquareOut, ClockCounterClockwise, MagnifyingGlass, CaretDown } from "@phosphor-icons/react";
import { ACTION_STATUS, actionSummary } from "./actionModel";
import { inputPreview } from "./actionInputs";
import { TOOL_CALL_STATUS, toolCategoryLabel, summarizeToolCalls } from "./toolCalls";
import type { Selection } from "./sceneModel";
import { useTrajectories } from "./TrajectoryContext";
import { confirmed, historyAction, LIFECYCLE, matchesOperation, matchesTrajectoryState, objectActionTitle, OPERATION_CATEGORIES, subjectRows, trajectorySearch } from "./trajectoryModel";
import type { OperationCategory, TrajectoryRow, TrajectoryState } from "./trajectoryModel";
import "./trajectories.css";

function HistoryState() {
    const history = useTrajectories()!;
    return <>
        {history.pending && <p className="trajectory-note">正在读取整局轨迹…</p>}
        {history.error && <p role="alert">轨迹读取失败：{history.error.message} <button onClick={history.refresh}>重试</button></p>}
        {history.data?.warning && <p className="trajectory-warning">{history.data.warning}</p>}
        {history.locationError && <p role="alert">无法定位：{history.locationError}</p>}
    </>;
}
export function TrajectoryResult({ row, subject }: { row: TrajectoryRow; subject?: Selection }) {
    const history = useTrajectories()!;
    const entry = historyAction(row);
    const title = objectActionTitle(row, subject) || entry.title;
    const instances = row.subjects.filter(item => item.kind === "equipment" && (subject?.kind !== "equipment" || item.id === subject.id)).map(item => item.id).join("、");
    return <button className={`trajectory-result ${row.lifecycle} ${entry.status}`} disabled={history.locating}
        aria-pressed={history.selectedCursor === row.cursor}
        aria-label={`查看轨迹 回合${row.turn ?? "未知"} ${title}${instances ? ` / 实例 ${instances}` : ""}`} onClick={() => history.locate(row)}>
        <span className="trajectory-when"><b>第 {row.turn ?? "未知"} 回合</b><small>操作 {row.ordinal}</small></span>
        <span className="trajectory-content"><strong>{title}</strong>
            {title !== entry.title && <small>对应行动：{entry.title}</small>}
            <small>{actionSummary(entry)}</small>
            <small className="action-input-preview">输入 · {inputPreview(entry.input)}</small>
            <span className="trajectory-tags"><span>{toolCategoryLabel(row.event)}</span><span className={`action-status is-${entry.status}`}>{ACTION_STATUS[entry.status]}</span>
                <span>{LIFECYCLE[row.lifecycle]}</span>
                {history.viewTurn != null && row.turn != null && row.turn > history.viewTurn && <span>后续回合</span>}
                {row.subjects.filter(s => s.kind === "equipment").map(s => <span className="mono" key={s.id}>实例 {s.id}</span>)}
            </span>
        </span><ArrowSquareOut size={14} />
    </button>;
}
export function RunTrajectories({ detail }: { detail?: ReactNode }) {
    const history = useTrajectories()!;
    const [search, setSearch] = useState("");
    const [type, setType] = useState("all");
    const [state, setState] = useState<TrajectoryState>("all");
    const [from, setFrom] = useState("");
    const [to, setTo] = useState("");
    const [page, setPage] = useState(0);
    const viewport = useRef<HTMLDivElement>(null);
    const entries = history.data?.entries;
    const rows = useMemo(() => (entries || []).filter(row =>
        (!search.trim() || trajectorySearch(row).includes(search.trim().toLowerCase())) &&
        matchesOperation(row, type) && matchesTrajectoryState(row, state) &&
        (!from || row.turn != null && row.turn >= Number(from)) && (!to || row.turn != null && row.turn <= Number(to))
    ), [entries, search, type, state, from, to]);
    const tools = useMemo(() => summarizeToolCalls((entries || []).map(row => row.event)), [entries]);
    const lastPage = Math.max(0, Math.ceil(rows.length / 40) - 1);
    const current = Math.min(page, lastPage);
    useLayoutEffect(() => { if (viewport.current) viewport.current.scrollTop = 0; }, [search, type, state, from, to, current]);
    function change(update: () => void) { update(); setPage(0); }
    function quickFilter(nextType = "all", nextState: TrajectoryState = "all") {
        setSearch(""); setFrom(""); setTo(""); setPage(0);
        setType(nextType); setState(nextState);
    }
    return <div className="trajectory-browser" aria-label="整局行动检索">
        <TrajectoryOverview type={type} state={state}
            onCategory={(category: OperationCategory) => quickFilter(`category:${category}`)}
            onState={state => quickFilter("all", state)} onAll={() => quickFilter()} />
        <div className={`trajectory-browser-body ${detail ? "has-detail" : ""}`}>
        <section className="trajectory-search-panel" aria-label="观战行动时间线">
        <div className="trajectory-filters">
            <label className="trajectory-search"><MagnifyingGlass size={15} /><input aria-label="搜索整局行动" placeholder="天赋、装备、成员名称或 ID" value={search} onChange={e => change(() => setSearch(e.target.value))} /></label>
            <select aria-label="筛选轨迹操作" value={type} onChange={e => change(() => setType(e.target.value))}>
                <option value="all">全部工具调用</option>
                <optgroup label="操作类别">{OPERATION_CATEGORIES.map(category => <option key={category.id} value={`category:${category.id}`}>{category.label}</option>)}</optgroup>
                <optgroup label="具体工具">{tools.map(tool => <option key={tool.name} value={tool.name}>{tool.label}（{tool.total}）</option>)}</optgroup>
            </select>
            <select aria-label="筛选轨迹状态" value={state} onChange={e => change(() => setState(e.target.value as TrajectoryState))}>
                <option value="all">全部记录</option>
                <optgroup label="执行结果">{Object.entries(TOOL_CALL_STATUS).map(([status, label]) => <option key={status} value={status}>{label}</option>)}</optgroup>
                <optgroup label="回合状态"><option value="confirmed">已确认成功</option><option value="uncommitted">回合未提交</option><option value="rolled_back">已回滚</option></optgroup>
            </select>
            <label className="trajectory-range">回合<input aria-label="轨迹起始回合" type="number" min="1" value={from} onChange={e => change(() => setFrom(e.target.value))} /><span>至</span><input aria-label="轨迹结束回合" type="number" min="1" value={to} onChange={e => change(() => setTo(e.target.value))} /></label>
        </div>
        <HistoryState />
        <p className="trajectory-note">{history.data?.origin === "execution" ? "当前运行阶段" : "整局记录"} · {rows.length} 条匹配 · 包含续跑前历史，可能晚于当前观看位置</p>
        <div className="trajectory-results" ref={viewport}>
            {history.data && <ToolCallSummary tools={tools} selected={type} onTool={name => quickFilter(name)} label="整局工具汇总" />}
            {rows.slice(current * 40, (current + 1) * 40).map(row => <TrajectoryResult key={row.id} row={row} />)}
            {!history.pending && !history.error && !rows.length && <p className="trajectory-note">没有匹配的已记录行动。缺失日志不能证明从未购买或制作。</p>}
        </div>
        {lastPage > 0 && <div className="trajectory-pagination"><button disabled={current === 0} onClick={() => setPage(current - 1)}>上一页</button><span>{current + 1} / {lastPage + 1}</span><button disabled={current === lastPage} onClick={() => setPage(current + 1)}>下一页</button></div>}
        </section>
        {detail}
        </div>
    </div>;
}
export function ObjectTrajectories({ subject }: { subject: Selection }) {
    const history = useTrajectories();
    if (!history || !["upgrade", "recipe", "equipment"].includes(subject.kind)) return null;
    return <ObjectHistory key={`${subject.kind}:${subject.id}`} subject={subject} />;
}
function ObjectHistory({ subject }: { subject: Selection }) {
    const history = useTrajectories()!;
    const [limit, setLimit] = useState(6);
    const rows = subjectRows(history.data?.entries || [], subject);
    const successes = rows.filter(confirmed);
    const origins = successes.filter(row => subject.kind === "upgrade" ? row.event.name === "purchase_upgrade" : row.event.name === "craft_equipment");
    const pending = rows.filter(row => row.lifecycle === "uncommitted" && row.event.result?.ok === true &&
        row.event.name === (subject.kind === "upgrade" ? "purchase_upgrade" : "craft_equipment"));
    const first = origins[0];
    let summary = subject.kind === "upgrade" ? "尚无已确认的解锁记录" : "制作回合未记录";
    if (first) {
        if (subject.kind === "upgrade") summary = `第 ${first.turn ?? "未知"} 回合解锁`;
        else if (subject.kind === "recipe") summary = `已确认制作 ${origins.length} 次 · 首次第 ${first.turn ?? "未知"} 回合`;
        else summary = `第 ${first.turn ?? "未知"} 回合制作`;
    } else if (pending.length) {
        summary = subject.kind === "recipe" ? `制作 ${pending.length} 次 · 回合未提交` :
            `第 ${pending[0].turn ?? "未知"} 回合${subject.kind === "upgrade" ? "购买成功" : "制作"} · 回合未提交`;
    }
    if (first && pending.length) summary += ` · 另 ${pending.length} 次未提交`;
    const firstRecorded = first || pending[0];
    if (firstRecorded?.turn != null && history.viewTurn != null && firstRecorded.turn > history.viewTurn) summary += " · 后续回合";
    return <details className="object-trajectories" key={`${subject.kind}:${subject.id}`}>
        <summary><ClockCounterClockwise size={14} /><strong>本局轨迹</strong><span>{history.error ? "轨迹读取失败" : history.pending ? "正在读取…" : summary}</span><b>{rows.length} 条</b><CaretDown size={13} /></summary>
        <HistoryState />
        <p className="trajectory-note">整局历史，不代表当前观看快照。点击记录查看当时局面。</p>
        {rows.slice(0, limit).map(row => <TrajectoryResult key={row.id} row={row} subject={subject} />)}
        {rows.length > limit && <button className="text-button" onClick={() => setLimit(limit + 10)}>再显示 10 条</button>}
        {!history.pending && !rows.length && <p className="trajectory-note">原日志未记录此对象的操作，不能确定获取回合。</p>}
    </details>;
}
