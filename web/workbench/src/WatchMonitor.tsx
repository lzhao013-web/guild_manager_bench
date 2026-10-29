import { useMemo } from "react";
import {
    ArrowsClockwise, ArrowsOutSimple, Broadcast, CaretLeft, CaretRight,
    ChartLineUp, Coins, Pause, PushPin, Sparkle, Stop, UsersThree, X,
} from "@phosphor-icons/react";
import { duration, fmt, isActive, modelEntries, PHASE, short } from "./model";
import { Portrait, Vital } from "./SceneParts";
import { MonsterBadges } from "./MonsterBadges";
import { AdventurerStatus, adventurerFrame, adventurerStateText, isLowHealth, monsterFrame } from "./UnitFrame";
import { ModelCallStats } from "./ModelCallStats";
import { MaterialExchangeStatus } from "./MaterialExchangeStatus";
import { monsterClassification } from "./monsterModel";
import { StatusBadge } from "./ui";
import { RunId } from "./RunId";
import { ChangeValue } from "./motion";
import { MonitorPreviews } from "./MonitorPreviews";
import { ATTEMPT_STATUS, attemptLabel, livePosition, watchPresentation } from "./watchModel";
import type { WatchFrame, WatchPage, WatchPosition, WatchRecord } from "./watchModel";
import type { Job } from "./types";
import type { WatchFocus } from "./sceneNavigation";

