import { runNotice } from "./runNotice";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@radix-ui/themes";
import {
    ArrowsOutSimple,
    Copy,
    PushPin,
    Stop,
    X,
    Pause,
    Broadcast,
    CaretLeft,
    CaretRight,
    Clock,
    UsersThree,
    Coins,
    Sparkle,
    ChartLineUp,
} from "@phosphor-icons/react";
import { api, useLiveWatch } from "./api";
import { fmt, isActive, modelEntries, PHASE, time } from "./model";
import { ElapsedTime } from "./ElapsedTime";
import { Empty, JsonDetails, StatusBadge, Trend } from "./ui";
import { ChangeValue, ResourceValue } from "./motion";
import { WatchMonitor } from "./WatchMonitor";
import { ModelCallStats } from "./ModelCallStats";
import { ModelContentTabs } from "./ModelContentTabs";
import type { ModelTab } from "./ModelContentTabs";
import { MemoPanel } from "./MemoPanel";
import { memoAt } from "./memoModel";
import { ActionTimeline } from "./ActionTimeline";
import type { ActionEntry } from "./actionModel";
import { TrajectoryProvider } from "./TrajectoryContext";
import type { TrajectoryPage } from "./trajectoryModel";
import { RunWorkspace } from "./RunWorkspace";
import { RunId } from "./RunId";
import type { RunModule, WatchFocus } from "./sceneNavigation";
import { SceneMaterials } from "./SceneMaterials";
import { MaterialExchangeStatus } from "./MaterialExchangeStatus";
import {
    ATTEMPT_STATUS,
    attemptLabel,
    isWatchEvent,
    livePosition,
    watchFrames,
    watchPresentation,
} from "./watchModel";
import type { WatchFrame, WatchPage, WatchPosition, WatchRecord } from "./watchModel";
import type { EventPage, EventRecord, Job } from "./types";
import "./watch.css";
import "./monitor.css";
import "./business-preview.css";

export type { WatchPosition } from "./watchModel";
export { livePosition } from "./watchModel";

interface ModelReasoningTarget { call: number; title: string }

