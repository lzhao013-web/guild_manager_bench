import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@radix-ui/themes";
import {
    ArrowSquareOut,
    ArrowsOutSimple,
    Copy,
    PushPin,
    Stop,
    X,
    ArrowDown,
    ChartLineUp,
} from "@phosphor-icons/react";
import { useEvents, useLiveWatch } from "./api";
import { ACTION_STATUS, actionEntries, actionGroups, actionSummary, recentActions } from "./actionModel";
import type { ActionEntry } from "./actionModel";
import { watchFrames } from "./watchModel";
import "./actions.css";
import {
    duration,
    fmt,
    isActive,
    modelEntries,
    PHASE,
    time,
} from "./model";
import { Empty, JsonDetails, Metric, StatusBadge, Trend } from "./ui";
import type { Job } from "./types";

export function RunDetail({
    job,
    compact = false,
    onStop,
    onClone,
    onCurves,
    onClose,
    onFocus,
    onPin,
    pinned,
}: {
    job: Job;
    compact?: boolean;
    onStop: (job: Job) => void;
    onClone: (job: Job) => void;
    onCurves?: () => void;
    onClose?: () => void;
    onFocus?: () => void;
    onPin?: () => void;
    pinned?: boolean;
}) {
    const [tab, setTab] = useState("overview");
    const [follow, setFollow] = useState(true);
    const { records, error, loading } = useEvents(job.id, isActive(job));
    const scroll = useRef<HTMLDivElement>(null);
    const models = useMemo(() => modelEntries(records), [records]);
    const watch = useLiveWatch(job);
    const frames = useMemo(() => watchFrames(watch.data?.records || []), [watch.data]);
    const entries = useMemo(() => actionEntries(frames), [frames]);
    const groups = useMemo(() => actionGroups(entries), [entries]);
    const recent = useMemo(() => recentActions(frames), [frames]);
    const recentEntries = recent.actions.length ? recent.actions.slice(0, 5) : recent.queries.slice(-1);
    function renderAction(entry: ActionEntry) {
        return <article className={`action ${entry.status === "failed" ? "failed" : ""}`} key={entry.frame.cursor}>
            <header><strong>{entry.title}</strong><span className={`action-status is-${entry.status}`}>{ACTION_STATUS[entry.status]}</span>
                <span className="muted mono">{time(entry.frame.record.at)}</span></header>
            {entry.rolledBack && <p className="action-rollback">本次尝试已回滚，不再生效</p>}
            <p>{actionSummary(entry)}</p>
            {entry.effects.length > 0 && <details><summary>{entry.effects.length} 项实际局面变化</summary><ul>{entry.effects.map((effect, index) => <li key={index}>{effect}</li>)}</ul></details>}
            <JsonDetails title="原始参数与返回" value={entry.frame.record.event} />
        </article>;
    }
    const diagnostics = records.filter((r) =>
        [
            "retry",
            "turn_retry",
            "turn_failed",
            "worker_failed",
            "checkpoint_failed",
            "worker_stopped",
        ].includes(r.event.type),
    );
    useEffect(() => {
        if (follow && tab !== "overview" && scroll.current)
            scroll.current.scrollTop = scroll.current.scrollHeight;
    }, [records, follow, tab]);
    const tabs = compact
        ? [
              ["overview", "概览"],
              ["actions", "行动"],
              ["models", "模型"],
          ]
        : [
              ["overview", "概览"],
              ["actions", "行动时间线"],
              ["models", "模型调用"],
              ["diagnostics", "诊断"],
              ["config", "配置与产物"],
          ];
    return (
        <section
            className={`run-detail ${compact ? "compact-detail" : ""}`}
            aria-label={`${job.name} 的运行详情`}
        >
            <header className="detail-header">
                <div className="detail-title">
                    <span className="subtle-label" title={job.archive_id || job.id}>
                        RUN / {(job.archive_id || job.id).slice(0, 22)}
                    </span>
                    <h2>{job.name}</h2>
                    <span className="muted">{job.batch_name}</span>
                </div>
                <div className="icon-actions">
                    {onCurves && <button aria-label="查看运行曲线" title="查看经济、经验与战力曲线" onClick={onCurves}><ChartLineUp size={17} /></button>}
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
                        <button
                            title="专注查看"
                            aria-label="专注查看"
                            onClick={onFocus}
                        >
                            <ArrowsOutSimple />
                        </button>
                    )}
                    {onClose && (
                        <button
                            title="关闭详情"
                            aria-label="关闭详情"
                            onClick={onClose}
                        >
                            <X />
                        </button>
                    )}
                </div>
            </header>
            <div className="detail-status">
                <StatusBadge status={job.status} />
                <span>{PHASE[job.phase] || job.phase}</span>
                {isActive(job) && (
                    <span className="mono muted">
                        {duration(job.phase_started_at)}
                    </span>
                )}
            </div>
            {job.error && (
                <p
                    className={`callout ${job.status === "failed" ? "error" : ""}`}
                    role="status"
                >
                    {job.error}
                </p>
            )}
            {job.checkpoint_error && (
                <p className="callout error" role="alert">
                    存档失败：{job.checkpoint_error}
                </p>
            )}
            {job.status === "stopping" && (
                <p className="callout">
                    已请求停止，等待进程确认。长时间阻塞时将强制结束，并保留最近成功写入的检查点。
                </p>
            )}
            <nav className="detail-tabs" aria-label="运行详情内容">
                {tabs.map(([key, label]) => (
                    <button
                        key={key}
                        className={tab === key ? "active" : ""}
                        aria-current={tab === key ? "page" : undefined}
                        onClick={() => setTab(key)}
                    >
                        {label}
                    </button>
                ))}
            </nav>
            <div
                className="detail-body"
                ref={scroll}
                onScroll={(e) => {
                    if (tab === "overview") return;
                    const el = e.currentTarget;
                    setFollow(
                        el.scrollHeight - el.scrollTop - el.clientHeight < 64,
                    );
                }}
            >
                {tab === "overview" && (
                    <>
                        <div className="metric-grid">
                            <Metric
                                label="当前回合"
                                value={
                                    job.turn == null
                                        ? "尚未开始"
                                        : `${job.turn} / ${job.max_turns}`
                                }
                            />
                            <Metric
                                label="最新 Rank Score"
                                value={fmt(job.last_score)}
                                note="使用已记录评分，不在浏览器重算"
                            />
                            <Metric
                                label="模型 / 工具调用"
                                value={`${fmt(job.calls)} / ${fmt(job.tools)}`}
                            />
                            <Metric
                                label="运行耗时"
                                value={duration(job.started_at, job.ended_at)}
                            />
                        </div>
                        <section className="detail-section">
                            <div className="section-heading">
                                <h3>战力走势</h3>
                                <span className="muted">按真实回合</span>
                            </div>
                            <Trend points={job.scores} large />
                            <div className="chart-axis">
                                <span>
                                    首个已记录回合{" "}
                                    {job.scores[0]?.turn ?? "未知"}
                                </span>
                                <span>
                                    最新回合 {job.scores.at(-1)?.turn ?? "未知"}
                                </span>
                            </div>
                        </section>
                        <section className="detail-section">
                            <div className="section-heading">
                                <h3>当前队伍</h3>
                                <span className="mono muted">
                                    {job.observation?.adventurers.length ?? 0} /{" "}
                                    {job.observation?.party_size_limit ??
                                        "未知"}
                                </span>
                            </div>
                            {job.observation ? (
                                <>
                                    <div className="resource-line">
                                        <span>
                                            金币{" "}
                                            <b>{fmt(job.observation.gold)}</b>
                                        </span>
                                        <span>
                                            经验池{" "}
                                            <b>
                                                {fmt(
                                                    job.observation
                                                        .experience_pool,
                                                )}
                                            </b>
                                        </span>
                                    </div>
                                    <div className="party-list">
                                        {job.observation.adventurers.length ? (
                                            job.observation.adventurers.map(
                                                (member) => (
                                                    <div
                                                        className="party-member"
                                                        key={
                                                            member.adventurer_id
                                                        }
                                                    >
                                                        <img
                                                            src={`/assets/icons/classes/${encodeURIComponent(member.template_id)}.png`}
                                                            alt=""
                                                            loading="lazy"
                                                        />
                                                        <div>
                                                            <strong>
                                                                {member.name}
                                                            </strong>
                                                            <span className="muted">
                                                                Lv.{" "}
                                                                {member.level}
                                                            </span>
                                                        </div>
                                                        <div className="party-resources">
                                                            <span>
                                                                HP{" "}
                                                                <b>
                                                                    {
                                                                        member
                                                                            .resources
                                                                            .current_hp
                                                                    }{" "}
                                                                    /{" "}
                                                                    {
                                                                        member
                                                                            .effective_stats
                                                                            .hp
                                                                    }
                                                                </b>
                                                            </span>
                                                            <span>
                                                                MP{" "}
                                                                <b>
                                                                    {
                                                                        member
                                                                            .resources
                                                                            .current_mp
                                                                    }{" "}
                                                                    /{" "}
                                                                    {
                                                                        member
                                                                            .effective_stats
                                                                            .mp
                                                                    }
                                                                </b>
                                                            </span>
                                                        </div>
                                                    </div>
                                                ),
                                            )
                                        ) : (
                                            <p className="muted">
                                                尚未招募冒险者
                                            </p>
                                        )}
                                    </div>
                                </>
                            ) : (
                                <p className="muted">等待首份游戏状态</p>
                            )}
                        </section>
                        <section className="detail-section">
                            <h3>最近行动</h3>
                            {watch.error && <p className="error-text">行动同步失败：{watch.error.message}</p>}
                            {recent.queryOnly && <p className="field-help">目前只进行了 {recent.queries.length} 次查询，尚无经营动作。</p>}
                            {recentEntries.length ? <ol className="recent-list">
                                {recentEntries.map(entry => <li key={entry.frame.cursor}>
                                    <span className="turn-label">{entry.frame.record.event.turn != null ? `T${entry.frame.record.event.turn}` : "提示"}</span>
                                    <div><strong>{entry.title}</strong><p className={entry.status === "failed" ? "error-text" : "muted"}>{entry.rolledBack ? "已回滚 · " : ""}{actionSummary(entry)}</p></div>
                                </li>)}
                            </ol> : <p className="muted">{watch.isPending ? "正在读取行动" : "尚无工具操作"}</p>}
                        </section>
                        <section className="detail-section">
                            <h3>用量与健康状态</h3>
                            <div className="metric-grid">
                                <Metric
                                    label="输入 Token（已报告）"
                                    value={fmt(job.usage.input_tokens)}
                                />
                                <Metric
                                    label="输出 Token（已报告）"
                                    value={fmt(job.usage.output_tokens)}
                                />
                                <Metric label="重试通知" value={job.retries} />
                                <Metric
                                    label="失败工具调用"
                                    value={job.tool_failures}
                                />
                            </div>
                            <p className="field-help">
                                提供用量字段的响应 {job.usage.reported} /{" "}
                                {job.usage.responses}
                                。缺失用量不按零处理。重试通知不包含 provider
                                内部的 HTTP 重试。
                            </p>
                        </section>
                    </>
                )}
                {tab !== "overview" && (
                    <>
                        {error && (
                            <p className="callout error" role="alert">
                                日志同步失败：{error}。正在重新连接。
                            </p>
                        )}
                        {tab !== "actions" && records.length >= 600 && (
                            <p className="field-help">
                                在线视图保留最近 600
                                个事件。完整历史请查看运行归档。
                            </p>
                        )}
                        {loading && (
                            <div
                                className="skeleton-lines"
                                aria-label="正在读取日志"
                            >
                                <i />
                                <i />
                                <i />
                            </div>
                        )}
                        {tab === "actions" && <>
                            {watch.error && <p className="error-text">行动同步失败：{watch.error.message}</p>}
                            <p className="field-help">当前回合的完整行动。查看其他回合可进入专注视图。</p>
                            {recent.queryOnly && <p className="field-help">目前只进行了 {recent.queries.length} 次查询，尚无经营动作。</p>}
                            <div className="action-timeline">
                                {groups.map(group => group.kind === "action" ? group.entries.map(renderAction) : <details className="action-group" key={group.entries[0].start}>
                                    <summary>{group.kind === "query" ? `连续查询 · ${group.entries.length} 次` : `运行事件 · ${group.entries.length} 条`}</summary>
                                    {group.entries.map(renderAction)}
                                </details>)}
                            </div>
                            {!watch.isPending && !entries.length && <Empty title="尚无行动记录">模型执行工具后，会在这里按顺序展示。</Empty>}
                        </>}
                        {tab === "models" &&
                            (models.length ? (
                                <div className="model-timeline">
                                    {models.map((entry) => (
                                        <article
                                            key={entry.key}
                                            className="model-entry"
                                        >
                                            <header>
                                                <strong>
                                                    回合 {entry.turn ?? "?"} /
                                                    调用 {entry.step ?? "?"}
                                                </strong>
                                                <span className="muted">
                                                    {entry.response
                                                        ? "已返回"
                                                        : "等待或接收中"}
                                                </span>
                                            </header>
                                            {entry.text ? (
                                                <pre className="model-text">
                                                    {entry.text}
                                                </pre>
                                            ) : (
                                                <p className="muted">
                                                    {entry.response
                                                        ? "本次没有模型正文，可能仅返回工具调用。"
                                                        : "尚未收到正文。"}
                                                </p>
                                            )}
                                            {entry.reasoning && (
                                                <details className="reasoning">
                                                    <summary>
                                                        API 提供的推理文本或摘要
                                                    </summary>
                                                    <pre>{entry.reasoning}</pre>
                                                </details>
                                            )}
                                            {entry.request != null && (
                                                <JsonDetails
                                                    title="查看完整输入"
                                                    value={entry.request}
                                                />
                                            )}
                                            {entry.response != null && (
                                                <JsonDetails
                                                    title="响应、工具调用与用量"
                                                    value={entry.response}
                                                />
                                            )}
                                        </article>
                                    ))}
                                </div>
                            ) : (
                                !loading && (
                                    <Empty title="尚无模型调用">
                                        只展示 API
                                        实际返回的内容，不推断模型思考过程。
                                    </Empty>
                                )
                            ))}
                        {tab === "diagnostics" && (
                            <>
                                <div className="diagnostic-summary">
                                    <p>
                                        最近活动 <b>{time(job.updated_at)}</b>
                                    </p>
                                    <p>
                                        最近成功存档{" "}
                                        <b>{time(job.checkpoint_at)}</b>
                                    </p>
                                    <p>
                                        事件游标{" "}
                                        <b className="mono">{job.cursor}</b>
                                    </p>
                                </div>
                                {diagnostics.length ? (
                                    diagnostics.map((r) => (
                                        <JsonDetails
                                            key={r.cursor}
                                            title={`${time(r.at)} ${r.event.type}`}
                                            value={r.event}
                                        />
                                    ))
                                ) : (
                                    <p className="muted">
                                        当前已读取的事件中没有错误或重试通知。
                                    </p>
                                )}
                            </>
                        )}
                        {tab === "config" && (
                            <>
                                <div className="artifact-links">
                                    {job.archive_id ? (
                                        <>
                                            <a
                                                href={`/replay/#run=${encodeURIComponent(job.archive_id)}`}
                                            >
                                                打开单局回放
                                                <ArrowSquareOut />
                                            </a>
                                            <a
                                                href={`/replay/profile.html?run=${encodeURIComponent(job.archive_id)}`}
                                            >
                                                查看经营画像
                                                <ArrowSquareOut />
                                            </a>
                                            <a
                                                href={`/api/llm/runs/${encodeURIComponent(job.archive_id)}/replay`}
                                                download={`${job.archive_id}.json`}
                                            >
                                                下载 replay.json
                                                <ArrowDown />
                                            </a>
                                        </>
                                    ) : (
                                        <p className="muted">
                                            存档创建后可查看和下载。
                                        </p>
                                    )}
                                </div>
                                <JsonDetails
                                    title="请求配置（不含凭证）"
                                    value={job.config}
                                />
                                <JsonDetails
                                    title="实际评测配置"
                                    value={job.effective_config}
                                />
                                <JsonDetails
                                    title="协议与数据指纹"
                                    value={job.data}
                                />
                                <p className="field-help">
                                    完整模型和行动历史保留在运行归档中。这里的在线日志视图最多保留最近
                                    600 个事件。
                                </p>
                            </>
                        )}
                    </>
                )}
            </div>
            <footer className="detail-footer">
                <Button
                    highContrast
                    variant="soft"
                    color="gray"
                    size="2"
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
                        size="2"
                        disabled={job.status === "stopping"}
                        onClick={() => onStop(job)}
                    >
                        <Stop />
                        {job.status === "queued" ? "取消排队" : "停止运行"}
                    </Button>
                )}
                {tab !== "overview" && (
                    <button
                        className={`follow-button ${follow ? "active" : ""}`}
                        aria-pressed={follow}
                        onClick={() => {
                            setFollow(!follow);
                            if (!follow && scroll.current)
                                scroll.current.scrollTop =
                                    scroll.current.scrollHeight;
                        }}
                    >
                        <ArrowDown />
                        {follow ? "跟随最新" : "跟随已暂停"}
                    </button>
                )}
            </footer>
        </section>
    );
}
