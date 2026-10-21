import { useEffect, useMemo, useRef, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowsClockwise, X } from "@phosphor-icons/react";
import { api } from "./api";
import { CurvePlot } from "./CurvePlot";
import type { CurveSeries } from "./CurvePlot";
import { conditionWarning, CURVE_COLORS, curveChoices, latestCommonTurn, milestoneCategory, selectedCurves } from "./curveModel";
import type { CurveData, CurveMetric } from "./curveModel";
import type { RunCatalog } from "./runCatalog";
import type { Archive } from "./types";
import { STATUS } from "./model";
import { Empty } from "./ui";
import "./curves.css";

const curveNumber = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4 });

export function CurveBoard({ catalog, selectedIds, onSelection, onBack }: {
    catalog: RunCatalog; selectedIds: string[]; onSelection: (ids: string[]) => void; onBack?: () => void;
}) {
    const client = useQueryClient();
    const archives = useQuery({ queryKey: ["archives"], queryFn: () => api<{ runs: Archive[] }>("/api/llm/runs"), staleTime: 15000 });
    const choices = useMemo(() => curveChoices(catalog, archives.data?.runs || []), [catalog, archives.data]);
    const selected = selectedCurves(selectedIds, choices, catalog);
    const signature = JSON.stringify(selected.map(choice => choice.key));
    const queries = useQueries({ queries: selected.map(choice => ({
        queryKey: ["run-curves", choice.url], queryFn: ({ signal }: { signal: AbortSignal }) => api<CurveData>(choice.url, { signal }),
        refetchInterval: ["queued", "starting", "running", "stopping"].includes(choice.status) ? 2500 : false,
        staleTime: 1500, retry: 1,
    })) });
    const previous = useRef<Map<string, string>>(new Map());
    const revisions = JSON.stringify(selected.map(choice => [choice.key, choice.revision]));
    useEffect(() => {
        for (const choice of selected) {
            const before = previous.current.get(choice.key);
            if (before !== undefined && before !== choice.revision)
                void client.invalidateQueries({ queryKey: ["run-curves", choice.url] });
        }
        previous.current = new Map(selected.map(choice => [choice.key, choice.revision]));
    }, [revisions, client]);
    const [mode, setMode] = useState<"income" | "balance">("income");
    const [start, setStart] = useState(1);
    const [end, setEnd] = useState<number | null>(null);
    const [located, setLocated] = useState<number | null>(null);
    useEffect(() => { setStart(1); setEnd(null); setLocated(null); }, [signature]);
    const series: CurveSeries[] = selected.flatMap((choice, index) => queries[index].data ? [{ key: choice.key, number: index + 1, name: `${choice.name} · ${choice.key.slice(-6)}`, color: CURVE_COLORS[index], data: queries[index].data! }] : []);
    const maxTurn = Math.max(1, ...series.flatMap(run => run.data.points.map(point => point.turn)));
    const from = Math.min(start, maxTurn), to = Math.max(from, Math.min(end ?? maxTurn, maxTurn));
    const turn = Math.max(from, Math.min(located ?? to, to));
    const metrics: { key: CurveMetric; title: string; note: string }[] = [
        { key: mode === "income" ? "gold_income" : "gold", title: "经济", note: mode === "income" ? "累计讨伐金币" : "结算后金币余额" },
        { key: mode === "income" ? "experience_income" : "experience_pool", title: "经验", note: mode === "income" ? "累计讨伐经验" : "结算后未分配经验池" },
        { key: "rank_score", title: "战力", note: "正式记录 Rank Score" },
    ];
    const commonTurn = latestCommonTurn(queries.map(query => query.data), metrics.map(metric => metric.key), from, to);
    const recordedCount = queries.filter(query => {
        const point = query.data?.points.find(point => point.turn === turn);
        return point && metrics.every(metric => point[metric.key] !== null);
    }).length;
    const runWarnings = selected.flatMap((choice, index) => !queries[index].isError && queries[index].data?.warning
        ? [{ key: choice.key, name: choice.name, message: queries[index].data!.warning! }] : []);
    const warningMessages = [...new Set(runWarnings.map(item => item.message))];
    const warning = conditionWarning(series.filter(run => run.data.points.length).map(run => run.data));
    const hasPoints = series.some(run => run.data.points.length);
    const milestones = series.flatMap(run => (run.data.milestones ?? []).filter(event => event.turn === turn).map(event => ({ run, event })));
    const categoryLabels = { economy: "经济", experience: "经验", power: "战力" };
    const setSelected = (ids: string[]) => onSelection([...new Set(ids)].slice(0, 4));
    return <section className="curve-board" aria-label="统一曲线对比工作区" tabIndex={0}>
        <div className="curve-selection-bar">
            {onBack && <button className="text-button curve-back" onClick={onBack}><ArrowLeft size={15} />返回原工作区</button>}
            <select aria-label="添加曲线 run" value="" disabled={selected.length >= 4} onChange={event => { if (event.target.value) setSelected([...selected.map(choice => choice.key), event.target.value]); }}>
                <option value="">选择 run 加入比较（最多四个）…</option>
                {choices.filter(choice => !selected.some(item => item.key === choice.key)).map(choice => <option key={choice.key} value={choice.key}>{choice.name} / {choice.preset} / {choice.key.slice(-6)}</option>)}
            </select>
            <button className="refresh-button" aria-label="刷新曲线" title="重新读取已记录数据" onClick={() => { void client.invalidateQueries({ queryKey: ["run-curves"] }); void archives.refetch(); }}><ArrowsClockwise size={17} /></button>
            {!!selected.length && <button className="text-button" onClick={() => onSelection([])}>清除选择</button>}
        </div>
        {archives.isError && <p className="callout error" role="alert">CLI 存档目录读取失败：{archives.error.message}。工作台已知 run 仍可选择。</p>}
        {!!selectedIds.length && !selected.length && !archives.isPending && <p className="callout">未找到所选 run，请重新选择。原记录可能已移动。</p>}
        {!!selected.length && <div className="curve-legend" aria-label="曲线图例">
            {selected.map((choice, index) => <div key={choice.key}>
                <i style={{ background: CURVE_COLORS[index] }} /><span><strong title={`${choice.name} / ${choice.preset || "未记录规则"} / ${choice.key}`}>{index + 1}. {choice.name}</strong>
                    <small title={choice.key.startsWith("archive:") ? "CLI 状态来自存档标记，不代表进程仍存活" : choice.preset}>{choice.key.startsWith("archive:") && "CLI · "}{choice.key.slice(-6)} · {STATUS[choice.status as keyof typeof STATUS] || choice.status}{queries[index].data ? ` · 已记 ${queries[index].data!.points.length}/${queries[index].data!.max_turns ?? "?"} 回合` : ""}</small></span>
                <button aria-label={`移除曲线 ${choice.name}`} title="移除此曲线" onClick={() => setSelected(selected.filter(item => item.key !== choice.key).map(item => item.key))}><X size={14} /></button>
            </div>)}
        </div>}
        {selected.map((choice, index) => queries[index].isError ? <p className="callout error" role="alert" key={choice.key}>{choice.name}：{queries[index].error!.message}。若服务尚未加载新接口，请等运行结束后重启 Web 服务。</p> : null)}
        {!!runWarnings.length && <details className="curve-warning-summary" aria-label="曲线风险提示">
            <summary><span role="status">{runWarnings.length} 个 run：{warningMessages.length === 1 ? warningMessages[0] : "存在版本或记录风险，请展开查看详情"}</span><span className="curve-warning-toggle">详情</span></summary>
            <ul>{runWarnings.map(item => <li key={item.key}><b>{item.name} · {item.key.slice(-6)}</b>：{item.message}</li>)}</ul>
        </details>}
        {warning && <p className="curve-warning" role="status">{warning}</p>}
        {!selected.length ? <Empty title="选择一至四个 run 开始比较">单局、运行列表、历史存档和多路观战都使用这个工作区。支持工作台任务和 CLI 存档。</Empty> : !hasPoints ? <Empty title={queries.some(query => query.isPending) ? "正在读取曲线" : "尚无可读取的结算曲线"}>只绘制实际已记录的数据。未完成的回合和缺失数值不会补零。</Empty> : <>
            <div className="curve-controls">
                <div className="curve-mode" aria-label="资源曲线口径">
                    <button aria-pressed={mode === "income"} onClick={() => setMode("income")}>累计讨伐收入</button>
                    <button aria-pressed={mode === "balance"} onClick={() => setMode("balance")}>结算后余额</button>
                </div>
                <label>回合范围 <input aria-label="曲线起始回合" type="number" min={1} max={maxTurn} value={from} onChange={event => setStart(Math.max(1, Math.min(maxTurn, Number(event.target.value))))} /></label>
                <span>至</span><input aria-label="曲线结束回合" type="number" min={from} max={maxTurn} placeholder={`最新 ${maxTurn}`} value={end === null ? "" : to} onChange={event => setEnd(event.target.value ? Math.max(from, Math.min(maxTurn, Number(event.target.value))) : null)} />
                <button className="text-button" onClick={() => { setStart(1); setEnd(null); setLocated(null); }}>全程 / 跟随最新</button>
                <button className="curve-common-turn" disabled={commonTurn === null} title={commonTurn === null ? "当前范围内没有全部所选 run 均具备三项指标的回合" : `定位到回合 ${commonTurn}，不改变曲线范围`} onClick={() => { if (commonTurn !== null) setLocated(commonTurn); }}>最近共同已记录回合</button>
                <div className="curve-coverage"><span>当前回合 {turn}：{recordedCount} / {selected.length} 路具备三项指标</span>{commonTurn === null && <span>当前范围内没有共同已记录的三项指标回合</span>}</div>
            </div>
            <div className="curve-charts">{metrics.map(metric => <CurvePlot key={metric.title} title={metric.title} note={metric.note} metric={metric.key} series={series} from={from} to={to} turn={turn} onTurn={setLocated} />)}</div>
            <section className="curve-values" aria-label="同回合数值与差值">
                <header><h2>回合 {turn} 对比</h2><label>定位回合 <input aria-label="定位对比回合" type="number" min={from} max={to} value={turn} onChange={event => setLocated(Math.max(from, Math.min(to, Number(event.target.value))))} /></label><span>悬停图表或用方向键联动，差值相对首个 run</span></header>
                <table><thead><tr><th>Run / 模型</th>{metrics.map(metric => <th key={metric.key}>{metric.note}</th>)}</tr></thead>
                    <tbody>{selected.map((choice, index) => {
                        const point = queries[index].data?.points.find(point => point.turn === turn);
                        const baseline = queries[0].data?.points.find(point => point.turn === turn);
                        return <tr key={choice.key}><th><div className="curve-run-label"><i style={{ background: CURVE_COLORS[index] }} /><strong title={choice.name}>{choice.name}</strong><small>{choice.key.slice(-6)}{index === 0 ? " · 基准" : ""}</small></div></th>
                            {metrics.map(metric => { const value = point?.[metric.key], base = baseline?.[metric.key]; return <td key={metric.key} data-label={metric.title}>
                                <strong>{value == null ? "未记录" : curveNumber.format(value)}</strong><small>{index === 0 ? "基准" : value != null && base != null ? `差值 ${value > base ? "+" : ""}${curveNumber.format(value - base)}` : "差值未知"}</small>
                            </td>; })}</tr>;
                    })}</tbody>
                </table>
            </section>
            <section className="curve-selected-milestones" aria-label="所选回合关键节点">
                <h2>T{turn} 关键节点</h2>
                {milestones.length ? <ul>{milestones.map(({ run, event }) => <li key={`${run.key}:${event.id}`}>
                    <i style={{ background: run.color }} /><strong>{run.number}. {run.name}</strong><span>{categoryLabels[milestoneCategory(event.kind)]}</span><span>{event.detail}</span>
                </li>)}</ul> : <p>本回合没有可核实的关键节点记录。</p>}
                {series.some(run => run.data.milestones === undefined) && <p role="status">部分曲线接口尚未提供关键节点信息，请在运行结束后重启 Web 服务并刷新。</p>}
            </section>
            <p className="curve-note">节点只表示该回合发生的事件，不表示它是曲线变化的唯一原因。旧日志证据不足时不推测。这里展示全程已记录的结算曲线，不随观战历史位置回退。缺失回合断线，不外推。奖励记录有缺口时，后续累计值保持未记录。累计收入仅计讨伐奖励，不扣制作、招募或经验分配，不代表持有余额。</p>
        </>}
    </section>;
}