export function WatchRun({
    job,
    runId,
    position,
    onPosition,
    compact = false,
    onStop,
    onClone,
    onCurves,
    onClose,
    onFocus,
    onPin,
    pinned,
    initialFocus,
}: {
    job: Job;
    runId: string;
    position: WatchPosition;
    onPosition: (position: WatchPosition) => void;
    compact?: boolean;
    onStop: (job: Job) => void;
    onClone: (job: Job) => void;
    onCurves?: () => void;
    onClose?: () => void;
    onFocus?: (target?: WatchFocus) => void;
    initialFocus?: WatchFocus;
    onPin?: () => void;
    pinned?: boolean;
}) {
    const following = position.attempt === null;
    const presentation = watchPresentation(job, following);
    const [utility, setUtility] = useState(false);
    const [historyRequested, setHistoryRequested] = useState(initialFocus?.actionView === "timeline" || initialFocus?.actionView === "search");
    const [reasoningTarget, setReasoningTarget] = useState<(ModelReasoningTarget & { page: WatchPage }) | null>(null);
    const [modelTab, setModelTab] = useState<ModelTab>("output");
    const [activeModule, setActiveModule] = useState<RunModule | null>(() => compact ? null : initialFocus?.module || null);
    const live = useLiveWatch(job);
    const history = useQuery({
        queryKey: ["watch", job.id, position.attempt],
        queryFn: ({ signal }) =>
            api<WatchPage>(`/api/workbench/jobs/${job.id}/watch?attempt=${position.attempt}`, {
                signal,
            }),
        enabled: position.attempt !== null && !position.frozen,
        gcTime: 60_000,
    });
    useEffect(() => {
        if (position.attempt !== null && !position.frozen && history.data && !history.isFetching)
            onPosition({ ...position, frozen: history.data });
    }, [position, history.data, history.isFetching, onPosition]);
    const page = position.frozen || (following ? live.data : history.data);
    const modelTarget = reasoningTarget && reasoningTarget.page === page ? reasoningTarget : undefined;
    const frames = useMemo(() => {
        const previous = page?.attempts
            .slice(
                0,
                page.attempts.findIndex((a) => a.id === page.selected),
            )
            .reverse()
            .find((a) => a.score !== null && a.status !== "rolled_back");
        return watchFrames(page?.records || [], previous?.score ?? null, page?.monster_stages, previous?.rank_contributions ?? null);
    }, [page]);
    const frame =
        position.cursor == null
            ? frames.at(-1)
            : frames.filter((f) => f.cursor <= position.cursor!).at(-1);
    const index = frame ? frames.indexOf(frame) : -1;
    const selected = page?.attempts.find((a) => a.id === page.selected);
    const latest = live.data?.attempts.at(-1);
    const frozenCursor = position.frozen?.live_cursor ?? position.frozen?.cursor;
    const hasNew = frozenCursor !== undefined && !!live.data?.records.some(record =>
        record.cursor > frozenCursor && isWatchEvent(record.event));
    const cutoff = following ? Infinity : (position.cursor ?? Infinity);
    const memo = memoAt(page, cutoff);
    const memoReverted = !!selected && ["rolled_back", "failed"].includes(selected.status);
    const modelRecords = useMemo(
        () => (page?.records || []).filter((r) => r.cursor <= cutoff),
        [page, cutoff],
    );
    const visibleFrames = useMemo(
        () => frames.filter((item) => item.cursor <= cutoff),
        [frames, cutoff],
    );
    const observation = frame?.observation || null;
    const historyNotice = page?.origin ? runNotice((page as TrajectoryPage).warning) : null;
    const actionsVisible = activeModule === "actions";

    const navigation = useRef<AbortController | null>(null);
    const [locationError, setLocationError] = useState("");
    useEffect(() => () => navigation.current?.abort(), []);
    function chooseFrame(next: WatchFrame) {
        navigation.current?.abort();
        setReasoningTarget(null);
        if (page) onPosition({ attempt: page.selected, cursor: next.cursor, frozen: page });
    }
    function showReasoning(entry: ActionEntry) {
        if (!page || entry.frame.call === null) return;
        chooseFrame(entry.frame);
        setReasoningTarget({ call: entry.frame.call, title: entry.title, page });
        setModelTab("output");
        setActiveModule("models");
    }
    async function locateTrajectory(attempt: number, cursor: number | null, origin: "archive" | "execution") {
        navigation.current?.abort();
        setReasoningTarget(null);
        const controller = new AbortController();
        navigation.current = controller;
        try {
            const next = await api<TrajectoryPage>(`/api/workbench/jobs/${job.id}/trajectories?attempt=${attempt}&origin=${origin}`, { signal: controller.signal });
            if (!controller.signal.aborted) {
                onPosition({ attempt: next.selected, cursor, frozen: { ...next, live_cursor: live.data?.cursor ?? 0 } });
                setLocationError("");
                if (cursor !== null) setActiveModule("actions");
            }
        } catch (error) { if (!controller.signal.aborted) throw error; }
    }
    async function chooseAttempt(attempt: number) {
        if (page?.origin) {
            try { await locateTrajectory(attempt, null, page.origin); }
            catch (error) { setLocationError((error as Error).message); }
        } else {
            setReasoningTarget(null);
            onPosition({ attempt, cursor: null });
        }
    }
    function pause() {
        navigation.current?.abort();
        if (page?.selected != null)
            onPosition({ attempt: page.selected, cursor: page.cursor, frozen: page });
    }
    function returnLive() {
        navigation.current?.abort();
        setReasoningTarget(null);
        setLocationError("");
        onPosition(livePosition);
    }
    const failure = live.error || history.error;
    const loading = !page && (following ? live.isPending : history.isFetching);
    const actionsPanel = (
        <ActionTimeline frames={frames} frame={frame} active={actionsVisible} following={following} initialView={initialFocus?.actionView}
            rolledBack={selected?.status === "rolled_back"} onFrame={chooseFrame} onWholeRun={() => setHistoryRequested(true)} onModelReasoning={showReasoning}
            renderDetails={item => <LazyRecord key={item.cursor} jobId={job.id} record={item.record} label="原始事件与调用参数" active={actionsVisible} trajectory={page?.origin} />} />
    );
    const modelsPanel = (
        <section className="spectator-models" aria-label="所选位置的模型调用">
            <ModelContentTabs value={modelTab} onChange={setModelTab}
                memo={<MemoPanel memo={memo} reverted={memoReverted} onActions={() => setActiveModule("actions")} />}
                output={<ModelPanel
                job={job}
                records={modelRecords}
                call={frame?.call ?? null}
                active={activeModule === "models" && modelTab === "output"}
                live={following && isActive(job)}
                reasoningTarget={modelTarget}
                onClearTarget={() => setReasoningTarget(null)}
                onReturnAction={() => { setReasoningTarget(null); setActiveModule("actions"); }}
                trajectory={page?.origin}
            />} />
        </section>
    );
    const runtime = (
        <div className="spectator-runtime">
            <StatusBadge status={job.status} turn={job.turn} maxTurns={job.max_turns} />
            {isActive(job) && <>
                <span>{PHASE[job.phase] || job.phase}</span>
                <span className="mono"><ElapsedTime start={job.phase_started_at} live /></span>
                <span className="runtime-budget">工具预算 {job.budget ? `${job.budget.used} / ${job.budget.max_tool_calls}` : "未记录"}</span>
            </>}
        </div>
    );
    if (compact) return <WatchMonitor job={job} runId={runId} page={page} live={live.data} frame={frame} frames={frames} records={modelRecords}
        position={position} hasNew={hasNew} loading={loading} failure={failure}
        onRetry={() => void (following ? live : history).refetch()} onPosition={onPosition} onFrame={chooseFrame} onPause={pause}
        onStop={onStop} onFocus={onFocus} onPin={onPin} onClose={onClose} pinned={pinned} />;
    return (
        <TrajectoryProvider job={job} enabled={["upgrades", "inventory"].includes(activeModule || "") || activeModule === "actions" && historyRequested} viewTurn={selected?.turn ?? null} selectedCursor={page?.origin ? frame?.cursor : undefined} onLocate={locateTrajectory}>
        <section
            className={`run-detail spectator spectator-full ${activeModule ? "is-module-expanded" : ""}`}
            aria-label={`${job.name} 的运行详情`}
        >
            <header className="spectator-header">
                <div className="spectator-title">
                    <RunId id={runId} />
                    <h2 title={job.name}>{job.name}</h2>
                    <span className="muted" title={`${job.config.provider} / ${job.config.model || "环境配置模型"}`}>
                        {job.config.provider} / {job.config.model || "环境配置模型"}
                    </span>
                </div>
                {runtime}
                <div className="detail-actions" role="group" aria-label="运行操作">
                    {onFocus && (
                        <button className="detail-action is-primary" onClick={() => onFocus()}>
                            <ArrowsOutSimple size={16} />专注查看
                        </button>
                    )}
                    {onCurves && (
                        <button className="detail-action" aria-label="查看运行曲线" title="在统一工作区查看经济、经验与战力" onClick={onCurves}>
                            <ChartLineUp size={16} />曲线
                        </button>
                    )}
                    {onPin && (
                        <button className="detail-action"
                            title={pinned ? "取消关注" : "关注此 run"}
                            aria-label={pinned ? "取消关注" : "关注此 run"}
                            aria-pressed={pinned} onClick={onPin}>
                            <PushPin size={16} weight={pinned ? "fill" : "regular"} />{pinned ? "已关注" : "关注"}
                        </button>
                    )}
                    {onClose && (
                        <button className="detail-close" title="关闭详情" aria-label="关闭详情" onClick={onClose}>
                            <X size={17} />
                        </button>
                    )}
                </div>
            </header>
            {(job.error || job.checkpoint_error) && (
                <p className="callout error" role="status">
                    {job.checkpoint_error ? `存档失败：${job.checkpoint_error}` : job.error}
                </p>
            )}
            {failure && (
                <div className="spectator-error" role="alert">
                    观战同步失败：{failure.message}。保留已读取的局面。
                    <button onClick={() => void (following ? live : history).refetch()}>
                        重新读取
                    </button>
                </div>
            )}
            {loading ? (
                <div className="skeleton-lines spectator-loading" aria-label="正在读取观战历史">
                    <i />
                    <i />
                    <i />
                </div>
            ) : !page?.records.length ? (
                <Empty title={job.status === "queued" ? "等待任务开始" : "尚无观战记录"} />
            ) : (
                <>
                    <div className="spectator-statusbar">
                    <div className={`spectator-viewpoint ${presentation.realtime ? "is-live" : following ? "is-final" : "is-history"}`}>
                        {presentation.realtime ? <Broadcast size={15} /> : <Clock size={15} />}
                        <strong>{presentation.label}</strong>
                        <span>
                            <ChangeValue value={selected?.id} variant="enter">{selected ? attemptLabel(selected) : "准备中"}</ChangeValue>
                            {selected?.status === "rolled_back" ? " / 已回滚" : ""}
                        </span>
                        <span className="viewpoint-action">{frame?.title}</span>
                        {!following && (
                            <span className="mono">{frame ? time(frame.record.at) : ""}</span>
                        )}
                    </div>
                    <div className="spectator-resources" aria-label="当前观看位置的资源">
                        <div>
                            <span>
                                <Coins size={14} />
                                金币
                            </span>
                            <b><ResourceValue value={observation?.gold} live={following && isActive(job)} scope={`${job.id}:${page.selected}`} label="金币">{fmt(observation?.gold)}</ResourceValue></b>
                        </div>
                        <div>
                            <span>
                                <Sparkle size={14} />
                                经验池
                            </span>
                            <b><ResourceValue value={observation?.experience_pool} live={following && isActive(job)} scope={`${job.id}:${page.selected}`} label="经验池">{fmt(observation?.experience_pool)}</ResourceValue></b>
                        </div>
                        <div>
                            <span>
                                <UsersThree size={14} />
                                队伍
                            </span>
                            <b>
                                <ChangeValue value={observation ? `${observation.adventurers.length}/${observation.party_size_limit}` : "unknown"}>
                                    {observation ? `${observation.adventurers.length} / ${observation.party_size_limit}` : "未知"}
                                </ChangeValue>
                            </b>
                        </div>
                        <div>
                            <span>
                                <ChartLineUp size={14} />
                                已记录评分
                            </span>
                            <b><ChangeValue value={frame?.score ?? "unknown"}>{fmt(frame?.score)}</ChangeValue></b>
                        </div>
                        {observation?.material_exchange && <div className="spectator-materials">
                            <MaterialExchangeStatus turn={observation.turn} exchange={observation.material_exchange} />
                        </div>}
                        <SceneMaterials materials={observation?.materials} />
                    </div>
                    </div>
                    {locationError && <p role="alert" className="spectator-notice">历史定位失败：{locationError}</p>}
                    {historyNotice && <p className="spectator-notice">{historyNotice}</p>}
                    {selected?.status === "rolled_back" && (
                        <p className="spectator-notice">
                            这次尝试已撤销，正在回看当时的局面。
                        </p>
                    )}
                    {observation && selected?.turn != null && observation.turn > selected.turn && !(following && job.status === "completed" && observation.finished) && (
                        <p className="spectator-notice">
                            {observation.finished
                                ? "最终局面"
                                : `结算后局面已进入回合 ${observation.turn}`}
                            ，该操作仍属于回合 {selected.turn}。
                        </p>
                    )}
                    <RunWorkspace
                            observation={observation}
                            contributions={frame?.rank_contributions ?? null}
                            before={frame?.before ?? null}
                            frames={visibleFrames}
                            records={modelRecords}
                            memo={memo} modelTab={modelTab} onModelTab={setModelTab} memoReverted={memoReverted}
                            activeModule={activeModule}
                            onModule={setActiveModule}
                            onFrame={chooseFrame}
                            actions={actionsPanel}
                            models={modelsPanel}
                            initialSelection={initialFocus?.selection}
                            live={following && isActive(job)}
                        />

                </>
            )}
            <div className="spectator-controls">
            <div className="spectator-scrubber">
                <label>
                    <span className="scrubber-label">定位回合</span>
                    <select
                        aria-label="选择观战回合"
                        value={position.attempt ?? live.data?.selected ?? ""}
                        disabled={!(page?.origin ? page : live.data)?.attempts.length}
                        onChange={(e) => void chooseAttempt(Number(e.target.value))}
                    >
                        {!(page?.origin ? page : live.data)?.attempts.length && <option value="">尚无回合</option>}
                        {((page?.origin ? page : live.data)?.attempts || []).map((a) => (
                            <option key={a.id} value={a.id}>
                                {attemptLabel(a)} / {ATTEMPT_STATUS[a.status] || a.status}
                            </option>
                        ))}
                    </select>
                </label>
                <div className="spectator-step-controls">
                    <button
                        aria-label="上一步"
                        title="上一步"
                        disabled={index <= 0}
                        onClick={() => chooseFrame(frames[index - 1])}
                    >
                        <CaretLeft />
                    </button>
                    <span className="mono">
                        {index + 1} / {frames.length}
                    </span>
                    <button
                        aria-label="下一步"
                        title="下一步"
                        disabled={index < 0 || index >= frames.length - 1}
                        onClick={() => chooseFrame(frames[index + 1])}
                    >
                        <CaretRight />
                    </button>
                </div>
                {following && isActive(job) ? (
                    <button
                        className="watch-follow active"
                        onClick={pause}
                        disabled={!page?.selected}
                    >
                        <Pause size={15} />
                        暂停跟随
                    </button>
                ) : !following ? (
                    <button className="watch-follow" onClick={returnLive}>
                        {isActive(job) ? <Broadcast size={15} /> : <Clock size={15} />}
                        {presentation.returnLabel}{isActive(job) && hasNew ? "（有新事件）" : ""}
                    </button>
                ) : null}
            </div>
            <footer className="detail-footer spectator-footer">
                <Button
                    highContrast
                    variant="soft"
                    color="gray"
                    size="1"
                    onClick={() => onClone(job)}
                >
                    <Copy />
                    复制配置
                </Button>
                {isActive(job) && (
                    <Button
                        highContrast
                        variant="soft"
                        color="red"
                        size="1"
                        disabled={job.status === "stopping"}
                        onClick={() => onStop(job)}
                    >
                        <Stop />
                        {job.status === "queued"
                            ? "取消排队"
                            : job.status === "stopping"
                              ? "停止中"
                              : "停止运行"}
                    </Button>
                )}
                <button
                    className="text-button"
                    onClick={() => setUtility(!utility)}
                    aria-expanded={utility}
                >
                    {utility ? "收起运行信息" : "运行信息与产物"}
                </button>
                <span className="spectator-footer-note">
                    {latest?.turn != null ? `任务最新回合 ${latest.turn}` : "等待初始化"}
                </span>
            </footer>
            </div>
            {utility && (
                <section className="spectator-utility">
                    <div className="spectator-column-title">
                        <h3>{isActive(job) ? "实时运行信息" : "最终运行信息"}</h3>
                    </div>
                    <p>
                        最近活动 {time(job.updated_at)} / 最近存档 {time(job.checkpoint_at)}
                    </p>
                    <p>
                        已报告输入 / 输出 Token：{fmt(job.usage.input_tokens)} /{" "}
                        {fmt(job.usage.output_tokens)}，重试通知 {job.retries}，失败工具{" "}
                        {job.tool_failures}
                    </p>
                    <Trend points={job.scores} large />
                    <JsonDetails title="请求配置（不含凭证）" value={job.config} />
                    <JsonDetails title="实际评测配置" value={job.effective_config} />
                </section>
            )}
        </section>
        </TrajectoryProvider>
    );
}

