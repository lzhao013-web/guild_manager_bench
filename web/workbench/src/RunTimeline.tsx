import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { ArrowSquareOut, FlagCheckered, TreeStructure, X } from "@phosphor-icons/react";
import { useTrajectories } from "./TrajectoryContext";
import { fmt, TOOLS } from "./model";
import { ATTEMPT_STATUS } from "./watchModel";
import { TOOL_CALL_STATUS } from "./toolCalls";
import { roundTitle, settlement, timelineBands, timelineColumnCount, timelineRounds, timelineRowHeight, timelineTurns, turnSummary, upgradeDetails } from "./runTimelineModel";
import type { TimelineBlock, TimelineItem, TimelineRound, TimelineTurn } from "./runTimelineModel";
import type { TrajectoryRow } from "./trajectoryModel";
import "./run-timeline.css";

function changeText(value: number | null): string {
    if (value === null) return "未知";
    return `${value > 0 ? "+" : ""}${value.toLocaleString("zh-CN", { maximumFractionDigits: Math.abs(value) < 1 ? 4 : 1 })}`;
}

export function RunTimeline({ onInspect, active = true }: { onInspect: (row: TrajectoryRow) => void; active?: boolean }) {
    const history = useTrajectories()!;
    const rounds = useMemo(() => history.data ? timelineRounds(history.data) : [], [history.data]);
    const turns = useMemo(() => timelineTurns(rounds), [rounds]);
    const [columns, setColumns] = useState(2);
    const [rowHeight, setRowHeight] = useState(20);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [attemptId, setAttemptId] = useState<number | null>(null);
    const viewport = useRef<HTMLDivElement>(null);
    const heading = useRef<HTMLHeadingElement>(null);
    const buttons = useRef(new Map<string, HTMLButtonElement>());
    const selected = turns.find(turn => turn.id === selectedId);
    const selectedRound = selected?.attempts.find(round => round.id === attemptId) || selected?.primary;
    const selectedExperienceGained = selected && selectedRound ? turnSummary({ ...selected, primary: selectedRound }).experienceGained : null;
    const bands = timelineBands(turns, columns);
    const loaded = history.data !== undefined;
    const notes = [history.data?.warning, history.data && !history.data.attempts
        ? "服务尚未提供回合边界，按已记录工具分组。无操作回合和回合评分可能缺失。" : null].filter(Boolean);
    useLayoutEffect(() => {
        if (!active || !viewport.current) return;
        const node = viewport.current;
        const measure = () => {
            const { width, height } = node.getBoundingClientRect();
            if (width > 0 && height > 0) {
                const nextColumns = timelineColumnCount(Math.max(0, width - 12), Math.max(0, height - 8), turns.length);
                setColumns(nextColumns);
                setRowHeight(timelineRowHeight(Math.max(0, height - 8), turns.length, nextColumns));
            }
        };
        measure();
        const observer = new ResizeObserver(measure);
        observer.observe(node);
        return () => observer.disconnect();
    }, [active, loaded, turns.length]);
    useLayoutEffect(() => { if (selected) heading.current?.focus(); }, [selectedId]);
    function select(id: string) { setSelectedId(id); setAttemptId(null); }
    function close() {
        const id = selectedId;
        setSelectedId(null);
        requestAnimationFrame(() => { if (id) buttons.current.get(id)?.focus(); });
    }
    return <section className="run-timeline" aria-label="整局回合时间线">
        <header className="run-process-toolbar">
            <h3>整局进程</h3>
            {history.data && <span className={`run-process-coverage ${history.data.origin === "execution" ? "partial" : ""}`}>
                {history.data.origin === "execution" ? "仅当前执行阶段" : "整局记录"} / {turns.filter(turn => turn.turn !== null).length} 回合
            </span>}
            <span className="run-process-help">逐列阅读，点选回合看细节</span>
            {notes.length > 0 && <details className="run-process-notices"><summary>记录提示</summary><div>{notes.map((note, index) => <p key={index}>{note}</p>)}</div></details>}
            <label><span className="sr-only">选择时间线回合</span><select aria-label="选择时间线回合" value={selected?.id || ""} onChange={event => { if (event.target.value) select(event.target.value); }}>
                <option value="">选择回合…</option>
                {turns.map(turn => <option key={turn.id} value={turn.id}>{turn.turn === null ? "初始化 / 恢复" : `第 ${turn.turn} 回合`}</option>)}
            </select></label>
        </header>
        {history.pending && !loaded && <p className="run-process-message" role="status">正在读取整局记录…</p>}
        {history.error && <p className="run-process-message error-text" role="alert">读取失败：{history.error.message} <button onClick={history.refresh}>重试</button></p>}
        {history.locationError && <p className="run-process-message error-text" role="alert">无法定位：{history.locationError}</p>}
        <div className={`run-process-body ${selected ? "has-selection" : ""}`}>
            <div className="run-process-overview" ref={viewport} tabIndex={0} aria-label="整局回合总览">
                <div className="run-process-columns" style={{ gridTemplateColumns: `repeat(${Math.max(1, bands.length)}, minmax(0, 1fr))`, "--run-process-row-height": `${rowHeight}px` } as CSSProperties}>
                    {bands.map((band, index) => <section className="run-process-band" key={index} aria-label={`回合进程第${index + 1}列`}>
                        <header><span>回合</span><span>主要操作 / 全局升级</span><span>胜/负</span><span title="讨伐金币奖励，不是余额变化">金币</span><span title="本回合讨伐获得的经验，不扣除培养与天赋消耗">经验获得</span><span title="相邻有效回合的正式Rank差值">战力Δ</span></header>
                        <ol>{band.map(turn => <li key={turn.id}><ProcessRow turn={turn} selected={selectedId === turn.id} onSelect={() => select(turn.id)}
                            buttonRef={node => { if (node) buttons.current.set(turn.id, node); else buttons.current.delete(turn.id); }} /></li>)}</ol>
                    </section>)}
                </div>
                {loaded && !turns.length && <p className="timeline-empty">没有已记录的回合操作。日志缺失不表示没有发生操作。</p>}
            </div>
            {selected && selectedRound && <section className="run-process-detail" aria-label="所选回合操作" onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); close(); } }}>
                <header><h4 ref={heading} tabIndex={-1}>{roundTitle(selectedRound)}</h4><button onClick={close} aria-label="关闭回合详情"><X size={15} /></button></header>
                <div className="run-process-detail-meta"><span>{ATTEMPT_STATUS[selectedRound.status] || "回合未提交"}</span>
                    {selectedRound.score !== null && <span className="mono">Rank Score {fmt(selectedRound.score)}{selectedRound.status === "rolled_back" && "（已回滚）"}</span>}
                    {selected.attempts.length > 1 && <label>尝试<select aria-label="选择回合尝试" value={selectedRound.id} onChange={event => setAttemptId(Number(event.target.value))}>
                        {selected.attempts.map(round => <option key={round.id} value={round.id}>第{round.attempt}次 / {ATTEMPT_STATUS[round.status] || "未提交"}</option>)}
                    </select></label>}
                </div>
                <div className="run-process-detail-growth"><span>获得经验 <b>{selectedExperienceGained === null ? "未知" : fmt(selectedExperienceGained)}</b></span>
                    <span>正式战力Δ <b>{changeText(selectedRound.id === selected.primary.id ? selected.rankScoreDelta : null)}</b></span></div>
                <div className="run-process-detail-scroll">
                    <p className="run-process-detail-note">这里只展开所选回合，不改变观战位置。点击操作的“查看详情”才定位到原始结果帧。</p>
                    {selectedRound.status === "rolled_back" && <p className="timeline-attempt-note">此尝试已回滚，不再生效。</p>}
                    <RoundBlocks key={selectedRound.id} round={selectedRound} onInspect={onInspect} />
                </div>
            </section>}
        </div>
        <footer className="run-process-legend">金币、经验均为本回合讨伐获得量；战力Δ比较相邻有效回合。<span>查询次数与完整操作按需查看，缺失记录标为未知。</span></footer>
    </section>;
}
function ProcessRow({ turn, selected, onSelect, buttonRef }: { turn: TimelineTurn; selected: boolean; onSelect: () => void; buttonRef: (node: HTMLButtonElement | null) => void }) {
    const summary = turnSummary(turn);
    const title = turn.turn === null ? "初始化 / 恢复" : `第 ${turn.turn} 回合`;
    const attemptNote = turn.attempts.length > 1 ? `${turn.attempts.length}次尝试，主表展示最近一次未回滚尝试` : "";
    const description = [ATTEMPT_STATUS[turn.primary.status] || "回合未提交", attemptNote, summary.upgrades.length ? `全局升级：${summary.upgrades.join("、")}` : "", summary.operationText,
        `辅助调用${summary.auxiliary}次`, summary.failures ? `失败${summary.failures}次` : "", summary.endText,
        summary.battle?.gold != null ? `金币奖励${summary.battle.gold}` : "", `讨伐获得经验${summary.experienceGained === null ? "未完整记录" : summary.experienceGained}`,
        `正式战力变化${changeText(turn.rankScoreDelta)}`, turn.primary.score !== null ? `Rank Score ${fmt(turn.primary.score)}` : ""].filter(Boolean).join("，");
    return <button className={`run-process-row is-${turn.primary.status} ${summary.upgrades.length ? "has-upgrade" : ""}`} ref={buttonRef}
        aria-label={`查看${title}概览`} aria-description={description} aria-pressed={selected} title={description} onClick={onSelect}>
        <span className="run-process-turn mono">{turn.turn === null ? "初始" : `T${turn.turn}`}{turn.attempts.length > 1 && <sup title={attemptNote}>↻{turn.attempts.length}</sup>}{summary.failures > 0 && <sup className="run-process-failed" title={`${summary.failures}次失败调用`}>!</sup>}</span>
        <span className="run-process-operations">
            {summary.upgrades.length > 0 ? <strong title={`${summary.upgrades.join("、")}。其他操作：${summary.operationText || "无"}`}>{summary.upgrades.join("、")}</strong>
                : <span>{summary.operationText || summary.emptyText}</span>}
        </span>
        <span className="run-process-battle" title={summary.endText}>{turn.primary.status === "rolled_back" ? "回滚"
            : summary.battle && !(summary.battle.unknown > 0 && summary.battle.unknown === summary.battle.battles.length)
                ? <>{summary.battle.wins}<span className="run-process-slash">/</span><span className={summary.battle.losses > 0 ? "run-process-failed" : ""}>{summary.battle.losses}</span>{summary.battle.unknown > 0 && <sup>?</sup>}</>
                : summary.ended ? "未知" : summary.endText === "结束失败" ? "失败" : "未结算"}</span>
        <span className="run-process-number run-process-gold" title={turn.primary.status === "rolled_back" ? "已回滚，不显示有效收益" : "本回合讨伐金币奖励"}>{turn.primary.status !== "rolled_back" && summary.battle?.gold != null ? fmt(summary.battle.gold) : "未知"}</span>
        <span className="run-process-number run-process-experience"
            title={`本回合讨伐获得经验 ${summary.experienceGained === null ? "未完整记录" : fmt(summary.experienceGained)}，不扣除培养、天赋等支出。`}>{summary.experienceGained === null ? "未知" : fmt(summary.experienceGained)}</span>
        <span className={`run-process-number run-process-rank ${turn.rankScoreDelta !== null && turn.rankScoreDelta < 0 ? "is-negative" : ""}`}
            title={turn.rankScoreDelta === null ? "相邻已完成回合的正式Rank记录不完整，不能计算战力变化。" : `正式Rank ${turn.previousRankScore} → ${turn.primary.score}，变化 ${changeText(turn.rankScoreDelta)}`}>{changeText(turn.rankScoreDelta)}</span>
    </button>;
}
function RoundBlocks({ round, onInspect }: { round: TimelineRound; onInspect: (row: TrajectoryRow) => void }) {
    return <div className="timeline-round-body">
        {round.status === "uncommitted" || round.status === "running" || round.status === "failed" || round.status === "interrupted"
            ? <p className="timeline-attempt-note">此回合尚未确认提交。成功操作仅描述当时的执行结果。</p> : null}
        {round.blocks.map(block => block.kind === "auxiliary" ? <AuxiliaryBlock key={block.id} block={block} onInspect={onInspect} />
            : <TimelineAction key={block.item.id} item={block.item} onInspect={onInspect} />)}
        {!round.items.length && <p className="timeline-empty">本次尝试未记录工具操作，不能推断具体行动。</p>}
    </div>;
}
function AuxiliaryBlock({ block, onInspect }: { block: Extract<TimelineBlock, { kind: "auxiliary" }>; onInspect: (row: TrajectoryRow) => void }) {
    const [open, setOpen] = useState(false);
    const history = useTrajectories()!;
    const tools = new Map<string, number>();
    for (const item of block.items) {
        const name = TOOLS[item.entry.event.name] || item.entry.event.name;
        tools.set(name, (tools.get(name) || 0) + 1);
    }
    return <details className="timeline-auxiliary" onToggle={event => setOpen(event.currentTarget.open)}>
        <summary><strong>查询 / 预览等辅助调用 · {block.items.length} 次</strong><span>{[...tools].map(([name, count]) => `${name} ×${count}`).join("、")}</span></summary>
        {open && <div>{block.items.map(item => <button key={item.id} disabled={history.locating} onClick={() => onInspect(item.row)}>
            <span>{item.entry.event.result?.already_executed ? "重复提交，未重复执行" : item.entry.title}</span>
            <small>{TOOL_CALL_STATUS[item.entry.status]}</small><ArrowSquareOut size={12} />
        </button>)}</div>}
    </details>;
}
function TimelineAction({ item, onInspect }: { item: TimelineItem; onInspect: (row: TrajectoryRow) => void }) {
    const history = useTrajectories()!;
    const entry = item.entry;
    const battle = item.emphasis === "settlement" ? settlement(item) : null;
    const upgrades = item.emphasis === "upgrade" ? upgradeDetails(item) : [];
    const Icon = item.emphasis === "upgrade" ? TreeStructure : FlagCheckered;
    return <article className={`timeline-action is-${item.emphasis} is-${entry.status}`} aria-label={entry.title}>
        <header><div className="timeline-action-title">{item.emphasis !== "normal" && <Icon size={17} />}<strong>{entry.title}</strong>
            <span className={`action-status is-${entry.status}`}>{TOOL_CALL_STATUS[entry.status]}</span></div>
            <button className="timeline-inspect" disabled={history.locating} onClick={() => onInspect(item.row)}
                aria-label={`查看详情 ${entry.title}`}>{item.planStep ? "查看所属方案" : "查看详情"}<ArrowSquareOut size={12} /></button>
        </header>
        {item.planStep && <p className="timeline-plan-step">方案第 {item.planStep.index} / {item.planStep.total} 步 · 共用一次原子提交</p>}
        {item.emphasis === "upgrade" && <p className="timeline-emphasis-label">全局升级{entry.status === "succeeded" ? " · 解锁记录" : " · 尚未确认解锁"}</p>}
        {upgrades.length > 0 && <div className="timeline-upgrade-effects">{upgrades.map((text, index) => <p key={index}>{text}</p>)}</div>}
        {item.emphasis === "upgrade" && entry.status === "succeeded" && !upgrades.length && <p className="timeline-missing">升级效果未记录，不能从当前配置补推历史效果。</p>}
        {battle ? <>
            <div className="timeline-settlement-metrics"><span><b>{battle.battles.length}</b> 场讨伐</span><span><b>{battle.wins}</b> 胜 / <b>{battle.losses}</b> 负{battle.unknown > 0 && ` / ${battle.unknown} 场其它或未知结果`}</span>
                <span>奖励金币 <b>{battle.gold === null ? "未完整记录" : `+${fmt(battle.gold)}`}</b></span>
                <span>奖励经验 <b>{battle.experience === null ? "未完整记录" : `+${fmt(battle.experience)}`}</b></span></div>
            {battle.battles.length > 0 ? <ul className="timeline-battles">{battle.battles.map((record, index) => <li key={index}><span>{record.name}</span><b className={record.result === "失败" ? "lost" : ""}>{record.result}</b></li>)}</ul>
                : <p className="timeline-missing">本回合未安排讨伐。</p>}
            {battle.materials?.length ? <p className="timeline-materials">奖励材料 · {battle.materials.join(" · ")}</p> : null}
            {battle.materials === null && <p className="timeline-missing">材料奖励未完整记录。</p>}
        </> : entry.summary && entry.summary !== entry.title ? <p className="timeline-action-summary">{entry.summary}</p> : null}
        {item.emphasis === "settlement" && entry.status === "succeeded" && !battle && <p className="timeline-missing">战果与奖励未记录，不按零场或零收益处理。</p>}
        {entry.effects.length > 0 && <EffectList effects={entry.effects} settlement={item.emphasis === "settlement"} />}
    </article>;
}
function EffectList({ effects, settlement: ended }: { effects: string[]; settlement: boolean }) {
    const [open, setOpen] = useState(false);
    return <div className="timeline-effects"><span>{ended ? "结算变化" : "记录变化"}</span><ul>{effects.slice(0, 3).map((text, index) => <li key={index}>{text}</li>)}</ul>
        {effects.length > 3 && <details onToggle={event => setOpen(event.currentTarget.open)}><summary>其余 {effects.length - 3} 项变化</summary>{open && <ul>{effects.slice(3).map((text, index) => <li key={index}>{text}</li>)}</ul>}</details>}
    </div>;
}
