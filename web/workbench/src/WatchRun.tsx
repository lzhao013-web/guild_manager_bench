import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@radix-ui/themes";
import {
    ArrowSquareOut,
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
import { duration, fmt, isActive, modelEntries, PHASE, time } from "./model";
import { Empty, JsonDetails, StatusBadge, Trend } from "./ui";
import { WatchMonitor } from "./WatchMonitor";
import { ActionTimeline } from "./ActionTimeline";
import { TrajectoryProvider } from "./TrajectoryContext";
import type { TrajectoryPage } from "./trajectoryModel";
import { RunWorkspace } from "./RunWorkspace";
import type { RunModule, WatchFocus } from "./sceneNavigation";
import { SceneMaterials } from "./SceneMaterials";
import {
    ATTEMPT_STATUS,
    attemptLabel,
    livePosition,
    watchFrames,
} from "./watchModel";
import type { WatchFrame, WatchPage, WatchPosition, WatchRecord } from "./watchModel";
import type { EventPage, EventRecord, Job } from "./types";
import "./watch.css";
import "./monitor.css";
import "./business-preview.css";

export type { WatchPosition } from "./watchModel";
export { livePosition } from "./watchModel";

export function WatchRun({
    job,
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
    const [utility, setUtility] = useState(false);
    const [historyRequested, setHistoryRequested] = useState(false);
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
    const frames = useMemo(() => {
        const previous = page?.attempts
            .slice(
                0,
                page.attempts.findIndex((a) => a.id === page.selected),
            )
            .reverse()
            .find((a) => a.score !== null);
        return watchFrames(page?.records || [], previous?.score ?? null);
    }, [page]);
    const frame =
        position.cursor == null
            ? frames.at(-1)
            : frames.filter((f) => f.cursor <= position.cursor!).at(-1);
    const index = frame ? frames.indexOf(frame) : -1;
    const selected = page?.attempts.find((a) => a.id === page.selected);
    const latest = live.data?.attempts.at(-1);
    const hasNew = !!position.frozen && !!live.data && live.data.cursor > (position.frozen.live_cursor ?? position.frozen.cursor);
    const cutoff = following ? Infinity : (position.cursor ?? Infinity);
    const modelRecords = useMemo(
        () => (page?.records || []).filter((r) => r.cursor <= cutoff),
        [page, cutoff],
    );
    const visibleFrames = useMemo(
        () => frames.filter((item) => item.cursor <= cutoff),
        [frames, cutoff],
    );
    const observation = frame?.observation || null;
    const actionsVisible = activeModule === "actions";

    const navigation = useRef<AbortController | null>(null);
    const [locationError, setLocationError] = useState("");
    useEffect(() => () => navigation.current?.abort(), []);
    function chooseFrame(next: WatchFrame) {
        navigation.current?.abort();
        if (page) onPosition({ attempt: page.selected, cursor: next.cursor, frozen: page });
    }
    async function locateTrajectory(attempt: number, cursor: number | null, origin: "archive" | "execution") {
        navigation.current?.abort();
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
        } else onPosition({ attempt, cursor: null });
    }
    function pause() {
        navigation.current?.abort();
        if (page?.selected != null)
            onPosition({ attempt: page.selected, cursor: page.cursor, frozen: page });
    }
    function returnLive() {
        navigation.current?.abort();
        setLocationError("");
        onPosition(livePosition);
    }
    const failure = live.error || history.error;
    const loading = !page && (following ? live.isPending : history.isFetching);
    const actionsPanel = (
        <ActionTimeline frames={frames} frame={frame} active={actionsVisible} following={following}
            rolledBack={selected?.status === "rolled_back"} onFrame={chooseFrame} onWholeRun={() => setHistoryRequested(true)}
            renderDetails={item => <><BattleResults event={item.record.event} />
                <LazyRecord key={item.cursor} jobId={job.id} record={item.record} label="原始事件与调用参数" active={actionsVisible} trajectory={page?.origin} /></>} />
    );
    const modelsPanel = (
        <section className="spectator-models" aria-label="所选位置的模型调用">
            <ModelPanel
                job={job}
                records={modelRecords}
                call={frame?.call ?? null}
                active={activeModule === "models"}
                trajectory={page?.origin}
            />
        </section>
    );
    const runtime = (
        <div className="spectator-runtime">
            <StatusBadge status={job.status} />
            <span>{PHASE[job.phase] || job.phase}</span>
            {isActive(job) && <span className="mono">{duration(job.phase_started_at)}</span>}
            <span className="runtime-budget">工具预算 {job.budget ? `${job.budget.used} / ${job.budget.max_tool_calls}` : "未记录"}</span>
        </div>
    );
    if (compact) return <WatchMonitor job={job} page={page} live={live.data} frame={frame} frames={frames} records={modelRecords}
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
                    <span className="subtle-label">{job.batch_name}</span>
                    <h2 title={job.name}>{job.name}</h2>
                    <span className="muted" title={`${job.config.provider} / ${job.config.model || "环境配置模型"}`}>
                        {job.config.provider} / {job.config.model || "环境配置模型"}
                    </span>
                </div>
                {runtime}
                <div className="icon-actions">
                    {onCurves && <button aria-label="查看运行曲线" title="在统一工作区查看经济、经验与战力" onClick={onCurves}><ChartLineUp size={17} /></button>}
                    {onPin && (
                        <button
                            title={pinned ? "取消关注" : "关注此 run"}
                            aria-label={pinned ? "取消关注" : "关注此 run"}
                            aria-pressed={pinned}
                            onClick={onPin}
                        >
                            <PushPin weight={pinned ? "fill" : "regular"} />
                        </button>
                    )}
                    {onFocus && (
                        <button title="专注查看" aria-label="专注查看" onClick={() => onFocus()}>
                            <ArrowsOutSimple />
                        </button>
                    )}
                    {onClose && (
                        <button title="关闭详情" aria-label="关闭详情" onClick={onClose}>
                            <X />
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
                <Empty title={job.status === "queued" ? "等待任务开始" : "尚无观战事件"}>
                    任务初始化后会显示真实局面和行动，不会为观战额外请求模型。
                </Empty>
            ) : (
                <>
                    <div className="spectator-statusbar">
                    <div className={`spectator-viewpoint ${following ? "is-live" : "is-history"}`}>
                        {following ? <Broadcast size={15} /> : <Clock size={15} />}
                        <strong>{following ? "实时局面" : "历史局面"}</strong>
                        <span>
                            {selected ? attemptLabel(selected) : "准备中"}
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
                            <b>{fmt(observation?.gold)}</b>
                        </div>
                        <div>
                            <span>
                                <Sparkle size={14} />
                                经验池
                            </span>
                            <b>{fmt(observation?.experience_pool)}</b>
                        </div>
                        <div>
                            <span>
                                <UsersThree size={14} />
                                队伍
                            </span>
                            <b>
                                {observation
                                    ? `${observation.adventurers.length} / ${observation.party_size_limit}`
                                    : "未知"}
                            </b>
                        </div>
                        <div>
                            <span>
                                <ChartLineUp size={14} />
                                已记录评分
                            </span>
                            <b>{fmt(frame?.score)}</b>
                        </div>
                        <SceneMaterials materials={observation?.materials} />
                    </div>
                    </div>
                    {locationError && <p role="alert" className="spectator-notice">历史定位失败：{locationError}</p>}
                    {page.origin && <p className="spectator-notice">正在查看{page.origin === "archive" ? "整局归档" : "当前运行阶段"}的真实历史，仅切换观看位置，不改变任务状态。{(page as TrajectoryPage).warning}</p>}
                    {selected?.status === "rolled_back" && (
                        <p className="spectator-notice">
                            本次尝试已回滚。这里保留当时的真实记录，不代表当前任务局面。
                        </p>
                    )}
                    {observation && selected?.turn != null && observation.turn > selected.turn && (
                        <p className="spectator-notice">
                            {observation.finished
                                ? "最终局面"
                                : `结算后局面已进入回合 ${observation.turn}`}
                            ，该操作仍属于回合 {selected.turn}。
                        </p>
                    )}
                    <RunWorkspace
                            observation={observation}
                            before={frame?.before ?? null}
                            frames={visibleFrames}
                            records={modelRecords}
                            activeModule={activeModule}
                            onModule={setActiveModule}
                            onFrame={chooseFrame}
                            actions={actionsPanel}
                            models={modelsPanel}
                            initialSelection={initialFocus?.selection}
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
                {following ? (
                    <button
                        className="watch-follow active"
                        onClick={pause}
                        disabled={!page?.selected}
                    >
                        <Pause size={15} />
                        暂停跟随
                    </button>
                ) : (
                    <button className="watch-follow" onClick={returnLive}>
                        <Broadcast size={15} />
                        返回实时{hasNew ? "（有新事件）" : ""}
                    </button>
                )}
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
                        <h3>实时运行信息</h3>
                        <span>不随历史局面回退</span>
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
                    {job.archive_id && (
                        <div className="artifact-links">
                            <a href={`/replay/#run=${encodeURIComponent(job.archive_id)}`}>
                                打开完整回放
                                <ArrowSquareOut />
                            </a>
                            <a
                                href={`/replay/profile.html?run=${encodeURIComponent(job.archive_id)}`}
                            >
                                查看经营画像
                                <ArrowSquareOut />
                            </a>
                        </div>
                    )}
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
    trajectory,
}: {
    job: Job;
    records: WatchRecord[];
    call: number | null;
    active: boolean;
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
    const model =
        models.find((m) => m.key === effective) ||
        models.find((m) => m.key === call) ||
        models.at(-1);
    useLayoutEffect(() => {
        viewportScroll.current = 0;
        if (viewport.current) viewport.current.scrollTop = 0;
    }, [model?.key]);
    const request = records.find((r) => r.cursor === model?.key);
    const response =
        model &&
        records.find((r) => r.event.type === "model_response" && r.event.step === model.step);
    return (
        <>
            <div className="spectator-column-title">
                <h3>模型调用</h3>
                <span>{models.length} 次</span>
            </div>
            {!model ? (
                <p className="scene-empty">该位置尚未发出模型请求</p>
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
                        onChange={(e) => setChosen(Number(e.target.value))}
                    >
                        {models.map((entry, i) => (
                            <option key={entry.key} value={entry.key}>
                                调用 {entry.step ?? i + 1} /{" "}
                                {entry.response ? "已返回" : "尚无完整响应"}
                            </option>
                        ))}
                    </select>
                    <p className="muted">
                        {model.response ? "API 已返回" : "此位置尚未记录完整响应"}
                    </p>
                    {model.text ? (
                        <pre className="watch-model-text">{model.text}</pre>
                    ) : (
                        <p className="scene-empty">
                            {model.response ? "本次未返回正文，可能仅调用工具。" : "尚未收到正文。"}
                        </p>
                    )}
                    {model.response != null && (
                        <div className="model-usage mono">
                            输入{" "}
                            {fmt(
                                (model.response as any).usage?.input_tokens ??
                                    (model.response as any).usage?.prompt_tokens,
                            )}{" "}
                            / 输出{" "}
                            {fmt(
                                (model.response as any).usage?.output_tokens ??
                                    (model.response as any).usage?.completion_tokens,
                            )}{" "}
                            Token
                        </div>
                    )}
                    {model.reasoning && (
                        <details className="reasoning">
                            <summary>API 提供的推理文本或摘要</summary>
                            <pre>{model.reasoning}</pre>
                        </details>
                    )}
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
function BattleResults({ event }: { event: EventRecord["event"] }) {
    if (event.name === "get_battle_log" && event.result?.ok === true) {
        const result = event.result;
        return (
            <div className="watch-battles">
                <h3>模型已查询的战斗日志</h3>
                {result.battle && (
                    <p>
                        {result.battle.adventurer_name} 对 {result.battle.monster_name}
                    </p>
                )}
                {result.events && (
                    <JsonDetails
                        title={`已记录 ${result.events.length} 条行动事件`}
                        value={result.events}
                    />
                )}
                {result.statistics && (
                    <JsonDetails title="已记录的伤害、治疗与技能统计" value={result.statistics} />
                )}
                {result.battles && <JsonDetails title="已记录的战斗目录" value={result.battles} />}
                <p>只展示模型已查询并返回的记录，不额外调用工具或重新模拟战斗。</p>
            </div>
        );
    }
    const battles = event.result?.turn_result?.battles;
    if (!Array.isArray(battles) || !battles.length) return null;
    const outcome: Record<string, string> = { left_win: "胜利", right_win: "失败", draw: "平局" };
    return (
        <div className="watch-battles">
            <h3>已记录的战斗结果</h3>
            {battles.map((battle, i) => (
                <details key={i}>
                    <summary>
                        {battle.adventurer_name || "冒险者"} 对 {battle.monster_name || "怪物"}
                        <strong>
                            {typeof battle.won === "boolean"
                                ? battle.won
                                    ? "胜利"
                                    : "失败"
                                : outcome[battle.combat?.outcome || battle.outcome] ||
                                  battle.combat?.outcome ||
                                  battle.outcome ||
                                  "结果未记录"}
                        </strong>
                    </summary>
                    <p>
                        奖励：金币 {fmt(battle.reward?.gold)} / 经验{" "}
                        {fmt(battle.reward?.experience)}
                    </p>
                    <JsonDetails title="战斗返回记录" value={battle} />
                </details>
            ))}
        </div>
    );
}
