import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, DropdownMenu } from "@radix-ui/themes";
import { ArrowDown, ArrowSquareOut, ArrowUp, ArrowsDownUp, CaretDown, MagnifyingGlass } from "@phosphor-icons/react";
import { api } from "./api";
import { fmt } from "./model";
import { HISTORY_COLUMNS, historyCreatedAt, historyPreset, historyRuns, historyStatus, sortHistoryRuns } from "./runCatalog";
import type { HistorySort, HistorySortField } from "./runCatalog";
import { Empty } from "./ui";
import type { Archive, Job } from "./types";

function failureSummary(error: string): string {
    if (/benchmark_version differs|original protocol/i.test(error)) return "协议版本不一致";
    if (/stream disconnected|stream closed before/i.test(error)) return "模型响应中断";
    if (/HTTP 408|timed?\s*out|timeout/i.test(error)) return "请求超时";
    return "运行失败，查看原始错误";
}

export function RunHistory({ jobs, batchId, onBatchChange, onResume, onRerun, onOpen, onClone, onWatch, onCompare }: {
    jobs: Job[];
    batchId: string;
    onBatchChange: (id: string) => void;
    onResume: (archive: Archive) => void;
    onRerun?: (archive: Archive) => void;
    onOpen: (job: Job) => void;
    onClone: (job: Job) => void;
    onWatch: (jobs: Job[]) => void;
    onCompare: (ids: string[]) => void;
}) {
    const query = useQuery({
        queryKey: ["archives"],
        queryFn: () => api<{ runs: Archive[] }>("/api/llm/runs"),
        refetchInterval: 15000,
    });
    const [search, setSearch] = useState("");
    const [filter, setFilter] = useState("all");
    const [preset, setPreset] = useState("all");
    const [sort, setSort] = useState<HistorySort>({ field: "created_at", direction: "desc" });
    const [checked, setChecked] = useState<string[]>([]);
    const allRows = historyRuns(jobs, query.data?.runs || []);
    // 续跑会把 run 移出历史；保留正在使用的筛选项，不能让控件静默变回“全部”。
    const presets = [...new Set([...allRows.map(historyPreset), ...(preset === "all" ? [] : [preset])])]
        .sort((a, b) => a.localeCompare(b));
    const rows = sortHistoryRuns(allRows.filter(row => {
        const status = row.job?.status || row.archive!.status;
        const text = `${row.job?.name || ""} ${row.job?.config.model || row.archive?.model || ""} ${row.id} ${historyPreset(row)}`;
        return text.toLowerCase().includes(search.toLowerCase()) && (filter === "all" || status === filter)
            && (preset === "all" || historyPreset(row) === preset)
            && (batchId === "all" || row.job?.batch_id === batchId);
    }), sort);
    const selectedRows = rows.filter(row => checked.includes(row.id));
    const selected = selectedRows.flatMap(row => row.job ? [row.job] : []);
    const curveId = (row: typeof rows[number]) => row.job?.id || `archive:${row.archive!.run_id}`;
    function sortHeader(field: HistorySortField) {
        const column = HISTORY_COLUMNS[field];
        const active = sort.field === field;
        const next = active ? sort.direction === "asc" ? "desc" : "asc" : column.initial;
        const Icon = active ? sort.direction === "asc" ? ArrowUp : ArrowDown : ArrowsDownUp;
        return <th scope="col" className="history-sort-column" aria-sort={active ? sort.direction === "asc" ? "ascending" : "descending" : undefined}>
            <button type="button" title={`点击按${column.label}${next === "asc" ? "升序" : "降序"}排列`}
                onClick={() => setSort({ field, direction: next })}>
                {column.label}<Icon size={13} aria-hidden="true" />
            </button>
        </th>;
    }
    return (
        <section className="archives-section">
            <p className="callout">
                每个 run 只显示一个当前结果，续跑不会新增记录。未生成存档的失败或取消也保留在这里。
                CLI 存档只读接入，其中的“运行中”不代表进程仍存活。
            </p>
            <div className="runs-toolbar">
                <label className="run-search">
                    <MagnifyingGlass />
                    <input aria-label="搜索存档" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索模型、run 或 preset" />
                </label>
                <select aria-label="筛选历史批次" value={batchId} onChange={event => onBatchChange(event.target.value)}>
                    <option value="all">所有批次</option>
                    {[...new Map(jobs.map(job => [job.batch_id, job.batch_name]))].map(([id, name]) => <option key={id} value={id}>{name}</option>)}
                </select>
                <select aria-label="筛选历史 preset" value={preset} onChange={event => setPreset(event.target.value)}>
                    <option value="all">所有 preset</option>
                    {presets.map(value => <option key={value} value={value}>{value || "未记录 preset"}</option>)}
                </select>
                <select aria-label="筛选历史状态" value={filter} onChange={event => setFilter(event.target.value)}>
                    <option value="all">所有历史状态</option>
                    <option value="completed">已完成</option>
                    <option value="failed">失败</option>
                    <option value="interrupted">已中断</option>
                    <option value="running">CLI 存档标记为运行中</option>
                </select>
                <Button highContrast variant="soft" onClick={() => void query.refetch()}>刷新归档</Button>
            </div>
            <p className="history-count muted" role="status">显示 {rows.length} / {allRows.length} 个历史 run</p>
            {selectedRows.length > 0 && <div className="selection-toolbar">
                <strong>已选择 {selectedRows.length} 个 run</strong>
                <Button highContrast size="1" variant="soft" onClick={() => onCompare(selectedRows.map(curveId))}>对比选中曲线{selectedRows.length > 4 ? "（前 4 个）" : ""}</Button>
                <Button highContrast size="1" variant="soft" disabled={!selected.length} title="实时观战仅支持工作台受控 run，CLI 存档可比较曲线或回放" onClick={() => onWatch(selected.slice(0, 4))}>
                    观战选中{selected.length > 4 ? "（前 4 个）" : ""}
                </Button>
                <button className="text-button" onClick={() => setChecked([])}>清除选择</button>
            </div>}
            {query.isPending && <p className="muted">正在读取 CLI 与存档产物…</p>}
            {query.isError && <p className="callout error" role="alert">读取存档产物失败：{query.error.message}。工作台已知的历史仍可查看。</p>}
            {rows.length ? (
                <div className="archive-table-wrap">
                    <table className="run-table archive-table">
                        <thead><tr>
                            <th className="checkbox-col"><input type="checkbox" aria-label="选择当前筛选的全部历史 run"
                                checked={rows.length > 0 && rows.every(row => checked.includes(row.id))}
                                onChange={event => setChecked(event.target.checked ? rows.map(row => row.id) : [])} /></th>
                            {sortHeader("name")}{sortHeader("preset")}{sortHeader("status")}{sortHeader("turns")}{sortHeader("score")}{sortHeader("created_at")}<th>操作</th>
                        </tr></thead>
                        <tbody>{rows.map(row => {
                            const { job, archive } = row;
                            const status = job?.status || archive!.status;
                            const failure = job ? job.error : archive!.failure_reason;
                            const timestamp = historyCreatedAt(row);
                            const created = timestamp == null ? null : new Date(timestamp);
                            return <tr key={row.id}>
                                <td><input type="checkbox" aria-label={`选择历史 ${job?.name || archive!.model || row.id}`}
                                    checked={checked.includes(row.id)}
                                    onChange={event => setChecked(ids => event.target.checked ? [...ids, row.id] : ids.filter(id => id !== row.id))} /></td>
                                <td className="archive-name"><strong title={job?.name || archive!.model}>{job?.name || archive!.model || "未记录模型"}</strong>
                                    <small className="archive-id" title={job?.archive_id || archive?.run_id || undefined}>{job?.archive_id || archive?.run_id || "未生成存档"}</small>
                                    {job?.config.rerun_run_id && <small className="archive-id" title={job.config.rerun_run_id}>从 T{job.config.rerun_from_turn} 分支 · 来源 {job.config.rerun_run_id}</small>}
                                    {(job?.data.protocol_status === "mixed" || archive?.protocol_status === "mixed") && <small className="history-error">跨版本执行，不作同版本标准比较</small>}
                                    {failure && <details className="history-failure">
                                        <summary>{failureSummary(failure)}</summary>
                                        <pre>{failure}</pre>
                                    </details>}
                                </td>
                                <td>{historyPreset(row) || "未记录"}</td>
                                <td>{historyStatus(row) || "未记录"}</td>
                                <td className="mono turn-cell">{job ? (job.turn == null ? "未开始" : `${job.turn} / ${job.max_turns}`) : archive!.turns}</td>
                                <td className="mono">{fmt(job ? job.last_score : archive!.rank_score)}</td>
                                <td className="archive-created mono">{created ? <time dateTime={row.created_at} title={row.created_at}>
                                    {created.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" })}
                                    <small>{created.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}</small>
                                </time> : "未记录"}</td>
                                <td><div className="archive-actions">
                                    {job && <Button highContrast size="1" variant="soft" onClick={() => onOpen(job)}>查看详情</Button>}
                                    {!job && archive && <Button asChild highContrast size="1" variant="soft"><a href={`/replay/#run=${encodeURIComponent(archive.run_id)}`}>回放<ArrowSquareOut /></a></Button>}
                                    <DropdownMenu.Root>
                                        <DropdownMenu.Trigger><Button highContrast size="1" variant="ghost" aria-label={`${job?.name || archive!.model || row.id} 的更多操作`}>更多<CaretDown size={12} /></Button></DropdownMenu.Trigger>
                                        <DropdownMenu.Content size="2" align="end">
                                            <DropdownMenu.Item onSelect={() => onCompare([curveId(row)])}>查看曲线</DropdownMenu.Item>
                                            {job && archive && <DropdownMenu.Item asChild><a href={`/replay/#run=${encodeURIComponent(archive.run_id)}`}>回放<ArrowSquareOut /></a></DropdownMenu.Item>}
                                            {archive && onRerun && <DropdownMenu.Item disabled={!archive.preset} onSelect={() => onRerun(archive)}>从指定回合重跑</DropdownMenu.Item>}
                                            {archive && status !== "completed" && <DropdownMenu.Item disabled={!archive.preset} onSelect={() => onResume(archive)}>续跑</DropdownMenu.Item>}
                                            {!archive && job && <DropdownMenu.Item onSelect={() => onClone(job)}>重新创建</DropdownMenu.Item>}
                                        </DropdownMenu.Content>
                                    </DropdownMenu.Root>
                                </div></td>
                            </tr>;
                        })}</tbody>
                    </table>
                </div>
            ) : !query.isPending && <Empty title="没有匹配的历史 run">结束的 run 会出现在这里；正在排队和执行的 run 请到运行中心查看。</Empty>}
        </section>
    );
}
