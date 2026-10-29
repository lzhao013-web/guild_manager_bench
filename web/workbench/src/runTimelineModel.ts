import { historyAction, planSteps } from "./trajectoryModel";
import type { TrajectoryData, TrajectoryRow } from "./trajectoryModel";
import type { ActionEntry } from "./actionModel";
import { isReadOnlyTool } from "./toolCalls";
import { summarizeBattles } from "./actionReturns";
import { STAT_NAMES, signed } from "./sceneModel";

export interface TimelineItem {
    id: string;
    row: TrajectoryRow;
    entry: ActionEntry;
    emphasis: "upgrade" | "settlement" | "normal";
    auxiliary: boolean;
    planStep?: TrajectoryRow["planStep"];
}
export type TimelineBlock = { kind: "action"; item: TimelineItem } | { kind: "auxiliary"; id: string; items: TimelineItem[] };
export interface TimelineRound {
    id: number;
    turn: number | null;
    attempt: number;
    status: string;
    score: number | null;
    items: TimelineItem[];
    blocks: TimelineBlock[];
}
function timelineItem(row: TrajectoryRow, parent = row, planStep?: TimelineItem["planStep"]): TimelineItem {
    const entry = historyAction(row);
    const auxiliary = entry.status !== "failed" && (isReadOnlyTool(row.event) || row.event.name === "write_memo" || row.event.result?.already_executed === true);
    return { id: row.id, row: parent, entry, auxiliary, planStep,
        emphasis: row.event.name === "purchase_upgrade" ? "upgrade" : row.event.name === "end_turn" ? "settlement" : "normal" };
}
export function timelineItems(row: TrajectoryRow): TimelineItem[] {
    const steps = planSteps(row);
    return steps.length ? steps.map(step => timelineItem(step, row, step.planStep)) : [timelineItem(row)];
}
export function timelineRounds(data: TrajectoryData): TimelineRound[] {
    const rounds = new Map<number, TimelineRound>();
    const numbers = new Map<number | null, number>();
    for (const attempt of data.attempts || []) {
        numbers.set(attempt.turn, Math.max(numbers.get(attempt.turn) || 0, attempt.attempt));
        rounds.set(attempt.id, { id: attempt.id, turn: attempt.turn, attempt: attempt.attempt,
            status: attempt.status, score: attempt.score, items: [], blocks: [] });
    }
    for (const row of data.entries) {
        let round = rounds.get(row.attempt);
        if (!round) {
            const attempt = (numbers.get(row.turn) || 0) + 1;
            numbers.set(row.turn, attempt);
            round = { id: row.attempt, turn: row.turn, attempt, score: null,
                status: row.lifecycle === "rolled_back" ? "rolled_back" : row.lifecycle === "confirmed" ? "completed" : "uncommitted", items: [], blocks: [] };
            rounds.set(row.attempt, round);
        }
        round.items.push(...timelineItems(row));
    }
    for (const round of rounds.values()) {
        for (const item of round.items) {
            const previous = round.blocks.at(-1);
            if (item.auxiliary && previous?.kind === "auxiliary") previous.items.push(item);
            else round.blocks.push(item.auxiliary ? { kind: "auxiliary", id: item.id, items: [item] } : { kind: "action", item });
        }
    }
    return [...rounds.values()].filter(round => round.turn !== null || round.items.length > 0);
}
export interface TimelineTurn {
    id: string;
    turn: number | null;
    attempts: TimelineRound[];
    primary: TimelineRound;
    previousRankScore: number | null;
    rankScoreDelta: number | null;
}
export function timelineTurns(rounds: TimelineRound[]): TimelineTurn[] {
    const turns = new Map<number | null, TimelineTurn>();
    for (const round of rounds) {
        let turn = turns.get(round.turn);
        if (!turn) {
            turn = { id: round.turn === null ? "initial" : `turn:${round.turn}`, turn: round.turn, attempts: [], primary: round, previousRankScore: null, rankScoreDelta: null };
            turns.set(round.turn, turn);
        }
        turn.attempts.push(round);
        // 主表只呈现最近一次未回滚尝试，旧尝试仍在详情中保留。
        if (round.status !== "rolled_back" || turn.primary.status === "rolled_back") turn.primary = round;
    }
    for (const turn of turns.values()) {
        const previous = turn.turn === null ? undefined : turns.get(turn.turn - 1);
        // score 来自 turn_scored.rank_score，只比较相邻且未回滚的已完成回合。
        if (turn.primary.status === "completed" && previous?.primary.status === "completed"
            && turn.primary.score !== null && previous.primary.score !== null) {
            turn.previousRankScore = previous.primary.score;
            turn.rankScoreDelta = Number((turn.primary.score - previous.primary.score).toFixed(4));
        }
    }
    return [...turns.values()];
}
const OPERATION_LABELS: Record<string, string> = {
    recruit_adventurer: "招募", dismiss_adventurer: "解雇", craft_equipment: "制作", equip_item: "装备", unequip_item: "卸装",
    allocate_experience: "培养", configure_tactics: "战术", refresh_monsters: "刷新",
};
export function turnSummary(turn: TimelineTurn) {
    const round = turn.primary;
    const successful = round.items.filter(item => !item.auxiliary && item.entry.status === "succeeded");
    const upgrades = successful.filter(item => item.emphasis === "upgrade").map(item => {
        const node = item.entry.event.result?._action_output?.global_upgrades?.[0];
        return node?.name || item.entry.title.replace(/^(购买全局加成|购买升级|购买)\s*/, "");
    });
    const operations = new Map<string, number>();
    for (const item of successful.filter(item => item.emphasis === "normal")) {
        const label = OPERATION_LABELS[item.entry.event.name] || "其他";
        operations.set(label, (operations.get(label) || 0) + 1);
    }
    const auxiliary = round.items.filter(item => item.auxiliary).length;
    const failures = round.items.filter(item => item.entry.status === "failed").length;
    const pending = round.items.filter(item => item.entry.status === "pending").length;
    const unrecorded = round.items.filter(item => !item.auxiliary && item.entry.status === "recorded").length;
    const ended = successful.filter(item => item.emphasis === "settlement").at(-1);
    const battle = ended ? settlement(ended) : null;
    const experienceGained = round.status !== "rolled_back" ? battle?.experience ?? null : null;
    const operationText = [...operations].map(([name, count]) => `${name}×${count}`).join(" / ");
    const endText = battle ? `${battle.wins}胜${battle.losses}负${battle.unknown ? ` ${battle.unknown}未知` : ""}`
        : ended ? "已结束 / 战果缺失" : failures && round.items.some(item => item.emphasis === "settlement" && item.entry.status === "failed") ? "结束失败" : "未结算";
    return { upgrades, operationText, auxiliary, failures, pending, unrecorded, ended, battle, experienceGained, endText,
        emptyText: pending ? "执行中" : unrecorded ? "结果未明" : auxiliary ? "仅辅助调用" : failures ? "操作失败" : "无工具记录" };
}
export function timelineColumnCount(width: number, height: number, turnCount: number): number {
    const maxColumns = Math.min(2, Math.max(1, Math.floor(width / 380)));
    const rows = Math.max(1, Math.floor((height - 22) / 20));
    return Math.min(maxColumns, Math.max(1, Math.ceil(turnCount / rows)));
}
export function timelineRowHeight(height: number, turnCount: number, columns: number): number {
    const rows = Math.max(1, Math.ceil(turnCount / columns));
    return Math.min(24, Math.max(20, Math.floor((height - 22) / rows)));
}
export function timelineBands(turns: TimelineTurn[], columns: number): TimelineTurn[][] {
    const size = Math.max(1, Math.ceil(turns.length / columns));
    return Array.from({ length: Math.ceil(turns.length / size) }, (_, index) => turns.slice(index * size, (index + 1) * size));
}

