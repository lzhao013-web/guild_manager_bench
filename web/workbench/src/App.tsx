import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertDialog, Button, Dialog, Theme } from "@radix-ui/themes";
import {
    SquaresFour,
    Rows,
    Stack,
    Archive as ArchiveBox,
    Sliders,
    Plus,
    MagnifyingGlass,
    ArrowSquareOut,
    Sun,
    Moon,
    Monitor,
    PushPin,
    ArrowLeft,
    Plugs,
    WarningCircle,
    ArrowsClockwise,
    Command,
    CaretRight,
    ChartLineUp,
} from "@phosphor-icons/react";
import { api } from "./api";
import {
    fmt,
    isActive,
    PHASE,
    short,
    STATUS,
} from "./model";
import type { Draft } from "./model";
import { defaultDraft, NewBatch } from "./NewBatch";
import { RunDetail } from "./RunDetail";
import { InspectorResizeHandle } from "./InspectorResizeHandle";
import { ResizableTable } from "./ResizableTable";
import { milliseconds } from "./modelMetrics";
import { PageBoundary } from "./PageBoundary";
import { buildRunCatalog, canonicalRunIds } from "./runCatalog";
import { livePosition } from "./watchModel";
import type { WatchPosition } from "./watchModel";
import type { WatchFocus } from "./sceneNavigation";
import { ApiProfilesPanel, readApiProfiles } from "./ApiProfiles";
import { Empty, StatusBadge, Trend } from "./ui";
import { ElapsedTime } from "./ElapsedTime";
import { ChangeValue } from "./motion";
import { RunResultNotice } from "./RunResultNotice";
import { RecentRuns } from "./RecentRuns";
import { RunId } from "./RunId";
import { RunPicker } from "./RunPicker";
import type { ApiProfile, Archive, HistoryDeletion, Job, Settings, Snapshot } from "./types";

type Section =
    "runs" | "archives" | "curves" | "apis" | "settings";
type View = "list" | "watch" | "focus";
const RUN_COLUMNS = [
    { id: "select", label: "选择", minWidth: 64 },
    { id: "name", label: "运行 / 模型", minWidth: 160 },
    { id: "status", label: "状态与阶段", minWidth: 140 },
    { id: "turn", label: "回合", minWidth: 64 },
    { id: "duration", label: "累计耗时", minWidth: 80 },
    { id: "score", label: "Rank Score", minWidth: 100 },
    { id: "tokens", label: "输出 Token", minWidth: 104 },
    { id: "activity", label: "最近活动", minWidth: 130 },
    { id: "pin", label: "关注", minWidth: 56 },
];
const WatchRun = lazy(() => import("./WatchRun").then(module => ({ default: module.WatchRun })));
const RunHistory = lazy(() => import("./RunHistory").then(module => ({ default: module.RunHistory })));
const CurveBoard = lazy(() => import("./CurveBoard").then(module => ({ default: module.CurveBoard })));
function useStored<T>(key: string, initial: T) {
    const [value, setValue] = useState<T>(() => {
        const raw = localStorage.getItem(key);
        if (!raw) return initial;
        try {
            return JSON.parse(raw) as T;
        } catch {
            return initial;
        }
    });
    useEffect(() => {
        localStorage.setItem(key, JSON.stringify(value));
    }, [key, value]);
    return [value, setValue] as const;
}

