import type { Archive } from "./types";
import type { RunCatalog } from "./runCatalog";

export type CurveMetric = "rank_score" | "gold" | "experience_pool" | "gold_income" | "experience_income";
export interface CurvePoint { turn: number; rank_score: number | null; gold: number | null; experience_pool: number | null; gold_income: number | null; experience_income: number | null }
export interface CurveMilestone {
    id: string;
    turn: number;
    kind: "expansion" | "level_9" | "boss_victory" | "challenge_victory";
    label: string;
    detail: string;
}
export interface CurveMilestoneGroup { turn: number; events: CurveMilestone[] }
export interface CurveData {
    run_id: string | null;
    status: string;
    max_turns: number | null;
    points: CurvePoint[];
    // 旧服务仍可返回原有曲线，未提供节点时明确提示，不能据此声称从未发生。
    milestones?: CurveMilestone[];
    conditions: Partial<Record<"preset" | "data_hash" | "benchmark_version" | "protocol_hash" | "settings_hash", string | null> & { game_seed: number | null; scoring_seed: number | null }>;
    updated_at: string | null;
    warning: string | null;
}
export interface CurveChoice { key: string; name: string; model: string; preset: string; status: string; url: string; revision: string; aliases: string[] }
export function curveChoices(catalog: RunCatalog, archives: Archive[]): CurveChoice[] {
    const managed = new Set(catalog.runs.flatMap(job => job.archive_id || job.config.resume_run_id ? [job.archive_id || job.config.resume_run_id!] : []));
    return [
        ...[...catalog.runs].reverse().map(job => ({
            key: catalog.keys.get(job.id)!, name: job.name, model: job.config.model || "", preset: job.config.preset, status: job.status,
            url: `/api/workbench/curves?job_id=${encodeURIComponent(job.id)}`, revision: `${job.status}:${job.checkpoint_at}:${job.archive_id}`,
            aliases: [catalog.keys.get(job.id)!, job.id, ...(job.archive_id || job.config.resume_run_id ? [`archive:${job.archive_id || job.config.resume_run_id}`] : [])],
        })),
        ...archives.filter(archive => !managed.has(archive.run_id)).map(archive => ({
            key: `archive:${archive.run_id}`, name: archive.model || archive.run_id, model: archive.model, preset: archive.preset, status: archive.status,
            url: `/api/workbench/curves?run_id=${encodeURIComponent(archive.run_id)}`, revision: archive.status, aliases: [`archive:${archive.run_id}`],
        })),
    ];
}
export function selectedCurves(ids: string[], choices: CurveChoice[], catalog: RunCatalog): CurveChoice[] {
    const selected: CurveChoice[] = [];
    for (const id of ids) {
        const job = catalog.byId.get(id);
        const key = job ? catalog.keys.get(job.id)! : id;
        const choice = choices.find(choice => choice.aliases.includes(key));
        if (choice && !selected.some(item => item.key === choice.key)) selected.push(choice);
    }
    return selected.slice(0, 4);
}
export function curveSegments(points: CurvePoint[], metric: CurveMetric, from: number, to: number): CurvePoint[][] {
    const segments: CurvePoint[][] = [];
    let previous: number | null = null;
    for (const point of points) {
        if (point.turn < from || point.turn > to) continue;
        if (point[metric] === null) { previous = null; continue; }
        if (previous === null || point.turn !== previous + 1) segments.push([]);
        segments.at(-1)!.push(point);
        previous = point.turn;
    }
    return segments;
}
export function latestCommonTurn(data: (CurveData | undefined)[], metrics: CurveMetric[], from: number, to: number): number | null {
    if (!data.length || data.some(run => !run)) return null;
    const turns = data.map(run => new Set(run!.points
        .filter(point => point.turn >= from && point.turn <= to && metrics.every(metric => point[metric] !== null))
        .map(point => point.turn)));
    const common = [...turns[0]].filter(turn => turns.every(recorded => recorded.has(turn)));
    return common.length ? Math.max(...common) : null;
}

export function milestoneCategory(kind: CurveMilestone["kind"]): "economy" | "experience" | "power" {
    return kind === "expansion" ? "economy" : kind === "level_9" ? "experience" : "power";
}
export function curveMilestoneGroups(data: CurveData, metric: CurveMetric, from: number, to: number): CurveMilestoneGroup[] {
    const category = metric === "rank_score" ? "power" : metric.startsWith("gold") ? "economy" : "experience";
    const groups = new Map<number, CurveMilestone[]>();
    for (const event of data.milestones ?? []) {
        if (event.turn < from || event.turn > to || milestoneCategory(event.kind) !== category) continue;
        if (!groups.has(event.turn)) groups.set(event.turn, []);
        groups.get(event.turn)!.push(event);
    }
    return [...groups].sort(([a], [b]) => a - b).map(([turn, events]) => ({ turn, events }));
}
export const CURVE_COLORS = ["var(--teal-10)", "var(--orange-10)", "var(--blue-10)", "var(--purple-10)"];
