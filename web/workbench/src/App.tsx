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
    Play,
    ArrowLeft,
    Flask,
    Plugs,
    WarningCircle,
    ArrowsClockwise,
    Command,
    CaretRight,
    ChartLineUp,
    X,
} from "@phosphor-icons/react";
import { api } from "./api";
import {
    duration,
    fmt,
    isActive,
    PHASE,
    safeTemplate,
    short,
    STATUS,
} from "./model";
import type { Draft } from "./model";
import { defaultDraft, NewBatch } from "./NewBatch";
import { RunDetail } from "./RunDetail";
import { PageBoundary } from "./PageBoundary";
import { buildRunCatalog, canonicalRunIds } from "./runCatalog";
import { livePosition } from "./watchModel";
import type { WatchPosition } from "./watchModel";
import type { WatchFocus } from "./sceneNavigation";
import { ApiProfilesPanel, readApiProfiles } from "./ApiProfiles";
import { Empty, StatusBadge, Trend } from "./ui";
import type { ApiProfile, Archive, Job, Settings, Snapshot } from "./types";

type Section =
    "runs" | "batches" | "archives" | "curves" | "apis" | "templates" | "settings";
type View = "list" | "watch" | "focus";
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
    const [now, setNow] = useState(Date.now());
    useEffect(() => {
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, []);
    const query = useQuery({
        queryKey: ["workbench"],
        queryFn: () => api<Snapshot>("/api/workbench"),
        refetchInterval: 1000,
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
            "batches",
            "archives",
            "curves",
            "apis",
            "templates",
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
    const [batchId, setBatchId] = useStored("gmb.batch.v1", "all");
    const [storedPins, setPins] = useStored<string[]>("gmb.pins.v1", []);
    const [storedWatchIds, setWatchIds] = useStored<string[]>("gmb.watch.v1", []);
    const pins = canonicalRunIds(storedPins, catalog);
    const watchIds = canonicalRunIds(storedWatchIds, catalog);
    const [templates, setTemplates] = useStored<Draft[]>(
        "gmb.templates.v1",
        [],
    );
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
    const attention = active.filter(job => job.checkpoint_error);
    const batches = useMemo(
        () =>
            Array.from(
                new Map(
                    jobs.map((job) => [job.batch_id, job.batch_name]),
                ).entries(),
            ).reverse(),
        [jobs],
    );
    const selectedJobs = jobs.filter((job) => checked.includes(runKey(job)));
    const visible = useMemo(
        () =>
            jobs
                .filter(isActive)
                .slice()
                .reverse()
                .filter((job) => {
                    const text =
                        `${job.name} ${job.config.model} ${job.batch_name} ${job.config.preset}`.toLowerCase();
                    return (
                        text.includes(search.toLowerCase()) &&
                        (batchId === "all" || job.batch_id === batchId) &&
                        (filter === "all" ||
                            (filter === "active" && isActive(job)) ||
                            (filter === "attention" && !!job.checkpoint_error) ||
                            filter === job.status)
                    );
                })
                .sort(
                    (a, b) =>
                        Number(pins.includes(runKey(b))) -
                        Number(pins.includes(runKey(a))),
                ),
        [jobs, search, batchId, filter, pins],
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
        else setBatchId("all");
    }
    function clone(job: Job) {
        const config = job.config;
        create({
            ...defaultDraft(config.preset),
            name: `${job.batch_name}（复制）`,
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
                timeout: replay.agent?.config?.timeout ?? 180,
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
        batches: "实验批次",
        archives: "历史存档",
        curves: "曲线对比",
        templates: "模型模板",
        apis: "API 配置",
        settings: "调度设置",
    }[section];
    const detailProps = (job: Job) => ({
        job,
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
                                ["batches", Stack, "实验批次"],
                                ["archives", ArchiveBox, "历史存档"],
                                ["curves", ChartLineUp, "曲线对比"],
                                ["apis", Plugs, "API 配置"],
                                ["templates", Flask, "模型模板"],
                            ] as const
                        ).map(([key, Icon, label]) => (
                            <button
                                key={key}
                                className={
                                    section === key
                                        ? "nav-item active"
                                        : "nav-item"
                                }
                                onClick={() => { setSection(key); if (key === "archives") { setView("list"); setBatchId("all"); } }}
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
                        <a href="/legacy/?mode=manual">
                            <Play size={17} />
                            人工操作
                            <ArrowSquareOut size={14} />
                        </a>
                        <a href="/replay/">
                            <ArchiveBox size={17} />
                            回放与复盘
                            <ArrowSquareOut size={14} />
                        </a>
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
                        <span className="sidebar-note">
                            任务由本机服务独立执行
                        </span>
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
                                    {notices.map(notice => <div key={notice.key} className={`run-result-notice ${notice.status === "failed" ? "is-failed" : ""}`}>
                                        <span><strong>{notice.name}</strong> {STATUS[notice.status]}，已移至历史存档。</span>
                                        <button className="text-button" onClick={() => openHistory(catalog.byId.get(notice.key))}>查看结果</button>
                                        <button className="notice-dismiss" aria-label={`关闭 ${notice.name} 的结果通知`} onClick={() => setNotices(items => items.filter(item => item.key !== notice.key))}><X size={16} /></button>
                                    </div>)}
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
                        <div>
                            <h1>{pageTitle}</h1>
                            <p>
                                {section === "runs"
                                    ? "这里只显示排队和执行中的 run，结束后移至历史存档。"
                                    : section === "batches"
                                      ? "把配置组合与重复运行组织成可追溯的实验。"
                                      : section === "archives"
                                        ? "读取已有运行，不修改存档或重新评分。"
                                        : section === "curves"
                                          ? "统一比较经济、经验与正式战力，按真实回合对齐。"
                                        : section === "apis"
                                          ? "复用 API 连接，安全保存凭证，直接获取模型目录。"
                                          : section === "templates"
                                            ? "复用模型与实验参数，凭证留在服务端。"
                                            : "限制并发，管理本机执行资源。"}
                            </p>
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
                                        {section === "runs" && <div className="summary-strip">
                                            <button
                                                className={
                                                    filter === "active"
                                                        ? "active"
                                                        : ""
                                                }
                                                onClick={() =>
                                                    setFilter(
                                                        filter === "active"
                                                            ? "all"
                                                            : "active",
                                                    )
                                                }
                                            >
                                                <span className="summary-value">
                                                    {running.length}
                                                </span>
                                                <span>执行中</span>
                                            </button>
                                            <button
                                                onClick={() =>
                                                    setFilter("queued")
                                                }
                                            >
                                                <span className="summary-value">
                                                    {queued.length}
                                                </span>
                                                <span>等待队列</span>
                                            </button>
                                            <button
                                                onClick={() =>
                                                    setFilter("attention")
                                                }
                                            >
                                                <span
                                                    className={`summary-value ${attention.length ? "error-text" : ""}`}
                                                >
                                                    {attention.length}
                                                </span>
                                                <span>存档异常</span>
                                            </button>
                                            <div className="scheduler-meter">
                                                <span>并发容量</span>
                                                <strong className="mono">
                                                    {running.length} /{" "}
                                                    {
                                                        query.data.settings
                                                            .max_parallel
                                                    }
                                                </strong>
                                                <button
                                                    onClick={() =>
                                                        setSection("settings")
                                                    }
                                                    aria-label="调整并发设置"
                                                >
                                                    <Sliders size={15} />
                                                </button>
                                            </div>
                                        </div>}
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
                                                    <select
                                                        aria-label="添加观战 run"
                                                        value=""
                                                        onChange={(e) => {
                                                            if (e.target.value)
                                                                setWatchIds(
                                                                    [
                                                                        ...watchIds.filter(
                                                                            (
                                                                                id,
                                                                            ) =>
                                                                                jobs.some(
                                                                                    (
                                                                                        j,
                                                                                    ) =>
                                                                                        runKey(j) ===
                                                                                        id,
                                                                                ),
                                                                        ),
                                                                        e.target
                                                                            .value,
                                                                    ].slice(
                                                                        0,
                                                                        4,
                                                                    ),
                                                                );
                                                        }}
                                                        disabled={
                                                            watching.length >= 4
                                                        }
                                                    >
                                                        <option value="">
                                                            添加一个 run…
                                                        </option>
                                                        {(section === "runs" ? active : jobs.filter(job => !isActive(job)))
                                                            .filter(
                                                                (j) =>
                                                                    !watchIds.includes(
                                                                        runKey(j),
                                                                    ),
                                                            )
                                                            .map((j) => (
                                                                <option
                                                                    key={j.id}
                                                                    value={runKey(j)}
                                                                >
                                                                    {j.name}
                                                                </option>
                                                            ))}
                                                    </select>
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
                                                <div className="runs-toolbar">
                                                    <label className="run-search">
                                                        <MagnifyingGlass
                                                            size={17}
                                                        />
                                                        <input
                                                            aria-label="搜索运行"
                                                            placeholder="搜索模型、批次或 preset"
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
                                                        aria-label="筛选批次"
                                                        value={batchId}
                                                        onChange={(e) =>
                                                            setBatchId(
                                                                e.target.value,
                                                            )
                                                        }
                                                    >
                                                        <option value="all">
                                                            所有批次
                                                        </option>
                                                        {batches.map(
                                                            ([id, name]) => (
                                                                <option
                                                                    key={id}
                                                                    value={id}
                                                                >
                                                                    {name}
                                                                </option>
                                                            ),
                                                        )}
                                                    </select>
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
                                                            进行中
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
                                                </div>
                                                {checked.length > 0 && (
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
                                                        {selectedJobs.length ===
                                                            2 &&
                                                            selectedJobs.every(
                                                                (j) =>
                                                                    j.archive_id,
                                                            ) && (
                                                                <a
                                                                    className="small-link"
                                                                    href={`/replay/compare.html?left=${encodeURIComponent(selectedJobs[0].archive_id!)}&right=${encodeURIComponent(selectedJobs[1].archive_id!)}`}
                                                                >
                                                                    对照两局
                                                                    <ArrowSquareOut />
                                                                </a>
                                                            )}
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
                                                            <table className="run-table">
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
                                                                                        <span>
                                                                                            {
                                                                                                job.batch_name
                                                                                            }
                                                                                        </span>
                                                                                    </div>
                                                                                </td>
                                                                                <td>
                                                                                    <StatusBadge
                                                                                        status={
                                                                                            job.status
                                                                                        }
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
                                                                                                    {duration(
                                                                                                        job.phase_started_at,
                                                                                                        null,
                                                                                                        now,
                                                                                                    )}
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
                                                                                    {job.turn ==
                                                                                    null ? (
                                                                                        "待启动"
                                                                                    ) : (
                                                                                        <>
                                                                                            {
                                                                                                job.turn
                                                                                            }
                                                                                            <span>
                                                                                                {" "}
                                                                                                /{" "}
                                                                                                {
                                                                                                    job.max_turns
                                                                                                }
                                                                                            </span>
                                                                                        </>
                                                                                    )}
                                                                                </td>
                                                                                <td>
                                                                                    <div className="score-value mono">
                                                                                        {fmt(
                                                                                            job.last_score,
                                                                                        )}
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
                                                            </table>
                                                        ) : (
                                                            <Empty
                                                                title={
                                                                    active.length ? "没有匹配的 run" : jobs.length ? "当前没有排队或执行中的 run" : "从第一组实验开始"
                                                                }
                                                                action={
                                                                    <Button
                                                                        highContrast
                                                                        onClick={() =>
                                                                            !active.length && jobs.length ? openHistory() : active.length
                                                                                ? (setSearch(
                                                                                      "",
                                                                                  ),
                                                                                  setBatchId(
                                                                                      "all",
                                                                                  ),
                                                                                  setFilter(
                                                                                      "all",
                                                                                  ))
                                                                                : create()
                                                                        }
                                                                    >
                                                                        {active.length ? "清除筛选" : jobs.length ? "查看历史存档" : "新建实验"}
                                                                    </Button>
                                                                }
                                                            >
                                                                {active.length ? "试试其他关键词、批次或状态。" : jobs.length ? "已结束的 run 保留在历史存档，续跑后会回到这里。" : "添加模型和实验参数。任务在服务端排队执行，离开页面也会继续。"}
                                                            </Empty>
                                                        )}
                                                        <div className="table-footer">
                                                            <span>
                                                                {visible.length}{" "}
                                                                个 run
                                                            </span>
                                                            <span>
                                                                状态每秒同步，列表不会随进度自动重排
                                                            </span>
                                                        </div>
                                                    </section>
                                                    {selected && (
                                                        <aside className="inspector">
                                                            <div className="resize-control">
                                                                <label>
                                                                    面板宽度
                                                                    <input
                                                                        type="range"
                                                                        min={
                                                                            340
                                                                        }
                                                                        max={
                                                                            600
                                                                        }
                                                                        value={
                                                                            width
                                                                        }
                                                                        onChange={(
                                                                            e,
                                                                        ) =>
                                                                            setWidth(
                                                                                Number(
                                                                                    e
                                                                                        .target
                                                                                        .value,
                                                                                ),
                                                                            )
                                                                        }
                                                                    />
                                                                </label>
                                                            </div>
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
                                                                onFocus={() => {
                                                                    setFocusTarget(undefined);
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
                                {section === "batches" && (
                                    <div className="batches-list">
                                        {batches.length ? (
                                            batches.map(([id, name]) => {
                                                const group = jobs.filter(
                                                        (j) =>
                                                            j.batch_id === id,
                                                    ),
                                                    completed = group.filter(
                                                        (j) =>
                                                            j.status ===
                                                            "completed",
                                                    ).length;
                                                return (
                                                    <article
                                                        className="batch-row"
                                                        key={id}
                                                    >
                                                        <div className="batch-icon">
                                                            <Stack size={22} />
                                                        </div>
                                                        <div className="batch-info">
                                                            <h2>{name}</h2>
                                                            <p>
                                                                {group.length}{" "}
                                                                个 run /{" "}
                                                                {
                                                                    new Set(
                                                                        group.map(
                                                                            (
                                                                                j,
                                                                            ) =>
                                                                                j
                                                                                    .config
                                                                                    .model,
                                                                        ),
                                                                    ).size
                                                                }{" "}
                                                                个模型 /{" "}
                                                                {
                                                                    new Set(
                                                                        group.map(
                                                                            (
                                                                                j,
                                                                            ) =>
                                                                                j
                                                                                    .config
                                                                                    .preset,
                                                                        ),
                                                                    ).size
                                                                }{" "}
                                                                个 preset
                                                            </p>
                                                            <div
                                                                className="batch-segments"
                                                                aria-label={`已完成 ${completed} 个，总共 ${group.length} 个`}
                                                            >
                                                                {group.map(
                                                                    (j) => (
                                                                        <span
                                                                            key={
                                                                                j.id
                                                                            }
                                                                            className={`segment-${j.status}`}
                                                                            title={`${j.name}：${STATUS[j.status]}`}
                                                                        />
                                                                    ),
                                                                )}
                                                            </div>
                                                        </div>
                                                        <div className="batch-progress mono">
                                                            {completed}
                                                            <span>
                                                                {" "}
                                                                / {
                                                                    group.length
                                                                }{" "}
                                                                完成
                                                            </span>
                                                        </div>
                                                        <button className="text-button" onClick={() => openCurves(group.map(runKey))}>对比曲线{group.length > 4 ? "（前 4 个）" : ""}</button>
                                                        <Button
                                                            highContrast
                                                            variant="soft"
                                                            onClick={() => {
                                                                setBatchId(id);
                                                                setFilter(
                                                                    "all",
                                                                );
                                                                setSearch("");
                                                                setSection(group.some(isActive) ? "runs" : "archives");
                                                                setView("list");
                                                            }}
                                                        >
                                                            查看批次
                                                            <CaretRight />
                                                        </Button>
                                                    </article>
                                                );
                                            })
                                        ) : (
                                            <Empty
                                                title="还没有实验批次"
                                                action={
                                                    <Button
                                                        highContrast
                                                        onClick={() => create()}
                                                    >
                                                        新建实验
                                                    </Button>
                                                }
                                            >
                                                把不同模型、推理强度和种子放进一个批次。
                                            </Empty>
                                        )}
                                    </div>
                                )}
                                {section === "curves" && <PageBoundary name="曲线对比"><Suspense fallback={<p className="muted">正在加载曲线工作区…</p>}>
                                    <CurveBoard catalog={catalog} selectedIds={curveIds} onSelection={setCurveIds}
                                        onBack={curveOrigin ? () => { setSection(curveOrigin.section); setView(curveOrigin.view); setCurveOrigin(null); } : undefined} />
                                </Suspense></PageBoundary>}
                                {section === "archives" && view === "list" && (
                                    <PageBoundary name="历史存档"><Suspense fallback={<p className="muted">正在加载历史…</p>}>
                                        <RunHistory jobs={jobs} onCompare={openCurves} batchId={batchId} onBatchChange={setBatchId} onResume={archive => void resume(archive)} onRerun={archive => void resume(archive, true)} onClone={clone} onOpen={job => openHistory(job)}
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
                                {section === "templates" && (
                                    <div className="templates-list">
                                        {templates.length ? (
                                            templates.map((template, i) => (
                                                <article
                                                    key={i}
                                                    className="template-row"
                                                >
                                                    <div>
                                                        <h2>{template.name}</h2>
                                                        <p>
                                                            {template.rows
                                                                .map(
                                                                    (r) =>
                                                                        r.model ||
                                                                        "服务端环境模型",
                                                                )
                                                                .join(" / ")}
                                                            ，{template.preset}
                                                        </p>
                                                        <small>
                                                            不含 API Key
                                                        </small>
                                                    </div>
                                                    <Button
                                                        highContrast
                                                        onClick={() =>
                                                            create(
                                                                safeTemplate(
                                                                    template,
                                                                ),
                                                            )
                                                        }
                                                    >
                                                        使用模板
                                                        <Plus />
                                                    </Button>
                                                    <button
                                                        className="text-button"
                                                        onClick={() =>
                                                            setTemplates(
                                                                (items) =>
                                                                    items.filter(
                                                                        (
                                                                            _,
                                                                            n,
                                                                        ) =>
                                                                            n !==
                                                                            i,
                                                                    ),
                                                            )
                                                        }
                                                    >
                                                        删除
                                                    </button>
                                                </article>
                                            ))
                                        ) : (
                                            <Empty
                                                title="保存常用的实验配置"
                                                action={
                                                    <Button
                                                        highContrast
                                                        onClick={() => create()}
                                                    >
                                                        配置第一个模板
                                                    </Button>
                                                }
                                            >
                                                在新建实验中选择“保存为模板”，不保存凭证。
                                            </Empty>
                                        )}
                                    </div>
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
                    <footer className="workspace-footer">
                        <span>Guild Manager Bench</span>
                        <span>评测规则与运行管理分离</span>
                        <a className="font-credit" href="/workbench/fonts/NOTICE.txt" target="_blank" rel="noreferrer" title="MiSans 与 JetBrains Mono 的来源及授权">字体：MiSans</a>
                        <a href="/legacy/">
                            旧版调试页
                            <ArrowSquareOut size={12} />
                        </a>
                    </footer>
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
                            onSave={(template) =>
                                setTemplates((items) => [
                                    ...items.filter(
                                        (t) => t.name !== template.name,
                                    ),
                                    template,
                                ])
                            }
                            onCreated={(created) => {
                                setNewOpen(false);
                                setSection("runs");
                                setView("list");
                                setBatchId("all");
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
                            排队任务会立即取消。运行中的任务会请求保存并退出，阻塞超过等待时间后强制结束。未完成回合可能在续跑时回滚。
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
            setMessage("设置已保存。降低上限不会中断已启动的任务。");
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
            <p className="muted">
                每个 run
                使用独立工作进程，评分在该进程内顺序执行，不再为每局派生整机规模的进程池。
            </p>
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
                    <small>相同服务地址共享上限，不等同于请求速率限制。</small>
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
            <section className="settings-notes">
                <h3>运行与恢复</h3>
                <p>
                    关页、刷新或浏览器断线不会停止任务。关闭工作台服务会停止任务，重启后不会自动重复提交模型请求。
                </p>
                <p>
                    停止先请求协作退出，8
                    秒后仍未退出则强制结束。续跑以最近成功写入的原子检查点为准。
                </p>
                <p>
                    工作台用于可信的本机环境，不提供用户认证。不要将服务直接暴露到公网。
                </p>
            </section>
        </form>
    );
}