export default function App() {
    const queryClient = useQueryClient();
    const query = useQuery({
        queryKey: ["workbench"],
        queryFn: () => api<Snapshot>("/api/workbench"),
        refetchInterval: result => result.state.data?.jobs.some(isActive) ? 1000 : 5000,
    });
    const profilesQuery = useQuery({
        queryKey: ["api-profiles"],
        queryFn: readApiProfiles,
        enabled: !!query.data,
    });
    const profiles = profilesQuery.data?.profiles || [];
    const catalog = useMemo(() => buildRunCatalog(query.data?.jobs || []), [query.data?.jobs]);
    const jobs = catalog.runs;
    const runKey = (job: Job) => catalog.keys.get(job.id)!;
    const [section, setSection] = useState<Section>(() => {
        const requested = new URLSearchParams(location.search).get("section");
        return [
            "runs",
            "archives",
            "curves",
            "apis",
            "settings",
        ].includes(requested || "")
            ? (requested as Section)
            : "runs";
    });
    const [view, setView] = useState<View>(() => {
        const v = new URLSearchParams(location.search).get("view");
        return v === "focus" || v === "watch" ? v : "list";
    });
    const [watchPositions, setWatchPositions] = useState<Record<string, WatchPosition>>({});
    const [focusOrigin, setFocusOrigin] = useState<"list" | "watch">("list");
    const [focusTarget, setFocusTarget] = useState<WatchFocus>();
    const [curveIds, setCurveIds] = useStored<string[]>("gmb.curves.v1", []);
    const [curveOrigin, setCurveOrigin] = useState<{ section: Section; view: View } | null>(null);
    useEffect(() => {
        const params = new URLSearchParams(location.search);
        if (params.has("curves")) setCurveIds(params.getAll("curves").filter(Boolean).slice(0, 4));
    }, []);
    const [selectedId, setSelectedId] = useState<string | null>(
        new URLSearchParams(location.search).get("run"),
    );
    const [search, setSearch] = useStored("gmb.search.v1", "");
    const [filter, setFilter] = useStored("gmb.filter.v1", "all");
    const [storedPins, setPins] = useStored<string[]>("gmb.pins.v1", []);
    const [storedWatchIds, setWatchIds] = useStored<string[]>("gmb.watch.v1", []);
    const pins = canonicalRunIds(storedPins, catalog);
    const watchIds = canonicalRunIds(storedWatchIds, catalog);
    const [width, setWidth] = useStored("gmb.inspector-width.v1", 410);
    const [appearance, setAppearance] = useStored<"dark" | "light" | "system">(
        "gmb.theme.v1",
        "system",
    );
    const [systemDark, setSystemDark] = useState(
        matchMedia("(prefers-color-scheme: dark)").matches,
    );
    const dark = appearance === "system" ? systemDark : appearance === "dark";
    const [storedChecked, setChecked] = useState<string[]>([]);
    const checked = canonicalRunIds(storedChecked, catalog);
    const [newOpen, setNewOpen] = useState(false);
    const [draft, setDraft] = useState<Draft>();
    const [draftKey, setDraftKey] = useState(0);
    const [stopTargets, setStopTargets] = useState<Job[]>([]);
    const [stopping, setStopping] = useState(false);
    const [error, setError] = useState("");
    const [stopError, setStopError] = useState("");
    const selected = selectedId ? catalog.byId.get(selectedId) : undefined;
    const [notices, setNotices] = useState<{ key: string; name: string; status: Job["status"] }[]>([]);
    const observed = useRef<Map<string, { id: string; status: Job["status"] }> | null>(null);
    const noticePanel = useRef<HTMLDetailsElement>(null);
    useEffect(() => {
        if (!query.data) return;
        const previous = observed.current;
        const finished = jobs.filter(job => {
            if (!previous || isActive(job)) return false;
            const before = previous.get(runKey(job));
            // 启动失败或很快完成的续跑可能发生在两次摘要轮询之间。
            return !before || before.id !== job.id || ["queued", "starting", "running", "stopping"].includes(before.status);
        });
        const resumed = new Set(jobs.filter(job => isActive(job)).map(runKey));
        if (finished.length || notices.some(notice => resumed.has(notice.key))) {
            setNotices(items => [
                ...items.filter(item => !resumed.has(item.key) && !finished.some(job => runKey(job) === item.key)),
                ...finished.map(job => ({ key: runKey(job), name: job.name, status: job.status })),
            ]);
        }
        observed.current = new Map(jobs.map(job => [runKey(job), { id: job.id, status: job.status }]));
        if (finished.length) void queryClient.invalidateQueries({ queryKey: ["archives"] });
    }, [catalog, query.data, queryClient]);
    useEffect(() => {
        if (["completed", "failed", "interrupted"].includes(filter)) setFilter("all");
    }, [filter]);
    const active = jobs.filter(isActive),
        running = active.filter((job) => job.status !== "queued"),
        queued = active.filter((job) => job.status === "queued");
    const recentJobs = useMemo(() => jobs.filter(job => !isActive(job)).sort((a, b) =>
        (b.ended_at || b.updated_at).localeCompare(a.ended_at || a.updated_at) || b.id.localeCompare(a.id)).slice(0, 5), [jobs]);
    const attention = active.filter(job => job.checkpoint_error);
    const selectedJobs = jobs.filter((job) => checked.includes(runKey(job)));
    const visible = useMemo(
        () =>
            jobs
                .filter(isActive)
                .slice()
                .reverse()
                .filter((job) => {
                    const text =
                        `${job.name} ${job.config.model} ${job.config.preset} ${runKey(job)} ${job.id} ${job.archive_id || ""}`.toLowerCase();
                    return (
                        text.includes(search.toLowerCase()) &&
                        (filter === "all" ||
                            (filter === "active" && job.status !== "queued") ||
                            (filter === "attention" && !!job.checkpoint_error) ||
                            filter === job.status)
                    );
                })
                .sort(
                    (a, b) =>
                        Number(pins.includes(runKey(b))) -
                        Number(pins.includes(runKey(a))),
                ),
        [jobs, search, filter, pins],
    );
    const watching = watchIds
        .map((id) => catalog.byId.get(id))
        .filter((job): job is Job => !!job)
        .slice(0, 4);
    useEffect(() => {
        const media = matchMedia("(prefers-color-scheme: dark)");
        const handler = () => setSystemDark(media.matches);
        media.addEventListener("change", handler);
        return () => media.removeEventListener("change", handler);
    }, []);
    useEffect(() => {
        document.documentElement.dataset.theme = dark ? "dark" : "light";
    }, [dark]);
    useEffect(() => {
        document.title = `${running.length ? `(${running.length} 运行中) ` : ""}运行工作台 · Guild Manager Bench`;
    }, [running.length]);
    useEffect(() => {
        const url = new URL(location.href);
        if (selectedId) url.searchParams.set("run", selectedId);
        else url.searchParams.delete("run");
        if (view !== "list") url.searchParams.set("view", view);
        else url.searchParams.delete("view");
        if (section === "runs") url.searchParams.delete("section");
        else url.searchParams.set("section", section);
        url.searchParams.delete("curves");
        if (section === "curves") for (const id of curveIds) url.searchParams.append("curves", id);
        history.replaceState(null, "", url);
    }, [selectedId, view, section, curveIds]);
    useEffect(() => {
        const handler = (event: KeyboardEvent) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "k") {
                if (
                    document.querySelector(
                        '[role="dialog"], [role="alertdialog"]',
                    )
                )
                    return;
                event.preventDefault();
                document
                    .querySelector<HTMLInputElement>(".run-search input")
                    ?.focus();
            }
        };
        addEventListener("keydown", handler);
        return () => removeEventListener("keydown", handler);
    }, []);

    function create(initial?: Draft) {
        setDraft(initial);
        setDraftKey((k) => k + 1);
        setNewOpen(true);
    }
    function select(job: Job, focus = false) {
        setSelectedId(runKey(job));
        if (focus) {
            setFocusTarget(undefined);
            setFocusOrigin(view === "watch" ? "watch" : "list");
            setView("focus");
        }
    }
    function pin(id: string) {
        const key = catalog.keys.get(id)!;
        setPins((ids) => {
            const keys = canonicalRunIds(ids, catalog);
            return keys.includes(key) ? keys.filter(x => x !== key) : [...keys, key];
        });
    }
    function openCurves(ids: string[]) {
        setCurveOrigin({ section, view });
        setCurveIds([...new Set(ids.map(id => catalog.keys.get(id) || id))].slice(0, 4));
        setSection("curves");
    }
    function openHistory(job?: Job) {
        setFocusTarget(undefined);
        noticePanel.current?.removeAttribute("open");
        setSection("archives");
        setFocusOrigin("list");
        setView(job ? "focus" : "list");
        if (job) setSelectedId(runKey(job));
    }
    function deletedHistory(result: HistoryDeletion) {
        const removed = new Set(result.deleted_job_ids.flatMap(id => [id, catalog.keys.get(id) || id]));
        if (result.run_id) removed.add(`archive:${result.run_id}`);
        setPins(ids => ids.filter(id => !removed.has(id)));
        setWatchIds(ids => ids.filter(id => !removed.has(id)));
        setChecked(ids => ids.filter(id => !removed.has(id)));
        setCurveIds(ids => ids.filter(id => !removed.has(id)));
        setNotices(items => items.filter(item => !removed.has(item.key)));
        setWatchPositions(previous => Object.fromEntries(Object.entries(previous).filter(([id]) => !removed.has(id))));
        if (selectedId && removed.has(selectedId)) {
            setSelectedId(null);
            setView("list");
        }
        queryClient.setQueryData<Snapshot>(["workbench"], current => current && ({
            ...current, jobs: current.jobs.filter(job => !removed.has(job.id)),
        }));
        queryClient.setQueryData<{ runs: Archive[] }>(["archives"], current => current && ({
            runs: current.runs.filter(archive => archive.run_id !== result.run_id),
        }));
        queryClient.removeQueries({ queryKey: ["run-curves"], predicate: query => {
            const params = new URL(query.queryKey[1] as string, location.origin).searchParams;
            return result.run_id !== null && params.get("run_id") === result.run_id || removed.has(params.get("job_id") || "");
        } });
    }
    function clone(job: Job) {
        const config = job.config;
        create({
            ...defaultDraft(config.preset),
            name: `${job.name}（复制）`,
            rows: [
                {
                    profile_id: config.profile_id || null,
                    provider: config.provider,
                    model: config.model || "",
                    base_url: config.base_url || "",
                    thinking: config.thinking,
                    api_key: "",
                },
            ],
            efforts: config.reasoning_effort || "",
            seeds: config.game_seed == null ? "" : String(config.game_seed),
            scoring_seed:
                config.scoring_seed == null ? "" : String(config.scoring_seed),
            max_tool_calls_per_turn: config.max_tool_calls_per_turn,
            timeout: config.timeout,
            temperature:
                config.temperature == null ? "" : String(config.temperature),
            max_tokens:
                config.max_tokens == null ? "" : String(config.max_tokens),
            objective: config.objective,
        });
    }
    function requestStop(targets: Job[]) {
        setStopTargets(targets.filter(isActive));
        setStopError("");
    }
    async function stop() {
        setStopping(true);
        setStopError("");
        try {
            await Promise.all(
                stopTargets.map((job) =>
                    api(`/api/workbench/jobs/${job.id}/stop`, {
                        method: "POST",
                    }),
                ),
            );
            setStopTargets([]);
            void queryClient.invalidateQueries({ queryKey: ["workbench"] });
        } catch (cause) {
            setStopError((cause as Error).message);
        } finally {
            setStopping(false);
        }
    }
    async function resume(archive: Archive, rerun = false) {
        setError("");
        try {
            const replay = await api<{
                agent: {
                    type: string;
                    config: {
                        model?: string;
                        base_url?: string;
                        reasoning_effort?: string;
                        effort?: string;
                        thinking?: boolean;
                        timeout?: number;
                        temperature?: number;
                        max_tokens?: number;
                        max_output_tokens?: number;
                    };
                };
                config: Record<string, unknown>;
                max_rerun_turn: number;
            }>(`/api/workbench/recoveries/${encodeURIComponent(archive.run_id)}`);
            create({
                ...defaultDraft(archive.preset),
                name: `${rerun ? "分支重跑" : "续跑"} ${archive.model || archive.run_id}`,
                resume_run_id: rerun ? undefined : archive.run_id,
                rerun_run_id: rerun ? archive.run_id : undefined,
                rerun_from_turn: rerun ? 1 : undefined,
                rerun_max_turn: replay.max_rerun_turn,
                objective: String(replay.config.objective ?? ""),
                max_tool_calls_per_turn: Number(replay.config.max_tool_calls_per_turn ?? 20),
                seeds: replay.config.game_seed == null ? "" : String(replay.config.game_seed),
                scoring_seed: replay.config.scoring_seed == null ? "" : String(replay.config.scoring_seed),
                efforts:
                    replay.agent?.config?.reasoning_effort ||
                    replay.agent?.config?.effort ||
                    "",
                timeout: replay.agent?.config?.timeout ?? 600,
                temperature:
                    replay.agent?.config?.temperature == null
                        ? ""
                        : String(replay.agent.config.temperature),
                max_tokens:
                    replay.agent?.config?.max_tokens == null &&
                    replay.agent?.config?.max_output_tokens == null
                        ? ""
                        : String(
                              replay.agent.config.max_tokens ??
                                  replay.agent.config.max_output_tokens,
                          ),
                rows: [
                    {
                        profile_id:
                            [...jobs]
                                .reverse()
                                .find(
                                    (job) =>
                                        job.archive_id === archive.run_id &&
                                        job.config.profile_id,
                                )?.config.profile_id || null,
                        provider:
                            (
                                {
                                    AnthropicMessagesAgent: "anthropic",
                                    GoogleGenerateContentAgent: "google",
                                    OpenAIResponsesAgent: "openai-responses",
                                } as Record<string, Job["config"]["provider"]>
                            )[replay.agent?.type] || "openai",
                        model: replay.agent?.config?.model || "",
                        base_url: replay.agent?.config?.base_url || "",
                        thinking: replay.agent?.config?.thinking ?? null,
                        api_key: "",
                    },
                ],
            });
        } catch (cause) {
            setError((cause as Error).message);
        }
    }
    const pageTitle = {
        runs: "运行中心",
        archives: "历史存档",
        curves: "曲线对比",
        apis: "API 配置",
        settings: "调度设置",
    }[section];
    const detailProps = (job: Job) => ({
        job,
        runId: runKey(job),
        onStop: (j: Job) => requestStop([j]),
        onClone: clone,
        onCurves: () => openCurves([runKey(job)]),
        onPin: () => pin(job.id),
        pinned: pins.includes(runKey(job)),
    });

    const watchProps = (job: Job) => ({
        ...detailProps(job),
        initialFocus: view === "focus" ? focusTarget : undefined,
        position: watchPositions[job.id] || livePosition,
        onPosition: (position: WatchPosition) => setWatchPositions(previous => ({ ...previous, [job.id]: position })),
    });

    return (
        <Theme
            appearance={dark ? "dark" : "light"}
            accentColor="teal"
            grayColor="slate"
            radius="medium"
            scaling="100%"
        >
            <div className="workbench-shell">
                <aside className="sidebar">
                    <a
                        className="brand"
                        href="/"
                        aria-label="Guild Manager Bench Workspace 工作台"
                    >
                        <span className="brand-mark">
                            <Command weight="bold" size={23} />
                        </span>
                        <div>
                            <strong>GUILD MANAGER</strong>
                            <span>BENCH WORKSPACE</span>
                        </div>
                    </a>
                    <div className="sidebar-label">工作空间</div>
                    <nav aria-label="主导航">
                        {(
                            [
                                ["runs", SquaresFour, "运行中心"],
                                ["archives", ArchiveBox, "历史存档"],
                                ["curves", ChartLineUp, "曲线对比"],
                                ["apis", Plugs, "API 配置"],
                            ] as const
                        ).map(([key, Icon, label]) => (
                            <button
                                key={key}
                                className={
                                    section === key
                                        ? "nav-item active"
                                        : "nav-item"
                                }
                                onClick={() => { setSection(key); if (key === "archives") setView("list"); }}
                            >
                                <Icon size={18} />
                                {label}
                                {key === "runs" && active.length > 0 && (
                                    <span>{active.length}</span>
                                )}
                            </button>
                        ))}
                    </nav>
                    <div className="sidebar-bottom">
                        <a href="/leaderboard/">
                            <Stack size={17} />
                            排行榜
                            <ArrowSquareOut size={14} />
                        </a>
                        <button
                            className={
                                section === "settings"
                                    ? "nav-item active"
                                    : "nav-item"
                            }
                            onClick={() => setSection("settings")}
                        >
                            <Sliders size={17} />
                            调度设置
                        </button>
                        <div className="theme-switch" aria-label="外观">
                            {(
                                [
                                    ["light", Sun, "浅色"],
                                    ["dark", Moon, "深色"],
                                    ["system", Monitor, "跟随系统"],
                                ] as const
                            ).map(([key, Icon, label]) => (
                                <button
                                    key={key}
                                    aria-label={label}
                                    title={label}
                                    aria-pressed={appearance === key}
                                    className={
                                        appearance === key ? "active" : ""
                                    }
                                    onClick={() => setAppearance(key)}
                                >
                                    <Icon size={16} />
                                </button>
                            ))}
                        </div>
                    </div>
                </aside>
                <main className={`workspace-main ${section === "curves" ? "is-curve-mode" : ""} ${(section === "runs" || section === "archives") ? view === "focus" ? "is-focus-mode" : view === "watch" ? "is-watch-mode" : "" : ""}`}>
                    <header className="workspace-header">
                        <div className="breadcrumbs">
                            <span>工作空间</span>
                            <CaretRight size={12} />
                            <strong>{pageTitle}</strong>
                        </div>
                        <div className="header-actions">
                            {notices.length > 0 && <details ref={noticePanel} className="run-result-notices" aria-label="运行结果通知"
                                onKeyDown={event => { if (event.key === "Escape") event.currentTarget.open = false; }}>
                                <summary className={notices.some(notice => notice.status === "failed") ? "has-failures" : ""}>
                                    <span role={notices.some(notice => notice.status === "failed") ? "alert" : "status"}>
                                        {notices.filter(notice => notice.status === "failed").length > 0 ? `${notices.filter(notice => notice.status === "failed").length} 个失败 · ` : ""}
                                        {notices.length} 个结果
                                    </span>
                                </summary>
                                <div className="run-result-menu">
                                    {notices.map(notice => <RunResultNotice key={notice.key} name={notice.name} status={notice.status}
                                        onOpen={() => openHistory(catalog.byId.get(notice.key))}
                                        onDismiss={() => setNotices(items => items.filter(item => item.key !== notice.key))} />)}
                                </div>
                            </details>}
                            <button
                                className="mobile-theme"
                                aria-label="切换明暗主题"
                                onClick={() =>
                                    setAppearance(dark ? "light" : "dark")
                                }
                            >
                                {dark ? <Sun size={17} /> : <Moon size={17} />}
                            </button>
                            <span
                                className={`connection ${query.isError ? "offline" : ""}`}
                                role="status"
                            >
                                {query.isError
                                    ? "离线，正在重连"
                                    : query.isPending
                                      ? "连接服务中"
                                      : "已连接本机服务"}
                            </span>
                            <Button
                                highContrast
                                size="2"
                                onClick={() => create()}
                                disabled={!query.data}
                            >
                                <Plus size={16} />
                                新建实验
                            </Button>
                        </div>
                    </header>
                    {(error || query.data?.scheduler_error) && (
                        <div className="global-error" role="alert">
                            <WarningCircle />
                            {error || query.data?.scheduler_error}
                            <button
                                onClick={() => {
                                    setError("");
                                    void query.refetch();
                                }}
                            >
                                重新读取
                            </button>
                        </div>
                    )}
                    {query.isError && (
                        <div className="global-error" role="alert">
                            无法同步运行状态。保留上次成功读取的数据，任务状态待确认。
                            <button onClick={() => void query.refetch()}>
                                重试连接
                            </button>
                        </div>
                    )}
                    <div className="workspace-heading">
                        <div className="workspace-title-group">
                            <h1>{pageTitle}</h1>
                            {section === "runs" && view === "list" && query.data && active.length > 0 && (
                                <div className="summary-strip" role="group" aria-label="运行统计">
                                    <button className={filter === "active" ? "active" : ""} aria-pressed={filter === "active"}
                                        onClick={() => setFilter(filter === "active" ? "all" : "active")}>
                                        <span>执行中</span>{" "}<ChangeValue value={running.length} className="summary-value">{running.length}</ChangeValue>
                                    </button>
                                    <button className={filter === "queued" ? "active" : ""} aria-pressed={filter === "queued"}
                                        onClick={() => setFilter(filter === "queued" ? "all" : "queued")}>
                                        <span>等待队列</span>{" "}<ChangeValue value={queued.length} className="summary-value">{queued.length}</ChangeValue>
                                    </button>
                                    <button className={filter === "attention" ? "active" : ""} aria-pressed={filter === "attention"}
                                        onClick={() => setFilter(filter === "attention" ? "all" : "attention")}>
                                        <span>存档异常</span>{" "}<ChangeValue value={attention.length} className={`summary-value ${attention.length ? "error-text" : ""}`}>{attention.length}</ChangeValue>
                                    </button>
                                    <div className="scheduler-meter">
                                        <span>并发</span><strong className="mono"><ChangeValue value={running.length}>{running.length}</ChangeValue> / {query.data.settings.max_parallel}</strong>
                                        <button onClick={() => setSection("settings")} aria-label="调整并发设置"><Sliders size={15} /></button>
                                    </div>
                                </div>
                            )}
                        </div>
                        {(section === "runs" || section === "archives") && (
                            <div className="view-switch" aria-label="工作视图">
                                <button
                                    className={view === "list" ? "active" : ""}
                                    onClick={() => setView("list")}
                                >
                                    <Rows />
                                    {section === "archives" ? "历史列表" : "总览"}
                                </button>
                                <button
                                    className={view === "watch" ? "active" : ""}
                                    onClick={() => {
                                        if (!watchIds.length)
                                            setWatchIds(
                                                (selectedJobs.length
                                                    ? selectedJobs
                                                    : section === "archives" ? jobs.filter(job => !isActive(job)) : visible
                                                )
                                                    .slice(0, 4)
                                                    .map(runKey),
                                            );
                                        setView("watch");
                                    }}
                                >
                                    <SquaresFour />
                                    多路观战
                                </button>
                            </div>
                        )}
                    </div>
                    {query.isPending ? (
                        <div
                            className="loading-workspace"
                            aria-label="正在加载工作台"
                        >
                            <div />
                            <div />
                            <div />
                            <div />
                        </div>
                    ) : (
                        query.data && (
                            <>
                                {(section === "runs" || (section === "archives" && view !== "list")) && (
                                    <>
                                        {view === "focus" && selected ? (
                                            <div className="focus-workspace">
                                                <button
                                                    className="back-button"
                                                    onClick={() =>
                                                        { setView(focusOrigin); if (section === "runs" && !isActive(selected)) setSelectedId(null); }
                                                    }
                                                >
                                                    <ArrowLeft />
                                                    {focusOrigin === "watch" ? "返回多路观战" : section === "archives" ? "返回历史存档" : "返回运行列表"}
                                                </button>
                                                <PageBoundary key={selected.id} name="观战界面">
                                                <Suspense fallback={<div className="skeleton-lines" aria-label="正在加载观战界面"><i /><i /><i /></div>}>
                                                <WatchRun
                                                    key={selected.id}
                                                    {...watchProps(selected)}
                                                />
                                                </Suspense>
                                                </PageBoundary>
                                            </div>
                                        ) : view === "watch" ? (
                                            <div className="watch-workspace">
                                                <div className="watch-toolbar">
                                                    <span>
                                                        每路独立回看，点击人物、怪物或摘要查看完整模块。
                                                    </span>
                                                    <button className="text-button" disabled={!watching.length} onClick={() => openCurves(watching.map(runKey))}><ChartLineUp size={15} />对比当前观战曲线</button>
                                                    <RunPicker label="添加观战 run" disabled={watching.length >= 4}
                                                        options={(section === "runs" ? active : jobs.filter(job => !isActive(job)))
                                                            .filter(job => !watchIds.includes(runKey(job)))
                                                            .map(job => ({ key: runKey(job), name: job.name, preset: job.config.preset,
                                                                model: job.config.model || "", status: job.status,
                                                                aliases: [job.id, job.archive_id || ""] }))}
                                                        onSelect={id => setWatchIds([...watchIds, id].slice(0, 4))} />
                                                </div>
                                                {watching.length ? (
                                                    <div
                                                        className={`watch-grid watch-count-${watching.length}`}
                                                    >
                                                        {watching.map((job) => (
                                                            <PageBoundary key={job.id} name={`${job.name}的观战面板`}>
                                                            <Suspense fallback={<div className="skeleton-lines" aria-label="正在加载观战面板"><i /><i /><i /></div>}>
                                                            <WatchRun
                                                                key={job.id}
                                                                {...watchProps(
                                                                    job,
                                                                )}
                                                                compact
                                                                onClose={() =>
                                                                    setWatchIds(
                                                                        (ids) =>
                                                                            canonicalRunIds(ids, catalog).filter(id => id !== runKey(job)),
                                                                    )
                                                                }
                                                                onFocus={(target) => {
                                                                    setFocusTarget(target);
                                                                    setSelectedId(runKey(job));
                                                                    setFocusOrigin("watch");
                                                                    setView(
                                                                        "focus",
                                                                    );
                                                                }}
                                                            />
                                                            </Suspense>
                                                            </PageBoundary>
                                                        ))}
                                                    </div>
                                                ) : (
                                                    <Empty title="选择你要观战的 run">
                                                        从上方添加任务，或在总览中多选后进入观战。
                                                    </Empty>
                                                )}
                                            </div>
                                        ) : (
                                            <>
                                                {active.length > 0 && <div className="runs-toolbar">
                                                    <label className="run-search">
                                                        <MagnifyingGlass
                                                            size={17}
                                                        />
                                                        <input
                                                            aria-label="搜索运行"
                                                            placeholder="搜索名称、模型、preset 或 ID"
                                                            value={search}
                                                            onChange={(e) =>
                                                                setSearch(
                                                                    e.target
                                                                        .value,
                                                                )
                                                            }
                                                        />
                                                        <kbd>Ctrl K</kbd>
                                                    </label>
                                                    <select
                                                        aria-label="筛选运行状态"
                                                        value={filter}
                                                        onChange={(e) =>
                                                            setFilter(
                                                                e.target.value,
                                                            )
                                                        }
                                                    >
                                                        <option value="all">
                                                            所有状态
                                                        </option>
                                                        <option value="active">
                                                            执行中
                                                        </option>
                                                        <option value="attention">
                                                            需要关注
                                                        </option>
                                                        {Object.entries(
                                                            STATUS,
                                                        ).filter(([key]) => ["queued", "starting", "running", "stopping"].includes(key)).map(
                                                            ([key, value]) => (
                                                                <option
                                                                    key={key}
                                                                    value={key}
                                                                >
                                                                    {value}
                                                                </option>
                                                            ),
                                                        )}
                                                    </select>
                                                    <button
                                                        className="refresh-button"
                                                        title="立即刷新"
                                                        aria-label="立即刷新"
                                                        onClick={() =>
                                                            void query.refetch()
                                                        }
                                                    >
                                                        <ArrowsClockwise
                                                            size={18}
                                                        />
                                                    </button>
                                                </div>}
                                                {checked.length > 0 && active.length > 0 && (
                                                    <div className="selection-toolbar">
                                                        <strong>
                                                            已选择{" "}
                                                            {checked.length} 个
                                                            run
                                                        </strong>
                                                        <Button
                                                            highContrast
                                                            size="1"
                                                            variant="soft"
                                                            onClick={() => {
                                                                setWatchIds(
                                                                    selectedJobs.slice(0, 4).map(runKey),
                                                                );
                                                                setView(
                                                                    "watch",
                                                                );
                                                            }}
                                                        >
                                                            观战选中
                                                            {checked.length > 4
                                                                ? "（前 4 个）"
                                                                : ""}
                                                        </Button>
                                                        <Button highContrast size="1" variant="soft" onClick={() => openCurves(selectedJobs.map(runKey))}>对比选中曲线{selectedJobs.length > 4 ? "（前 4 个）" : ""}</Button>
                                                        <Button
                                                            highContrast
                                                            size="1"
                                                            variant="soft"
                                                            color="red"
                                                            disabled={
                                                                !selectedJobs.some(
                                                                    isActive,
                                                                )
                                                            }
                                                            onClick={() =>
                                                                requestStop(
                                                                    selectedJobs,
                                                                )
                                                            }
                                                        >
                                                            停止选中
                                                        </Button>
                                                        <button
                                                            className="text-button"
                                                            onClick={() =>
                                                                setChecked([])
                                                            }
                                                        >
                                                            清除选择
                                                        </button>
                                                    </div>
                                                )}
                                                <div
                                                    className={`runs-layout ${selected ? "has-inspector" : ""}`}
                                                    style={
                                                        {
                                                            "--inspector-width": `${width}px`,
                                                        } as CSSProperties
                                                    }
                                                >
                                                    <section
                                                        className="run-table-wrap"
                                                        aria-label="运行列表"
                                                    >
                                                        {visible.length ? (
                                                            <ResizableTable className="run-table" storageKey="runs" columns={RUN_COLUMNS}>
                                                                <thead>
                                                                    <tr>
                                                                        <th className="checkbox-col">
                                                                            <input
                                                                                aria-label="选择当前筛选的全部运行"
                                                                                type="checkbox"
                                                                                checked={
                                                                                    visible.length >
                                                                                        0 &&
                                                                                    visible.every(
                                                                                        (
                                                                                            j,
                                                                                        ) =>
                                                                                            checked.includes(
                                                                                                runKey(j),
                                                                                            ),
                                                                                    )
                                                                                }
                                                                                onChange={(
                                                                                    e,
                                                                                ) =>
                                                                                    setChecked(
                                                                                        e
                                                                                            .target
                                                                                            .checked
                                                                                            ? [
                                                                                                  ...new Set(
                                                                                                      [
                                                                                                          ...checked,
                                                                                                          ...visible.map(
                                                                                                              (
                                                                                                                  j,
                                                                                                              ) =>
                                                                                                                  runKey(j),
                                                                                                          ),
                                                                                                      ],
                                                                                                  ),
                                                                                              ]
                                                                                            : checked.filter(
                                                                                                  (
                                                                                                      id,
                                                                                                  ) =>
                                                                                                      !visible.some(
                                                                                                          (
                                                                                                              j,
                                                                                                          ) =>
                                                                                                              runKey(j) ===
                                                                                                              id,
                                                                                                      ),
                                                                                              ),
                                                                                    )
                                                                                }
                                                                            />
                                                                        </th>
                                                                        <th>
                                                                            运行
                                                                            /
                                                                            模型
                                                                        </th>
                                                                        <th>
                                                                            状态与阶段
                                                                        </th>
                                                                        <th>
                                                                            回合
                                                                        </th>
                                                                        <th title="模型调用累计耗时，不含排队、工具执行、评分和中断等待">累计耗时</th>
                                                                        <th>
                                                                            Rank
                                                                            Score
                                                                        </th>
                                                                        <th>
                                                                            输出
                                                                            Token
                                                                        </th>
                                                                        <th>
                                                                            最近活动
                                                                        </th>
                                                                        <th className="pin-col">
                                                                            关注
                                                                        </th>
                                                                    </tr>
                                                                </thead>
                                                                <tbody>
                                                                    {visible.map(
                                                                        (
                                                                            job,
                                                                        ) => (
                                                                            <tr
                                                                                key={
                                                                                    job.id
                                                                                }
                                                                                className={
                                                                                    selected?.id ===
                                                                                    job.id
                                                                                        ? "selected"
                                                                                        : ""
                                                                                }
                                                                                onClick={() =>
                                                                                    select(
                                                                                        job,
                                                                                    )
                                                                                }
                                                                            >
                                                                                <td
                                                                                    onClick={(
                                                                                        e,
                                                                                    ) =>
                                                                                        e.stopPropagation()
                                                                                    }
                                                                                >
                                                                                    <input
                                                                                        type="checkbox"
                                                                                        aria-label={`选择 ${job.name}`}
                                                                                        checked={checked.includes(
                                                                                            runKey(job),
                                                                                        )}
                                                                                        onChange={(
                                                                                            e,
                                                                                        ) =>
                                                                                            setChecked(
                                                                                                (
                                                                                                    ids,
                                                                                                ) =>
                                                                                                    e
                                                                                                        .target
                                                                                                        .checked
                                                                                                        ? [
                                                                                                              ...ids,
                                                                                                              runKey(job),
                                                                                                          ]
                                                                                                        : ids.filter(
                                                                                                              (
                                                                                                                  id,
                                                                                                              ) =>
                                                                                                                  id !==
                                                                                                                  runKey(job),
                                                                                                          ),
                                                                                            )
                                                                                        }
                                                                                    />
                                                                                </td>
                                                                                <td>
                                                                                    <button
                                                                                        className="run-name"
                                                                                        onClick={() =>
                                                                                            select(
                                                                                                job,
                                                                                            )
                                                                                        }
                                                                                    >
                                                                                        {
                                                                                            job.name
                                                                                        }
                                                                                    </button>
                                                                                    <RunId id={runKey(job)} />
                                                                                    <div className="run-subtitle">
                                                                                        {
                                                                                            job
                                                                                                .config
                                                                                                .preset
                                                                                        }
                                                                                        <span>
                                                                                            {job
                                                                                                .config
                                                                                                .reasoning_effort ||
                                                                                                "默认推理"}
                                                                                        </span>
                                                                                    </div>
                                                                                </td>
                                                                                <td>
                                                                                    <StatusBadge
                                                                                        status={job.status}
                                                                                        turn={job.turn}
                                                                                        maxTurns={job.max_turns}
                                                                                    />
                                                                                    <div className="phase-text">
                                                                                        {PHASE[
                                                                                            job
                                                                                                .phase
                                                                                        ] ||
                                                                                            job.phase}
                                                                                        {isActive(
                                                                                            job,
                                                                                        ) &&
                                                                                            job.status !==
                                                                                                "queued" && (
                                                                                                <span>
                                                                                                    <ElapsedTime start={job.phase_started_at} live />
                                                                                                </span>
                                                                                            )}
                                                                                    </div>
                                                                                    {job.checkpoint_error && (
                                                                                        <span className="error-text">
                                                                                            存档异常
                                                                                        </span>
                                                                                    )}
                                                                                </td>
                                                                                <td className="mono turn-cell">
                                                                                    <ChangeValue value={job.turn ?? "pending"}>
                                                                                        {job.turn == null ? "待启动" : <>
                                                                                            {job.turn}
                                                                                            <span className="turn-max"> / {job.max_turns}</span>
                                                                                        </>}
                                                                                    </ChangeValue>
                                                                                </td>
                                                                                <td className="run-duration mono" title="已记录的模型调用累计耗时，不含排队、工具执行、评分和中断等待">
                                                                                    {milliseconds(job.model_duration_ms ?? null)}
                                                                                </td>
                                                                                <td>
                                                                                    <div className="score-value mono">
                                                                                        <ChangeValue value={job.last_score ?? "unknown"}>
                                                                                            {fmt(job.last_score)}
                                                                                        </ChangeValue>
                                                                                    </div>
                                                                                    <Trend
                                                                                        points={
                                                                                            job.scores
                                                                                        }
                                                                                    />
                                                                                </td>
                                                                                <td className="mono">
                                                                                    {short(
                                                                                        job
                                                                                            .usage
                                                                                            .output_tokens,
                                                                                    )}
                                                                                </td>
                                                                                <td className="activity-cell">
                                                                                    <span>
                                                                                        {new Date(
                                                                                            job.updated_at,
                                                                                        ).toLocaleTimeString(
                                                                                            "zh-CN",
                                                                                            {
                                                                                                hour12: false,
                                                                                            },
                                                                                        )}
                                                                                    </span>
                                                                                    <small>
                                                                                        {job.recent_actions.at(
                                                                                            -1,
                                                                                        )
                                                                                            ? TOOLS_LABEL(
                                                                                                  job.recent_actions.at(
                                                                                                      -1,
                                                                                                  )!
                                                                                                      .name,
                                                                                              )
                                                                                            : job.calls
                                                                                              ? "模型调用已发出"
                                                                                              : "已加入队列"}
                                                                                    </small>
                                                                                </td>
                                                                                <td
                                                                                    onClick={(
                                                                                        e,
                                                                                    ) =>
                                                                                        e.stopPropagation()
                                                                                    }
                                                                                >
                                                                                    <button
                                                                                        className={`pin-button ${pins.includes(runKey(job)) ? "pinned" : ""}`}
                                                                                        aria-label={`${pins.includes(runKey(job)) ? "取消关注" : "关注"} ${job.name}`}
                                                                                        aria-pressed={pins.includes(
                                                                                            runKey(job),
                                                                                        )}
                                                                                        onClick={() =>
                                                                                            pin(
                                                                                                runKey(job),
                                                                                            )
                                                                                        }
                                                                                    >
                                                                                        <PushPin
                                                                                            weight={
                                                                                                pins.includes(
                                                                                                    runKey(job),
                                                                                                )
                                                                                                    ? "fill"
                                                                                                    : "regular"
                                                                                            }
                                                                                        />
                                                                                    </button>
                                                                                </td>
                                                                            </tr>
                                                                        ),
                                                                    )}
                                                                </tbody>
                                                            </ResizableTable>
                                                        ) : (
                                                            <div className="run-list-empty">
                                                                {!active.length && <button className="refresh-button" aria-label="立即刷新" title="立即刷新运行状态" onClick={() => void query.refetch()}><ArrowsClockwise size={18} /></button>}
                                                                <h2>{active.length ? "没有匹配的运行" : jobs.length ? "当前没有进行中的任务" : "创建你的第一组实验"}</h2>
                                                                <p>{active.length ? "修改关键词或清除筛选，即可重新查看正在执行的任务。"
                                                                    : jobs.length ? "最近的运行结果保留在下方。继续实验可使用右上角的新建入口。"
                                                                    : "选择模型和运行参数后加入队列，运行状态与结果将在这里显示。"}</p>
                                                                {active.length > 0 ? <Button highContrast size="2" variant="soft" onClick={() => { setSearch(""); setFilter("all"); }}>清除筛选</Button>
                                                                    : !jobs.length && <Button highContrast size="2" onClick={() => create()}>配置第一组实验</Button>}
                                                            </div>
                                                        )}
                                                        {visible.length > 0 && <div className="table-footer"><span>{visible.length} 个运行</span></div>}
                                                        {active.length === 0 && recentJobs.length > 0 && <RecentRuns jobs={recentJobs} runKeys={catalog.keys}
                                                            onOpen={job => openHistory(job)} onHistory={() => openHistory()} />}
                                                    </section>
                                                    {selected && (
                                                        <aside className="inspector" id="run-inspector">
                                                            <InspectorResizeHandle width={width} onChange={setWidth} />
                                                            <RunDetail
                                                                key={
                                                                    selected.id
                                                                }
                                                                {...detailProps(
                                                                    selected,
                                                                )}
                                                                onClose={() =>
                                                                    setSelectedId(
                                                                        null,
                                                                    )
                                                                }
                                                                onFocus={(target) => {
                                                                    setFocusTarget(target);
                                                                    setFocusOrigin("list");
                                                                    setView("focus");
                                                                }}
                                                            />
                                                        </aside>
                                                    )}
                                                </div>
                                            </>
                                        )}
                                    </>
                                )}
                                {section === "curves" && <PageBoundary name="曲线对比"><Suspense fallback={<p className="muted">正在加载曲线工作区…</p>}>
                                    <CurveBoard catalog={catalog} selectedIds={curveIds} onSelection={setCurveIds}
                                        onBack={curveOrigin ? () => { setSection(curveOrigin.section); setView(curveOrigin.view); setCurveOrigin(null); } : undefined} />
                                </Suspense></PageBoundary>}
                                {section === "archives" && view === "list" && (
                                    <PageBoundary name="历史存档"><Suspense fallback={<p className="muted">正在加载历史…</p>}>
                                        <RunHistory jobs={jobs} runKeys={catalog.keys} onCompare={openCurves} onDeleted={deletedHistory} onResume={archive => void resume(archive)} onRerun={archive => void resume(archive, true)} onClone={clone} onOpen={job => openHistory(job)}
                                            onWatch={selected => { setWatchIds(selected.map(runKey)); setView("watch"); }} />
                                    </Suspense></PageBoundary>
                                )}
                                {section === "apis" && (
                                    <ApiProfilesPanel
                                        profiles={profiles}
                                        loading={profilesQuery.isPending}
                                        error={profilesQuery.error?.message}
                                        onChanged={() =>
                                            void queryClient.invalidateQueries({
                                                queryKey: ["api-profiles"],
                                            })
                                        }
                                        onUse={(profile) =>
                                            create({
                                                ...defaultDraft(
                                                    query.data.presets[0],
                                                ),
                                                rows: [
                                                    {
                                                        profile_id: profile.id,
                                                        provider:
                                                            profile.provider,
                                                        base_url:
                                                            profile.base_url,
                                                        model:
                                                            profile.default_model ||
                                                            "",
                                                        api_key: "",
                                                    },
                                                ],
                                            })
                                        }
                                    />
                                )}
                                {section === "settings" && (
                                    <SettingsPanel
                                        settings={query.data.settings}
                                        onSaved={() =>
                                            void queryClient.invalidateQueries({
                                                queryKey: ["workbench"],
                                            })
                                        }
                                    />
                                )}
                            </>
                        )
                    )}
                </main>
                <Dialog.Root open={newOpen} onOpenChange={setNewOpen}>
                    {newOpen && query.data && (
                        <NewBatch
                            key={draftKey}
                            presets={query.data.presets}
                            profiles={profiles}
                            profilesLoading={profilesQuery.isPending}
                            profilesError={profilesQuery.error?.message}
                            onProfileSaved={(profile) =>
                                queryClient.setQueryData<{
                                    profiles: ApiProfile[];
                                }>(["api-profiles"], (current) => ({
                                    profiles: [
                                        ...(current?.profiles || []).filter(
                                            (p) => p.id !== profile.id,
                                        ),
                                        profile,
                                    ],
                                }))
                            }
                            initial={draft}
                            onCreated={(created) => {
                                setNewOpen(false);
                                setSection("runs");
                                setView("list");
                                setFilter("all");
                                setSearch("");
                                setSelectedId(created[0].id);
                                void queryClient.invalidateQueries({
                                    queryKey: ["workbench"],
                                });
                            }}
                        />
                    )}
                </Dialog.Root>
                <AlertDialog.Root
                    open={stopTargets.length > 0}
                    onOpenChange={(open) => {
                        if (!open && !stopping) setStopTargets([]);
                    }}
                >
                    <AlertDialog.Content maxWidth="450px">
                        <AlertDialog.Title>
                            停止 {stopTargets.length} 个运行任务？
                        </AlertDialog.Title>
                        <AlertDialog.Description>
                            排队任务会取消。正在运行的任务会尝试保存进度后退出，超过 8 秒仍未退出则强制结束。续跑时，未完成的回合可能需要重新执行。
                        </AlertDialog.Description>
                        {stopError && (
                            <p className="callout error" role="alert">
                                {stopError}
                            </p>
                        )}
                        <div className="dialog-footer">
                            <AlertDialog.Cancel>
                                <Button
                                    highContrast
                                    variant="soft"
                                    color="gray"
                                    disabled={stopping}
                                >
                                    继续运行
                                </Button>
                            </AlertDialog.Cancel>
                            <Button
                                highContrast
                                color="red"
                                loading={stopping}
                                onClick={() => void stop()}
                            >
                                确认停止
                            </Button>
                        </div>
                    </AlertDialog.Content>
                </AlertDialog.Root>
            </div>
        </Theme>
    );
}