function ModelPanel({
    job,
    records,
    call,
    active,
    live,
    reasoningTarget,
    onClearTarget,
    onReturnAction,
    trajectory,
}: {
    job: Job;
    records: WatchRecord[];
    call: number | null;
    active: boolean;
    live: boolean;
    reasoningTarget?: ModelReasoningTarget;
    onClearTarget: () => void;
    onReturnAction: () => void;
    trajectory?: "archive" | "execution";
}) {
    const models = useMemo(() => modelEntries(records), [records]);
    const [chosen, setChosen] = useState<number | null>(null);
    const previousCall = useRef(call);
    // 行动定位优先于模型面板内的手动选择。
    const effective = previousCall.current === call ? chosen : null;
    useEffect(() => {
        if (previousCall.current !== call) {
            setChosen(null);
            previousCall.current = call;
        }
    }, [call]);
    const viewport = useRef<HTMLDivElement>(null);
    const viewportScroll = useRef(0);
    useLayoutEffect(() => {
        if (active && viewport.current) viewport.current.scrollTop = viewportScroll.current;
    }, [active]);
    const model = reasoningTarget
        ? models.find((m) => m.key === reasoningTarget.call)
        : models.find((m) => m.key === effective) || models.find((m) => m.key === call) || models.at(-1);
    const reasoning = useRef<HTMLDetailsElement>(null);
    useLayoutEffect(() => {
        viewportScroll.current = 0;
        if (viewport.current) viewport.current.scrollTop = 0;
    }, [model?.key]);
    useLayoutEffect(() => {
        if (!active || !reasoningTarget || model?.key !== reasoningTarget.call || !reasoning.current || !viewport.current) return;
        reasoning.current.open = true;
        viewport.current.scrollTop += reasoning.current.getBoundingClientRect().top - viewport.current.getBoundingClientRect().top - 8;
    }, [active, reasoningTarget, model?.key, model?.reasoning]);
    const returnedTools = (model?.response as { tool_calls?: unknown[] } | undefined)?.tool_calls;
    const request = records.find((r) => r.cursor === model?.key);
    const response =
        model &&
        records.find((r) => r.event.type === "model_response" && r.event.step === model.step);
    const reasoningPanel = model && (model.reasoning || reasoningTarget) && (
        <details className="reasoning model-linked-reasoning" key={model.key} ref={reasoning}>
            <summary>API 提供的推理文本或摘要</summary>
            {model.reasoning ? <>
                {reasoningTarget && returnedTools && returnedTools.length > 1 && <p className="model-reasoning-note">这 {returnedTools.length} 个工具调用共用下面这段模型思考。</p>}
                <pre>{model.reasoning}</pre>
            </> : <p className="model-reasoning-note">这次调用没有保存思考过程。</p>}
        </details>
    );
    return (
        <>
            <div className="spectator-column-title">
                <h3>模型调用</h3>
                <span>{models.length} 次</span>
            </div>
            {reasoningTarget && <div className="model-reasoning-source" role="status">
                <span>来自工具调用：<strong>{reasoningTarget.title}</strong></span>
                <button onClick={onReturnAction}><CaretLeft size={13} />返回该工具</button>
            </div>}
            {!model ? (
                <p className="scene-empty">{reasoningTarget ? "找不到对应的模型调用记录。" : "该位置尚未发出模型请求"}</p>
            ) : (
                <div
                    className="spectator-model-content"
                    ref={viewport}
                    onScroll={(event) => {
                        if (active) viewportScroll.current = event.currentTarget.scrollTop;
                    }}
                >
                    <select
                        aria-label="选择模型调用"
                        value={model.key}
                        onChange={(e) => { setChosen(Number(e.target.value)); onClearTarget(); }}
                    >
                        {models.map((entry, i) => (
                            <option key={entry.key} value={entry.key}>
                                调用 {entry.step ?? i + 1} /{" "}
                                {entry.response ? "已返回" : "尚无完整响应"}
                            </option>
                        ))}
                    </select>
                    <div className="model-call-config">
                        <span>当前 run 配置</span>
                        <strong>{job.config.model || "服务端默认模型"}</strong>
                        <span>接口 {job.config.provider}</span>
                        <span>配置强度 {job.config.reasoning_effort || "默认"}</span>
                    </div>
                    <ModelCallStats model={model} live={live && active && model === models.at(-1)} />
                    {reasoningTarget && reasoningPanel}
                    {model.text ? (
                        <pre className="watch-model-text">{model.text}</pre>
                    ) : (
                        <p className="scene-empty">
                            {model.response ? "本次回复没有正文。" : "尚未收到正文。"}
                        </p>
                    )}
                    {!reasoningTarget && reasoningPanel}
                    {model.response != null && (
                        <JsonDetails title="工具调用与用量" value={model.response} />
                    )}
                    {request && (
                        <LazyRecord
                            jobId={job.id}
                            record={request}
                            label="完整模型输入"
                            active={active}
                            trajectory={trajectory}
                        />
                    )}
                    {response && (
                        <LazyRecord
                            jobId={job.id}
                            record={response}
                            label="原始模型响应"
                            active={active}
                            trajectory={trajectory}
                        />
                    )}
                </div>
            )}
        </>
    );
}
function LazyRecord({
    jobId,
    record,
    label,
    active = true,
    trajectory,
}: {
    jobId: string;
    record: WatchRecord;
    label: string;
    active?: boolean;
    trajectory?: "archive" | "execution";
}) {
    const [open, setOpen] = useState(false);
    const query = useQuery({
        queryKey: ["watch-raw", jobId, trajectory, record.after],
        queryFn: ({ signal }) =>
            api<EventPage | EventRecord>(trajectory ? `/api/workbench/jobs/${jobId}/trajectories?raw_after=${record.after}&origin=${trajectory}` : `/api/workbench/jobs/${jobId}/events?after=${record.after}&limit=1`, {
                signal,
            }),
        enabled: open && active,
        staleTime: Infinity,
        gcTime: 60_000,
    });
    return (
        <details className="json-details" onToggle={(e) => setOpen(e.currentTarget.open)}>
            <summary>{label}</summary>
            {open &&
                (query.isPending ? (
                    <p className="muted">正在读取…</p>
                ) : query.error ? (
                    <p role="alert">
                        读取失败：{query.error.message}
                        <button onClick={() => void query.refetch()}>重试</button>
                    </p>
                ) : (
                    <pre>{JSON.stringify(query.data && ("events" in query.data ? query.data.events[0]?.event : query.data.event), null, 2)}</pre>
                ))}
        </details>
    );
}
