import type { Archive } from "./types";
import type { RunCatalog } from "./runCatalog";

export type CurveMetric = "rank_score" | "gold" | "experience_pool" | "gold_income" | "experience_income";
export interface CurvePoint { turn: number; rank_score: number | null; gold: number | null; experience_pool: number | null; gold_income: number | null; experience_income: number | null }
export interface CurveData {
    run_id: string | null;
    status: string;
    max_turns: number | null;
    points: CurvePoint[];
    conditions: Partial<Record<"preset" | "data_hash" | "benchmark_version" | "protocol_hash" | "settings_hash", string | null> & { game_seed: number | null; scoring_seed: number | null }>;
    updated_at: string | null;
    warning: string | null;
}
export interface CurveChoice { key: string; name: string; preset: string; status: string; url: string; revision: string; aliases: string[] }
export function curveChoices(catalog: RunCatalog, archives: Archive[]): CurveChoice[] {
    const managed = new Set(catalog.runs.flatMap(job => job.archive_id || job.config.resume_run_id ? [job.archive_id || job.config.resume_run_id!] : []));
    return [
        ...[...catalog.runs].reverse().map(job => ({
            key: catalog.keys.get(job.id)!, name: job.name, preset: job.config.preset, status: job.status,
            url: `/api/workbench/curves?job_id=${encodeURIComponent(job.id)}`, revision: `${job.status}:${job.checkpoint_at}:${job.archive_id}`,
            aliases: [catalog.keys.get(job.id)!, job.id, ...(job.archive_id || job.config.resume_run_id ? [`archive:${job.archive_id || job.config.resume_run_id}`] : [])],
        })),
        ...archives.filter(archive => !managed.has(archive.run_id)).map(archive => ({
            key: `archive:${archive.run_id}`, name: archive.model || archive.run_id, preset: archive.preset, status: archive.status,
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
export function conditionWarning(data: CurveData[]): string | null {
    if (data.length < 2) return null;
    const keys = ["preset", "data_hash", "protocol_hash", "settings_hash", "game_seed", "scoring_seed"] as const;
    const missing = data.some(run => ["data_hash", "protocol_hash", "settings_hash"].some(key => !run.conditions[key as keyof CurveData["conditions"]]));
    const labels = { preset: "Preset", data_hash: "数据", protocol_hash: "规则协议", settings_hash: "评测配置", game_seed: "游戏种子", scoring_seed: "评分种子" };
    const different = keys.filter(key => new Set(data.map(run => run.conditions[key]).filter(value => value != null)).size > 1).map(key => labels[key]);
    if (new Set(data.map(run => run.max_turns).filter(value => value !== null)).size > 1) different.push("回合上限");
    if (different.length) return `所选 run 的${different.join("、")}不同。曲线与数值差仅供并列查看，不代表同条件效果差异。`;
    if (missing || data.some(run => run.conditions.game_seed == null)) return "部分规则信息或游戏种子未核实。曲线与数值差仅供并列查看，不作同条件结论。";
    return null;
}
export const CURVE_COLORS = ["var(--teal-10)", "var(--orange-10)", "var(--blue-10)", "var(--purple-10)"];