function TOOLS_LABEL(name: string) {
    return (
        (
            {
                end_turn: "回合已结算",
                recruit_adventurer: "招募冒险者",
                craft_equipment: "制作装备",
                allocate_experience: "分配经验",
                purchase_upgrade: "购买升级",
            } as Record<string, string>
        )[name] || name
    );
}

function SettingsPanel({
    settings,
    onSaved,
}: {
    settings: Settings;
    onSaved: () => void;
}) {
    const [draft, setDraft] = useState(settings);
    const [message, setMessage] = useState("");
    const [busy, setBusy] = useState(false);
    async function save(event: React.FormEvent) {
        event.preventDefault();
        setBusy(true);
        setMessage("");
        try {
            await api("/api/workbench/settings", {
                method: "PUT",
                body: JSON.stringify(draft),
            });
            setMessage("设置已保存，正在运行的任务不受影响。");
            onSaved();
        } catch (cause) {
            setMessage((cause as Error).message);
        } finally {
            setBusy(false);
        }
    }
    return (
        <form className="settings-form" onSubmit={save}>
            <h2>执行资源</h2>
            <div className="form-grid">
                <label>
                    全局并发上限
                    <input
                        type="number"
                        required
                        min={1}
                        max={8}
                        value={draft.max_parallel}
                        onChange={(e) =>
                            setDraft((d) => ({
                                ...d,
                                max_parallel: Number(e.target.value),
                            }))
                        }
                    />
                    <small>同时执行的 run 数量，取值 1 到 8。</small>
                </label>
                <label>
                    每个服务端点的并发上限
                    <input
                        type="number"
                        required
                        min={1}
                        max={8}
                        value={draft.per_endpoint}
                        onChange={(e) =>
                            setDraft((d) => ({
                                ...d,
                                per_endpoint: Number(e.target.value),
                            }))
                        }
                    />
                    <small>使用同一服务地址的任务共享这个上限。</small>
                </label>
            </div>
            <Button highContrast type="submit" loading={busy}>
                保存设置
            </Button>
            {message && (
                <p className="callout" role="status">
                    {message}
                </p>
            )}
            <details className="settings-notes">
                <summary>运行与恢复</summary>
                <p>
                    关闭页面不会停止任务。关闭工作台服务后，需要手动续跑。
                </p>
                <p>
                    工作台没有登录保护，请勿直接开放到公网。
                </p>
            </details>
            <details className="settings-notes">
                <summary>字体授权</summary>
                <p>本软件使用小米 MiSans 字体及 JetBrains Mono 字体。</p>
                <a href="/workbench/fonts/NOTICE.txt" target="_blank" rel="noreferrer">版权与许可证</a>
            </details>
        </form>
    );
}