/** 多路只呈现有界摘要，完整模块在单局工作区中查看。 */
export function WatchMonitor({ job, runId, page, live, frame, frames, records, position, hasNew,
    loading, failure, onRetry, onPosition, onFrame, onPause, onStop, onFocus, onPin,
    onClose, pinned,
}: {
    job: Job;
    runId: string;
    page: WatchPage | undefined;
    live: WatchPage | undefined;
    frame: WatchFrame | undefined;
    frames: WatchFrame[];
    records: WatchRecord[];
    position: WatchPosition;
    hasNew: boolean;
    loading: boolean;
    failure: Error | null;
    onRetry: () => void;
    onPosition: (position: WatchPosition) => void;
    onFrame: (frame: WatchFrame) => void;
    onPause: () => void;
    onStop: (job: Job) => void;
    onFocus?: (target?: WatchFocus) => void;
    onPin?: () => void;
    onClose?: () => void;
    pinned?: boolean;
}) {
    const following = position.attempt === null;
    const presentation = watchPresentation(job, following);
    const selected = page?.attempts.find(attempt => attempt.id === page.selected);
    const observation = frame?.observation;
    const members = observation && [...observation.adventurers].sort((a, b) =>
        Number(isLowHealth(b)) - Number(isLowHealth(a))).slice(0, 6);
    const monsters = observation?.monsters;
    const model = useMemo(() => modelEntries(records).at(-1), [records]);
    const index = frame ? frames.indexOf(frame) : -1;
    const warning = failure ? `更新失败，暂时显示上次的局面：${failure.message}` :
        job.checkpoint_error ? `存档失败：${job.checkpoint_error}` :
        job.error || (selected?.status === "rolled_back" ? "这次尝试已撤销，正在回看当时的局面" : "");
    function inspect(target?: WatchFocus) { onFocus?.(target); }
    const text = model?.text || model?.reasoning || "";
    const body = text ? `${text.length > 600 ? "…" : ""}${text.slice(-600)}` :
        model ? (model.response ? "响应未包含正文，查看工具调用" : "查看当前模型调用") : "尚无模型调用";
    return (
        <section className="run-detail spectator spectator-compact watch-monitor" aria-label={`${job.name} 的运行详情`}>
            <header className="monitor-header">
                <div className="monitor-identity">
                    <h2 title={`${job.name}\n${job.config.provider} / ${job.config.model}\n${job.config.preset} / ${job.batch_name}`}>{job.name}</h2>
                    <RunId id={runId} />
                    <div className="monitor-stage">
                        <StatusBadge status={job.status} turn={job.turn} maxTurns={job.max_turns} />
                        {warning ? <span className={`monitor-warning ${failure || job.checkpoint_error || job.status === "failed" ? "is-error" : ""}`} title={warning} role={failure || job.checkpoint_error || job.status === "failed" ? "alert" : "status"}>{warning}</span> :
                            <span className="monitor-phase" title={`${PHASE[job.phase] || job.phase}${isActive(job) ? ` · ${duration(job.phase_started_at)}` : ""}`}>{PHASE[job.phase] || job.phase}{isActive(job) && ` · ${duration(job.phase_started_at)}`}</span>}
                        <span className="monitor-budget" title="实时工具预算，不随历史位置回退">工具 {job.budget ? `${job.budget.used}/${job.budget.max_tool_calls}` : "未记录"}</span>
                        {failure && <button aria-label="重新读取" title="重新读取观战" onClick={onRetry}><ArrowsClockwise size={13} /></button>}
                    </div>
                </div>
                <div className="monitor-buttons">
                    {onPin && <button aria-label={pinned ? "取消关注此 run" : "关注此 run"} title={pinned ? "取消关注" : "关注"} aria-pressed={pinned} onClick={onPin}><PushPin size={14} weight={pinned ? "fill" : "regular"} /></button>}
                    {isActive(job) && <button className="monitor-stop" aria-label={job.status === "queued" ? "取消排队" : job.status === "stopping" ? "停止中" : "停止运行"} title="停止运行" disabled={job.status === "stopping"} onClick={() => onStop(job)}><Stop size={14} /></button>}
                    {onFocus && <button className="monitor-focus" aria-label="专注查看" title="专注查看完整局面" onClick={() => inspect()}><ArrowsOutSimple size={15} /><span>专注</span></button>}
                    {onClose && <button aria-label="关闭详情" title="移出观战" onClick={onClose}><X size={15} /></button>}
                </div>
            </header>
            <div className="monitor-metrics" aria-label="当前观看位置的资源">
                <div className={`spectator-viewpoint ${presentation.realtime ? "is-live" : following ? "is-final" : "is-history"}`} title={selected ? attemptLabel(selected) : "等待初始化"}>
                    {presentation.realtime ? <Broadcast size={12} /> : <Pause size={12} />}<strong>{presentation.label}</strong>
                </div>
                <span className="monitor-score" title={`已记录评分 ${fmt(frame?.score)}`}><ChartLineUp size={12} /><ChangeValue value={frame?.score ?? "unknown"}>{fmt(frame?.score)}</ChangeValue></span>
                <span title={`金币 ${fmt(observation?.gold)}`}><Coins size={12} /><b><ChangeValue value={observation?.gold ?? "unknown"}>{observation ? short(observation.gold) : "未知"}</ChangeValue></b></span>
                <span title={`经验池 ${fmt(observation?.experience_pool)}`}><Sparkle size={12} /><b><ChangeValue value={observation?.experience_pool ?? "unknown"}>{observation ? short(observation.experience_pool) : "未知"}</ChangeValue></b></span>
                <button title="查看完整编队" aria-label="展开编队" disabled={!onFocus} onClick={() => inspect({ module: "party" })}><UsersThree size={12} /><ChangeValue value={observation ? `${observation.adventurers.length}/${observation.party_size_limit}` : "unknown"}>{observation ? `${observation.adventurers.length}/${observation.party_size_limit}` : "未知"}</ChangeValue>{observation && observation.adventurers.length > 6 ? "（显示6位）" : ""}</button>
                {observation?.material_exchange && <MaterialExchangeStatus turn={observation.turn} exchange={observation.material_exchange} />}
            </div>
            {loading ? <div className="monitor-empty" aria-label="正在读取观战历史">正在读取局面…</div> : !page?.records.length ? (
                <div className="monitor-empty">{job.status === "queued" ? "等待任务开始" : "尚无观战记录"}</div>
            ) : <div className="monitor-body">
                <section className="monitor-party" aria-label="编队摘要">
                    {members?.length ? members.map(member => <button key={member.adventurer_id}
                        className={`monitor-member actor-frame ${isLowHealth(member) ? "is-low-health" : ""}`}
                        {...adventurerFrame(member)}
                        aria-label={`查看成员 ${member.name}`} aria-description={adventurerStateText(member)} title={`${member.name} · Lv.${member.level}\n生命 ${fmt(member.resources.current_hp)} / ${fmt(member.effective_stats.hp)}\n法力 ${fmt(member.resources.current_mp)} / ${fmt(member.effective_stats.mp)}`} disabled={!onFocus}
                        onClick={() => inspect({ module: "party", selection: { kind: "member", id: member.adventurer_id } })}>
                        <Portrait id={member.template_id} name={member.name} />
                        <span className="monitor-member-body"><span className="monitor-member-name"><AdventurerStatus member={member} compact /><strong title={member.name}>{member.name}</strong><small>Lv.{member.level}</small></span>
                            <Vital label="生命" value={member.resources.current_hp} max={member.effective_stats.hp} />
                            {member.effective_stats.mp > 0 && <Vital label="法力" value={member.resources.current_mp} max={member.effective_stats.mp} mana />}
                        </span>
                    </button>) : <p>{observation ? "此时尚未招募冒险者" : "该位置没有编队快照"}</p>}
                </section>
                <section className="monitor-monsters" aria-label="怪物摘要">
                    <button className="monitor-section-link" disabled={!onFocus} onClick={() => inspect({ module: "monsters" })}>怪物{monsters ? ` ${monsters.length}` : "未记录"}</button>
                    {monsters?.slice(0, 4).map(monster => <button key={monster.monster_id as string} className="actor-frame" {...monsterFrame(monster)} title={`${monster.name} · ${monsterClassification(monster)}`} aria-label={`查看怪物 ${monster.name}`} aria-description={monsterClassification(monster)} disabled={!onFocus}
                        onClick={() => inspect({ module: "monsters", selection: { kind: "monster", id: monster.monster_id as string } })}>
                        <Portrait monster id={monster.archetype_id as string} name={monster.name!} />
                        <span className="monitor-monster-info"><strong>{monster.name}</strong><MonsterBadges monster={monster} compact /></span>
                    </button>)}
                    {monsters && monsters.length > 4 && <button className="monitor-more" aria-label={`查看全部 ${monsters.length} 个怪物`} disabled={!onFocus} onClick={() => inspect({ module: "monsters" })}>+{monsters.length - 4}</button>}
                    {monsters?.length === 0 && <span className="muted">此时没有怪物记录</span>}
                </section>
                <MonitorPreviews observation={observation || null} frame={frame} frames={frames} onFrame={onFrame} onFocus={onFocus} live={following && isActive(job)} />
                <button className={`monitor-model ${text ? "has-output" : "is-pending"}`} aria-label="查看完整模型输出" title={body} disabled={!onFocus} onClick={() => inspect({ module: "models" })}>
                    <span className="monitor-row-label">{!model?.text && model?.reasoning ? "推理" : "模型"}</span>
                    <span className="monitor-model-content">
                        {model && <ModelCallStats model={model} live={following && isActive(job)} compact />}
                        {(text || !model) && <span className="monitor-model-excerpt">{body}</span>}
                    </span><ArrowsOutSimple size={12} />
                </button>
            </div>}
            <footer className="monitor-controls">
                <select aria-label="选择观战回合" title={selected ? `${attemptLabel(selected)} / ${ATTEMPT_STATUS[selected.status]}` : "尚无回合"}
                    value={position.attempt ?? live?.selected ?? ""} disabled={!live?.attempts.length}
                    onChange={event => onPosition({ attempt: Number(event.target.value), cursor: null })}>
                    {!live?.attempts.length && <option value="">尚无回合</option>}
                    {live?.attempts.map(attempt => <option key={attempt.id} value={attempt.id}>{attemptLabel(attempt)} / {ATTEMPT_STATUS[attempt.status]}</option>)}
                </select>
                <div className="monitor-step-controls">
                    <button aria-label="上一步" title="上一步" disabled={index <= 0} onClick={() => onFrame(frames[index - 1])}><CaretLeft size={12} /></button>
                    <input type="range" aria-label="选择观战步骤" title={`步骤 ${index + 1} / ${frames.length}`} min={0} max={Math.max(0, frames.length - 1)}
                        value={Math.max(0, index)} disabled={!frames.length} onChange={event => onFrame(frames[Number(event.target.value)])} />
                    <button aria-label="下一步" title="下一步" disabled={index < 0 || index >= frames.length - 1} onClick={() => onFrame(frames[index + 1])}><CaretRight size={12} /></button>
                </div>
                {(isActive(job) || !following) ? <button className={`monitor-follow ${presentation.realtime ? "is-following" : ""}`} aria-label={following ? "暂停跟随" : `${presentation.returnLabel}${isActive(job) && hasNew ? "（有新事件）" : ""}`}
                    disabled={following && !page?.selected} onClick={following ? onPause : () => onPosition(livePosition)}>
                    {following ? <Pause size={12} /> : <Broadcast size={12} />}{following ? "暂停跟随" : presentation.returnLabel}{isActive(job) && hasNew && <i title="有新事件" />}
                </button> : <span className="muted">运行已结束</span>}
            </footer>
        </section>
    );
}