export function roundTitle(round: TimelineRound): string {
    return `${round.turn === null ? "初始化 / 恢复" : `第 ${round.turn} 回合`}${round.attempt > 1 ? ` · 尝试 ${round.attempt}` : ""}`;
}
export function upgradeDetails(item: TimelineItem): string[] {
    if (item.entry.status !== "succeeded") return [];
    const nodes = item.entry.event.result?._action_output?.global_upgrades || [];
    return nodes.flatMap((node: Record<string, any>) => {
        const lines: string[] = [];
        if (typeof node.description === "string" && node.description) lines.push(node.description);
        const stats = Object.entries(node.stats || {}).filter(([, value]) => typeof value === "number" && value !== 0)
            .map(([key, value]) => `${STAT_NAMES[key] || key} ${signed(value as number)}`);
        if (stats.length) lines.push(`全队属性：${stats.join(" · ")}`);
        if (typeof node.party_size_bonus === "number" && node.party_size_bonus !== 0) lines.push(`编队容量 ${signed(node.party_size_bonus)}`);
        return lines;
    });
}
export function settlement(item: TimelineItem) {
    if (item.entry.status !== "succeeded") return null;
    const result = item.entry.event.result;
    const recorded = result?.turn_result?.battles ?? result?.event?.battles;
    if (!Array.isArray(recorded)) return null;
    return summarizeBattles(recorded);
}
