import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertDialog, Button, DropdownMenu } from "@radix-ui/themes";
import { ArrowDown, ArrowUp, ArrowsDownUp, CaretDown, MagnifyingGlass } from "@phosphor-icons/react";
import { api } from "./api";
import { fmt, isActive } from "./model";
import { HISTORY_COLUMNS, historyCreatedAt, historyMetrics, historyPreset, historyRuns, historyStatus, sortHistoryRuns } from "./runCatalog";
import { milliseconds } from "./modelMetrics";
import { ResizableTable } from "./ResizableTable";
import type { HistoryRun, HistorySort, HistorySortField } from "./runCatalog";
import { Empty } from "./ui";
import type { Archive, HistoryDeletion, Job } from "./types";

const HISTORY_RESIZE_COLUMNS = [
    { id: "select", label: "选择", minWidth: 44 },
    { id: "name", label: HISTORY_COLUMNS.name.label, minWidth: 120 },
    { id: "preset", label: HISTORY_COLUMNS.preset.label, minWidth: 64 },
    { id: "status", label: HISTORY_COLUMNS.status.label, minWidth: 64 },
    { id: "turn", label: HISTORY_COLUMNS.turns.label, minWidth: 64 },
    { id: "score", label: HISTORY_COLUMNS.score.label, minWidth: 80 },
    { id: "duration", label: HISTORY_COLUMNS.duration.label, minWidth: 80 },
    { id: "tokens", label: HISTORY_COLUMNS.tokens.label, minWidth: 96 },
    { id: "created", label: HISTORY_COLUMNS.created_at.label, minWidth: 100 },
    { id: "actions", label: "操作", minWidth: 166 },
];

function failureSummary(error: string): string {
    if (/benchmark_version differs|original protocol/i.test(error)) return "协议版本不一致";
    if (/stream disconnected|stream closed before/i.test(error)) return "模型响应中断";
    if (/HTTP 408|timed?\s*out|timeout/i.test(error)) return "请求超时";
    return "运行失败，查看原始错误";
}

