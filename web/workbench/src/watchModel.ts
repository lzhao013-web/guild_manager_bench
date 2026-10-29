import { isActive, TOOLS } from "./model";
import type { EventRecord, Job, Observation } from "./types";
import { withMonsterStages } from "./monsterModel";
import type { MonsterStages } from "./monsterModel";

export interface RankContributions {
    turn: number;
    scores: Record<string, number>;
}
export interface WatchAttempt {
    id: number;
    turn: number | null;
    attempt: number;
    status: string;
    score: number | null;
    rank_contributions?: RankContributions | null;
    actions: number;
    at: string;
    updated_at: string;
}
export type WatchRecord = EventRecord & { after: number };
export interface WatchPage {
    cursor: number;
    attempts: WatchAttempt[];
    selected: number | null;
    records: WatchRecord[];
    origin?: "archive" | "execution";
    live_cursor?: number;
    monster_stages?: MonsterStages;
}
export interface WatchPosition {
    attempt: number | null;
    cursor: number | null;
    frozen?: WatchPage;
}
export const livePosition: WatchPosition = { attempt: null, cursor: null };
export function watchPresentation(job: Job, following: boolean) {
    const active = isActive(job);
    const finalLabel = job.status === "completed" ? "最终局面" : "最后记录局面";
    return {
        realtime: active && following,
        label: following ? active ? "实时局面" : finalLabel : "历史局面",
        returnLabel: active ? "返回实时" : `返回${finalLabel}`,
    };
}
export interface WatchFrame {
    cursor: number;
    record: WatchRecord;
    observation: Observation | null;
    before: Observation | null;
    score: number | null;
    title: string;
    summary: string;
    failed: boolean;
    call: number | null;
    names?: Record<string, string>;
    rank_contributions?: RankContributions | null;
}
export const ATTEMPT_STATUS: Record<string, string> = {
    running: "进行中", scoring: "评分中", completed: "已完成",
    failed: "失败", rolled_back: "已回滚", interrupted: "已中断",
};
const TITLES: Record<string, string> = {
    run_started: "初始局面", run_resumed: "恢复检查点", turn_started: "回合开始",
    model_request: "请求模型", model_response: "模型已返回", tool_call: "执行工具",
    turn_completed: "行动结束，等待评分", turn_scored: "评分已记录", scoring_started: "正在评分",
    retry: "模型重试", turn_retry: "本次尝试已回滚", turn_failed: "本次尝试失败",
    checkpoint_failed: "检查点保存失败", worker_failed: "工作进程失败", worker_stopped: "任务已停止",
    run_completed: "运行已完成", run_failed: "运行失败",
};
export function isWatchEvent(event: EventRecord["event"]): boolean {
    return event.type in TITLES || event.type === "tool_result";
}
export function watchFrames(records: WatchRecord[], initialScore: number | null = null, stages: MonsterStages = {}, initialContributions: RankContributions | null = null): WatchFrame[] {
    const frames: WatchFrame[] = [];
    let observation: Observation | null = null;
    let score: number | null = initialScore;
    let contributions = initialContributions;
    let call: number | null = null;
    for (const record of records) {
        const e = record.event;
        const before = observation;
        if (e.type === "run_started" || e.type === "turn_started") observation = withMonsterStages(e.observation, stages);
        if (e.type === "run_resumed") {
            observation = withMonsterStages(e.restored_observation, stages);
            score = e.last_rank_score ?? null;
            contributions = e.last_adventurer_rank_scores ?? null;
        }
        if (e.type === "tool_result" && e.result?._observation_after) observation = withMonsterStages(e.result._observation_after, stages);
        if (e.type === "turn_scored") {
            score = e.rank_score;
            contributions = e.adventurer_rank_scores == null ? null : { turn: e.turn, scores: e.adventurer_rank_scores };
        }
        if ((e.type === "run_completed" || e.type === "run_failed") && e.score?.rank_score != null) score = e.score.rank_score;
        if (e.type === "model_request") call = record.cursor;
        if (!isWatchEvent(e)) continue;
        const title = e.type === "tool_call" ? `执行：${TOOLS[e.name] || e.name}`
            : e.type === "tool_result" ? (TOOLS[e.name] || e.name) : TITLES[e.type];
        const summary = e.result?.error || e.result?.event?.summary || e.reason || e.error || e.failure_reason ||
            (e.type === "tool_result" ? String(e.content || "").split("\n")[0].slice(0, 240) : "");
        frames.push({ cursor: record.cursor, record, observation, before, score, title, summary,
            rank_contributions: contributions,
            failed: e.result?.ok === false || ["turn_failed", "run_failed", "worker_failed", "checkpoint_failed"].includes(e.type), call });
    }
    return frames;
}

export function stateChanges(before: Observation | null, after: Observation | null): string[] {
    if (!before || !after || before === after) return [];
    const changes: string[] = [];
    const add = (label: string, from: number, to: number) => {
        if (from !== to) changes.push(`${label} ${to - from > 0 ? "+" : ""}${to - from}`);
    };
    add("金币", before.gold, after.gold);
    add("经验池", before.experience_pool, after.experience_pool);
    if (before.material_exchange && after.material_exchange)
        add("兑换剩余额度", before.material_exchange.remaining_points, after.material_exchange.remaining_points);
    for (const material of new Set([...Object.keys(before.materials), ...Object.keys(after.materials)]))
        add(material, before.materials[material] || 0, after.materials[material] || 0);
    for (const member of after.adventurers) {
        const previous = before.adventurers.find(m => m.adventurer_id === member.adventurer_id);
        if (!previous) changes.push(`招募 ${member.name}`);
        else {
            add(`${member.name} HP`, previous.resources.current_hp, member.resources.current_hp);
            add(`${member.name} MP`, previous.resources.current_mp, member.resources.current_mp);
            if (member.level !== previous.level) changes.push(`${member.name} Lv.${previous.level} → ${member.level}`);
            if (JSON.stringify(member.equipment_slots) !== JSON.stringify(previous.equipment_slots)) changes.push(`${member.name} 装备变化`);
        }
    }
    for (const member of before.adventurers)
        if (!after.adventurers.some(m => m.adventurer_id === member.adventurer_id)) changes.push(`离队 ${member.name}`);
    return changes;
}
export const attemptLabel = (a: WatchAttempt) => a.turn == null ? "初始 / 恢复点" : `回合 ${a.turn}${a.attempt > 1 ? ` / 尝试 ${a.attempt}` : ""}`;
