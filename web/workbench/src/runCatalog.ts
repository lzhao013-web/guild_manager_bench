import type { Archive, Job } from "./types";
import { isActive } from "./model";

export interface RunCatalog {
    runs: Job[];
    byId: Map<string, Job>;
    keys: Map<string, string>;
}

/** 执行 ID 只用于访问日志和控制进程；界面将同一存档投影为一个 run。 */
export function buildRunCatalog(jobs: Job[]): RunCatalog {
    const groups = new Map<string, Job[]>();
    for (const job of jobs) {
        const archive = job.archive_id || job.config.resume_run_id;
        const identity = archive ? `archive:${archive}` : `job:${job.id}`;
        const group = groups.get(identity) || [];
        group.push(job);
        groups.set(identity, group);
    }
    const runs: Job[] = [];
    const byId = new Map<string, Job>();
    const keys = new Map<string, string>();
    for (const group of groups.values()) {
        group.sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
        const first = group[0];
        const current = group.filter(isActive).at(-1) || group.at(-1)!;
        // 归属仍是首次创建的实验；续跑不是另一个实验，也不累加已恢复的统计。
        const run = {
            ...current,
            batch_id: first.batch_id,
            batch_name: first.batch_name,
            created_at: first.created_at,
        };
        runs.push(run);
        for (const job of group) {
            byId.set(job.id, run);
            keys.set(job.id, first.id);
        }
    }
    runs.sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
    return { runs, byId, keys };
}

/** 兼容浏览器中已保存的旧执行 ID，并去掉同一个 run 的重复选择。 */
export function canonicalRunIds(ids: string[], catalog: RunCatalog): string[] {
    return [...new Set(ids.flatMap(id => {
        const key = catalog.keys.get(id);
        return key ? [key] : [];
    }))];
}

export interface HistoryRun {
    id: string;
    created_at: string;
    job: Job | null;
    archive: Archive | null;
}

/** 工作台状态为准；CLI 存档只读接入。没有 replay 的终态同样不能丢失。 */
export function historyRuns(jobs: Job[], archives: Archive[]): HistoryRun[] {
    const byArchive = new Map(archives.map(archive => [archive.run_id, archive]));
    const managed = new Set(jobs.flatMap(job => job.archive_id ? [job.archive_id] : []));
    return [
        ...jobs.filter(job => !isActive(job)).map(job => ({
            id: job.archive_id || job.id,
            created_at: job.created_at,
            job,
            archive: job.archive_id ? byArchive.get(job.archive_id) || null : null,
        })),
        ...archives.filter(archive => !managed.has(archive.run_id)).map(archive => ({
            id: archive.run_id,
            created_at: archive.created_at,
            job: null,
            archive,
        })),
    ].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
}
