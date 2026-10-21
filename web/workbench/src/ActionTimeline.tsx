import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { RunTrajectories } from "./RunTrajectories";
import { useTrajectories } from "./TrajectoryContext";
import type { ReactNode } from "react";
import { ACTION_STATUS, actionEntries, actionGroups, actionSummary, describeAction, recentActions } from "./actionModel";
import { ActionResults } from "./ActionResultPanel";
import { inputPreview } from "./actionInputs";
import type { ActionEntry } from "./actionModel";
import { time } from "./model";
import { ToolCallFilters, ToolCallSummary } from "./ToolCallSummary";
import { matchesTool, summarizeToolCalls, toolCategoryLabel } from "./toolCalls";
import type { ToolCallStatus } from "./toolCalls";
import { ArrowSquareOut } from "@phosphor-icons/react";
import type { WatchFrame } from "./watchModel";
import "./actions.css";

export function ActionTimeline({ frames, frame, active, following, rolledBack, onFrame, renderDetails, onWholeRun, onModelReasoning }: {
    frames: WatchFrame[];
    frame: WatchFrame | undefined;
    active: boolean;
    following: boolean;
    rolledBack: boolean;
    onFrame: (frame: WatchFrame) => void;
    renderDetails: (frame: WatchFrame) => ReactNode;
    onWholeRun?: () => void;
    onModelReasoning?: (entry: ActionEntry) => void;
}) {
    const history = useTrajectories();
    const [wholeRun, setWholeRun] = useState(false);
    const entries = useMemo(() => actionEntries(frames), [frames]);
    const [toolType, setToolType] = useState("all");
    const [toolStatus, setToolStatus] = useState<"all" | ToolCallStatus>("all");
    const tools = useMemo(() => summarizeToolCalls(entries.map(entry => entry.event)), [entries]);
    const filtered = useMemo(() => entries.filter(entry => entry.kind === "system"
        ? toolType === "all" && toolStatus === "all"
        : matchesTool(entry.event, toolType) && (toolStatus === "all" || entry.status === toolStatus)), [entries, toolType, toolStatus]);
    const groups = useMemo(() => actionGroups(filtered), [filtered]);
    const recent = useMemo(() => recentActions(frames.filter(item => item.cursor <= (frame?.cursor ?? 0))), [frames, frame?.cursor]);
    const showingRecent = following && frame && !frame.failed && !["tool_call", "tool_result"].includes(frame.record.event.type);
    const detailFrame = showingRecent ? recent.actions[0]?.frame || recent.queries.at(-1)?.frame || frame : frame;
    const selected = detailFrame && (recent.entries.find(entry => entry.frame.cursor === detailFrame.cursor) || describeAction(detailFrame));
    const actions = entries.filter(entry => entry.kind === "action");
    const queries = entries.filter(entry => entry.kind === "query");
    const viewport = useRef<HTMLDivElement>(null);
    const scroll = useRef(0);
    const previousFrame = useRef<number | undefined>(undefined);
    useLayoutEffect(() => {
        if (!active || !viewport.current) return;
        const node = viewport.current;
        const selected = node.querySelector<HTMLElement>(".spectator-step.selected");
        if (!following && selected && previousFrame.current !== frame?.cursor) {
            const group = selected.closest("details");
            if (group) group.open = true;
            node.scrollTop += selected.getBoundingClientRect().top - node.getBoundingClientRect().top - 8;
        } else node.scrollTop = following ? node.scrollHeight : scroll.current;
        previousFrame.current = frame?.cursor;
    }, [active, frame?.cursor, filtered, following, wholeRun]);
    function row(entry: ActionEntry) {
        const isSelected = entry.frame.cursor === frame?.cursor || entry.start === frame?.cursor;
        return <button key={entry.frame.cursor} aria-label={`定位行动 ${entry.title}`} aria-pressed={isSelected}
            className={`spectator-step action-entry ${isSelected ? "selected" : ""} ${entry.status === "failed" ? "failed" : ""}`}
            onClick={() => onFrame(entry.frame)}>
            <span className="action-entry-time mono">{time(entry.frame.record.at)}</span>
            <span><strong>{entry.title}</strong>
                <small>{actionSummary(entry)}</small>
                {entry.kind !== "system" && <small className="action-input-preview">输入 · {inputPreview(entry.input)}</small>}
                <span className="action-entry-meta">{entry.kind !== "system" && <span>{toolCategoryLabel(entry.event)}</span>}<span className={`action-status is-${entry.status}`}>{entry.kind === "system" && entry.status === "recorded" ? "运行提示" : ACTION_STATUS[entry.status]}</span>
                    {(entry.rolledBack || rolledBack && entry.kind !== "system") && <span className="action-rollback">{entry.kind === "query" ? "所属尝试已回滚" : "已回滚，不再生效"}</span>}</span>
            </span>
        </button>;
    }
    const mode = history && <nav className="trajectory-mode" aria-label="行动查看范围">
        <button aria-pressed={!wholeRun} onClick={() => setWholeRun(false)}>所选回合</button>
        <button aria-pressed={wholeRun} onClick={() => { setWholeRun(true); onWholeRun?.(); }}>整局检索</button>
    </nav>;
    const detail = <section className="action-detail" aria-label="所选行动详情">
        {selected ? <>
            <header><span className="subtle-label">{selected.kind === "query" ? "查询记录，不代表修改局面" : selected.kind === "system" ? "运行事件" : "实际操作"}</span>
                <h3>{selected.title}</h3><div className="action-detail-meta"><span className={`action-status is-${selected.status}`}>{ACTION_STATUS[selected.status]}</span><time className="mono">{time(selected.frame.record.at)}</time>
                    {selected.kind !== "system" && onModelReasoning && <button className="action-model-link"
                        disabled={selected.frame.call === null}
                        title={selected.frame.call === null ? "该日志没有保留所属模型调用，不能跳到其它响应代替" : "定位所属模型调用并打开 API 推理文本"}
                        onClick={() => onModelReasoning(selected)}>
                        <ArrowSquareOut size={13} />{selected.frame.call === null ? "未记录模型关联" : "查看对应模型推理"}
                    </button>}
                </div>
            </header>
            {showingRecent && detailFrame !== frame && <p className="muted">最近操作详情。观战仍跟随最新局面。</p>}
            {rolledBack && selected.kind === "action" && <p className="action-detail-warning">本次尝试已回滚。以下是当时的执行结果，不代表当前生效局面。</p>}
            <ActionResults entry={selected} />
            {renderDetails(selected.frame)}
        </> : <p className="scene-empty">选择一条行动查看对象、结果和局面变化</p>}
    </section>;
    if (wholeRun) return <div className="action-whole-workspace">{mode}<RunTrajectories detail={detail} /></div>;
    return <div className="action-workspace">
        <section className="spectator-timeline" aria-label="观战行动时间线">
            <div className="spectator-column-title"><h3>行动时间线</h3><span>{actions.length} 次操作 · {queries.length} 次查询</span></div>
            {mode}
            <ToolCallFilters tools={tools} type={toolType} status={toolStatus} onType={setToolType} onStatus={setToolStatus} scope="本回合" />
            {!actions.length && <p className="action-query-only">{queries.length ? `目前只进行了 ${queries.length} 次查询，尚无经营动作` : "尚无经营动作记录"}</p>}
            <div className="spectator-timeline-scroll" ref={viewport} onScroll={event => { if (active) scroll.current = event.currentTarget.scrollTop; }}>
                <ToolCallSummary tools={tools} selected={toolType} label="本回合工具汇总" onTool={name => { setToolType(name); setToolStatus("all"); }} />
                {!filtered.length && <p className="action-query-only">没有匹配的调用记录。</p>}
                {groups.map(group => group.kind !== "system" ? group.entries.map(row) : <details className={`action-group is-${group.kind}`} key={group.entries[0].start}>
                    <summary><strong>运行事件 · {group.entries.length} 条</strong>
                        <span>{[...new Set(group.entries.map(entry => entry.title))].join("、")}</span>
                        {group.entries.some(entry => entry.status === "failed") && <b>包含失败</b>}</summary>
                    {group.entries.map(row)}
                </details>)}
            </div>
        </section>
        {detail}
    </div>;
}