export function RunHistory({ jobs, onDeleted, onResume, onRerun, onOpen, onClone, onWatch, onCompare }: {
    jobs: Job[];
    onDeleted: (result: HistoryDeletion) => void;
    onResume: (archive: Archive) => void;
    onRerun?: (archive: Archive) => void;
    onOpen: (job: Job) => void;
    onClone: (job: Job) => void;
    onWatch: (jobs: Job[]) => void;
    onCompare: (ids: string[]) => void;
}) {
    const client = useQueryClient();
    const [deleteTargets, setDeleteTargets] = useState<HistoryRun[]>([]);
    const [deleting, setDeleting] = useState(false);
    const [deleteErrors, setDeleteErrors] = useState<{ row: HistoryRun; message: string }[]>([]);
    const [deleteProgress, setDeleteProgress] = useState(0);
    const [deletedCount, setDeletedCount] = useState(0);
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
            && (preset === "all" || historyPreset(row) === preset);
    }), sort);
    const selectedRows = rows.filter(row => checked.includes(row.id));
    const selected = selectedRows.flatMap(row => row.job ? [row.job] : []);
    const curveId = (row: typeof rows[number]) => row.job?.id || `archive:${row.archive!.run_id}`;
    function inUse(row: HistoryRun) {
        const runId = row.archive?.run_id || row.job?.archive_id;
        return jobs.some(job => isActive(job) && (job.id === row.job?.id || !!runId &&
            [job.archive_id, job.config.resume_run_id, job.config.rerun_run_id].includes(runId)));
    }
    const blocked = selectedRows.filter(inUse).length;
    const bulkDelete = deleteTargets.length + deletedCount > 1;
    const name = (row: HistoryRun) => row.job?.name || row.archive?.model || row.id;
    function requestDelete(targets: HistoryRun[]) {
        setDeleteTargets([...targets]);
        setDeleteErrors([]);
        setDeleteProgress(0);
        setDeletedCount(0);
    }
    async function deleteHistory() {
        const targets = deleteTargets;
        const failures: { row: HistoryRun; message: string }[] = [];
        let succeeded = 0;
        setDeleting(true);
        setDeleteErrors([]);
        setDeleteProgress(0);
        try {
            for (const [index, target] of targets.entries()) {
                const path = target.job
                    ? `/api/workbench/jobs/${encodeURIComponent(target.job.id)}`
                    : `/api/workbench/archives/${encodeURIComponent(target.archive!.run_id)}`;
                let result: HistoryDeletion;
                try {
                    result = await api<HistoryDeletion>(path, { method: "DELETE" });
                } catch (cause) {
                    failures.push({ row: target, message: (cause as Error).message });
                    setDeleteProgress(index + 1);
                    continue;
                }
                await Promise.all([client.cancelQueries({ queryKey: ["workbench"] }), client.cancelQueries({ queryKey: ["archives"] })]);
                onDeleted(result);
                setChecked(ids => ids.filter(id => id !== target.id));
                succeeded++;
                setDeleteProgress(index + 1);
            }
            setDeletedCount(count => count + succeeded);
            setDeleteErrors(failures);
            setDeleteTargets(failures.map(failure => failure.row));
        } finally {
            setDeleting(false);
            void client.invalidateQueries({ queryKey: ["workbench"] });
            void client.invalidateQueries({ queryKey: ["archives"] });
        }
    }
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
            <div className="runs-toolbar">
                <label className="run-search">
                    <MagnifyingGlass />
                    <input aria-label="搜索存档" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索模型、run 或 preset" />
                </label>
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
            <p className="history-count muted" role="status">显示 {rows.length} / {allRows.length} 个历史 run{deletedCount > 0 && `，已删除 ${deletedCount} 项`}</p>
            {selectedRows.length > 0 && <div className="selection-toolbar">
                <strong>已选择 {selectedRows.length} 个 run</strong>
                <Button highContrast size="1" variant="soft" onClick={() => onCompare(selectedRows.map(curveId))}>对比选中曲线{selectedRows.length > 4 ? "（前 4 个）" : ""}</Button>
                <Button highContrast size="1" variant="soft" disabled={!selected.length} title="实时观战仅支持工作台受控 run，CLI 存档可查看曲线" onClick={() => onWatch(selected.slice(0, 4))}>
                    观战选中{selected.length > 4 ? "（前 4 个）" : ""}
                </Button>
                <Button highContrast size="1" variant="soft" color="red" disabled={blocked > 0}
                    title={blocked ? "请先取消勾选被运行或续跑占用的存档" : "删除当前筛选中选中的存档和记录"}
                    onClick={() => requestDelete(selectedRows)}>删除选中</Button>
                {blocked > 0 && <span className="muted">{blocked} 项占用中</span>}
                <button className="text-button" onClick={() => setChecked([])}>清除选择</button>
            </div>}
            {query.isPending && <p className="muted">正在读取存档…</p>}
            {query.isError && <p className="callout error" role="alert">读取存档产物失败：{query.error.message}。工作台已知的历史仍可查看。</p>}
            {rows.length ? (
                <div className="archive-table-wrap">
                    <ResizableTable className="run-table archive-table" storageKey="history" columns={HISTORY_RESIZE_COLUMNS}>
                        <thead><tr>
                            <th className="checkbox-col"><input type="checkbox" aria-label="选择当前筛选的全部历史 run"
                                checked={rows.length > 0 && rows.every(row => checked.includes(row.id))}
                                onChange={event => setChecked(event.target.checked ? rows.map(row => row.id) : [])} /></th>
                            {sortHeader("name")}{sortHeader("preset")}{sortHeader("status")}{sortHeader("turns")}{sortHeader("score")}{sortHeader("duration")}{sortHeader("tokens")}{sortHeader("created_at")}<th>操作</th>
                        </tr></thead>
                        <tbody>{rows.map(row => {
                            const { job, archive } = row;
                            const status = job?.status || archive!.status;
                            const failure = job ? job.error : archive!.failure_reason;
                            const metrics = historyMetrics(row);
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
                                <td className="archive-duration mono" title="已记录的模型调用累计耗时，不含工具执行、评分和中断等待">{milliseconds(metrics.duration_ms)}</td>
                                <td className="archive-tokens mono" title="API 已报告用量。总量为输入与输出之和，不重复加计缓存或推理 Token。">
                                    <span>{metrics.total_tokens == null ? "未记录" : fmt(metrics.total_tokens)}</span>
                                    <small>输入 {metrics.input_tokens == null ? "未记录" : fmt(metrics.input_tokens)}</small>
                                    <small>输出 {metrics.output_tokens == null ? "未记录" : fmt(metrics.output_tokens)}</small>
                                </td>
                                <td className="archive-created mono">{created ? <time dateTime={row.created_at} title={row.created_at}>
                                    {created.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" })}
                                    <small>{created.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}</small>
                                </time> : "未记录"}</td>
                                <td><div className="archive-actions">
                                    {job && <Button highContrast size="1" variant="soft" onClick={() => onOpen(job)}>查看详情</Button>}
                                    {!job && archive && <Button highContrast size="1" variant="soft" onClick={() => onCompare([curveId(row)])}>查看曲线</Button>}
                                    <DropdownMenu.Root>
                                        <DropdownMenu.Trigger><Button highContrast size="1" variant="ghost" aria-label={`${job?.name || archive!.model || row.id} 的更多操作`}>更多<CaretDown size={12} /></Button></DropdownMenu.Trigger>
                                        <DropdownMenu.Content size="2" align="end">
                                            {job && <DropdownMenu.Item onSelect={() => onCompare([curveId(row)])}>查看曲线</DropdownMenu.Item>}
                                            {archive && onRerun && <DropdownMenu.Item disabled={!archive.preset} onSelect={() => onRerun(archive)}>从指定回合重跑</DropdownMenu.Item>}
                                            {archive && status !== "completed" && <DropdownMenu.Item disabled={!archive.preset} onSelect={() => onResume(archive)}>续跑</DropdownMenu.Item>}
                                            {!archive && job && <DropdownMenu.Item onSelect={() => onClone(job)}>重新创建</DropdownMenu.Item>}
                                            <DropdownMenu.Separator />
                                            <DropdownMenu.Item color="red" disabled={inUse(row)} onSelect={() => requestDelete([row])}>
                                                {archive || job?.archive_id ? "删除存档" : "删除记录"}
                                            </DropdownMenu.Item>
                                        </DropdownMenu.Content>
                                    </DropdownMenu.Root>
                                </div></td>
                            </tr>;
                        })}</tbody>
                    </ResizableTable>
                </div>
            ) : !query.isPending && <Empty title="没有匹配的历史 run">结束的 run 会出现在这里；正在排队和执行的 run 请到运行中心查看。</Empty>}
            <AlertDialog.Root open={deleteTargets.length > 0} onOpenChange={open => {
                if (!open && !deleting) { setDeleteTargets([]); setDeleteErrors([]); }
            }}>
                {deleteTargets.length > 0 && <AlertDialog.Content maxWidth={bulkDelete ? "520px" : "480px"} aria-busy={deleting}>
                    <AlertDialog.Title>{bulkDelete
                        ? `删除${deletedCount ? "剩余" : "选中的"} ${deleteTargets.length} 项？`
                        : deleteTargets[0].archive || deleteTargets[0].job?.archive_id ? "删除存档？" : "删除记录？"}</AlertDialog.Title>
                    <AlertDialog.Description>
                        永久删除{bulkDelete ? `这 ${deleteTargets.length} 项` : `“${name(deleteTargets[0])}”`}及关联执行日志，无法恢复。独立重跑分支不受影响。
                    </AlertDialog.Description>
                    {bulkDelete && !deleteErrors.length && <ul className="history-delete-list" aria-label="待删除项">
                        {deleteTargets.map(row => <li key={row.id}><strong>{name(row)}</strong>
                            <small className="mono">{row.archive?.run_id || row.job?.archive_id || row.job!.id}</small></li>)}
                    </ul>}
                    {deleteTargets.some(row => !row.job && row.archive?.status === "running") &&
                        <p className="callout">请先确认外部 CLI 进程已经停止。</p>}
                    {deleteTargets.some(inUse) && <p className="callout">部分存档正在被运行或续跑占用，不能删除。</p>}
                    {deleting && <p className="muted" role="status">已处理 {deleteProgress} / {deleteTargets.length} 项</p>}
                    {deleteErrors.length > 0 && <div className="callout error history-delete-errors" role="alert">
                        {bulkDelete && <p>已删除 {deletedCount} 项，{deleteErrors.length} 项请求失败。</p>}
                        {deleteErrors.map(failure => <p key={failure.row.id}>{bulkDelete && `${name(failure.row)}：`}{failure.message}</p>)}
                    </div>}
                    <div className="dialog-footer">
                        <AlertDialog.Cancel><Button highContrast variant="soft" color="gray" disabled={deleting}>取消</Button></AlertDialog.Cancel>
                        <Button highContrast color="red" loading={deleting} disabled={deleting || deleteTargets.some(inUse)} onClick={() => void deleteHistory()}>确认删除</Button>
                    </div>
                </AlertDialog.Content>}
            </AlertDialog.Root>
        </section>
    );
}
