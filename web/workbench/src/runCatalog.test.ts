import { expect, it } from "vitest";
import type { Archive } from "./types";
import { buildRunCatalog, canonicalRunIds, historyMetrics, historyPreset, historyRuns, sortHistoryRuns } from "./runCatalog";
import type { HistorySort } from "./runCatalog";
import { testArchive, testJob } from "./test-jobs";

it("同一存档只保留最新状态，顺序不依赖 UUID，也不重复累加续跑恢复的用量", () => {
    const first = testJob("zzz-first", { status: "interrupted" });
    const resumed = testJob("aaa-resumed", {
        created_at: "2026-10-18T11:00:00Z", status: "completed", turn: 35, last_score: 2290.3388,
        batch_id: "resume-batch", batch_name: "续跑批次", model_duration_ms: 1250,
        usage: { input_tokens: 4000, output_tokens: 800, reported: 4, responses: 4 },
    });
    const catalog = buildRunCatalog([resumed, first]);
    expect(catalog.runs).toHaveLength(1);
    expect(catalog.runs[0]).toMatchObject({ id: "aaa-resumed", status: "completed", turn: 35, last_score: 2290.3388, batch_id: first.batch_id, batch_name: first.batch_name, created_at: first.created_at });
    expect(catalog.runs[0].usage.output_tokens).toBe(800);
    expect(historyMetrics(historyRuns(catalog.runs, [testArchive()])[0])).toMatchObject({ duration_ms: 1250, total_tokens: 4800 });
    expect(catalog.byId.get(first.id)).toBe(catalog.byId.get(resumed.id));
    expect(canonicalRunIds([resumed.id, first.id, "removed"], catalog)).toEqual([first.id]);
    expect(historyRuns(catalog.runs, [testArchive()])).toHaveLength(1);
    expect(first.status).toBe("interrupted");
});

it("受控run使用当前Token统计，旧工作台缺失累计耗时时读取存档，不相加", () => {
    const job = testJob("old-managed", { status: "completed", usage: { input_tokens: null, output_tokens: 80, reported: 1, responses: 2 } });
    const archive = testArchive("same-archive", { model_duration_ms: 1200, usage: { input_tokens: 999, output_tokens: 999, reported: 1, responses: 1 } });
    const row = historyRuns([job], [archive])[0];
    expect(historyMetrics(row)).toEqual({ duration_ms: 1200, input_tokens: null, output_tokens: 80, total_tokens: null });
    expect(historyMetrics({ ...row, job: { ...job, model_duration_ms: 0 } }).duration_ms).toBe(0);
});

it("同名模型的不同存档、尚未生成存档的不同 run 都不能合并", () => {
    const catalog = buildRunCatalog([
        testJob("one", { archive_id: "a" }), testJob("two", { archive_id: "b" }),
        testJob("three", { archive_id: null }), testJob("four", { archive_id: null }),
    ]);
    expect(catalog.runs).toHaveLength(4);
});

it("续跑排队时沿用原 run 身份，并从历史移出，不能把旧中断状态再展示出来", () => {
    const first = testJob("first", { status: "interrupted" });
    const next = testJob("next", { status: "queued", created_at: "2026-10-18T11:00:00Z", config: { ...first.config, resume_run_id: first.archive_id } });
    const catalog = buildRunCatalog([next, first]);
    expect(catalog.runs[0].status).toBe("queued");
    expect(catalog.keys.get(next.id)).toBe(first.id);
    expect(historyRuns(catalog.runs, [testArchive()])).toEqual([]);
});

it("首次落盘后，已保存的关注和选择仍然指向原来的 run", () => {
    const before = testJob("first", { archive_id: null });
    const after = { ...before, archive_id: "new-archive" };
    expect(canonicalRunIds([before.id], buildRunCatalog([after]))).toEqual([before.id]);
});

it("未归档的失败和取消留在历史，同时保留只读 CLI 存档", () => {
    const catalog = buildRunCatalog([
        testJob("failed", { archive_id: null, status: "failed", turn: null }),
        testJob("cancelled", { archive_id: null, status: "interrupted", turn: null }),
        testJob("active"),
    ]);
    const rows = historyRuns(catalog.runs, [testArchive(), testArchive("cli", { status: "running" })]);
    expect(rows).toHaveLength(3);
    expect(rows.map(row => row.id)).toEqual(expect.arrayContaining(["failed", "cancelled", "cli"]));
});

const sortedArchives: Archive[] = [
    testArchive("a", { created_at: "2026-10-18T12:00:00+08:00", model: "Model2", rank_score: 0, turns: 2 }),
    testArchive("b", { created_at: "2026-10-18T05:00:00Z", model: "Model10", rank_score: 100, turns: 9 }),
    testArchive("unknown-date", { created_at: null, model: "", rank_score: 50, turns: 0 }),
];
const sortedJobs = [testJob("c", { archive_id: null, status: "failed", created_at: "2026-10-18T06:00:00Z", name: "Model1", last_score: null, turn: null })];

it.each<[HistorySort, string[]]>([
    [{ field: "created_at", direction: "desc" }, ["c", "b", "a", "unknown-date"]],
    [{ field: "created_at", direction: "asc" }, ["a", "b", "c", "unknown-date"]],
    [{ field: "score", direction: "desc" }, ["b", "unknown-date", "a", "c"]],
    [{ field: "score", direction: "asc" }, ["a", "unknown-date", "b", "c"]],
    [{ field: "turns", direction: "desc" }, ["b", "a", "unknown-date", "c"]],
    [{ field: "turns", direction: "asc" }, ["unknown-date", "a", "b", "c"]],
    [{ field: "name", direction: "asc" }, ["c", "a", "b", "unknown-date"]],
    [{ field: "name", direction: "desc" }, ["b", "a", "c", "unknown-date"]],
])("历史排序 %s 支持受控 run 与 CLI，缺失值始终置后且不修改原列表", (sort, expected) => {
    const rows = historyRuns(sortedJobs, sortedArchives);
    const before = rows.map(row => row.id);
    expect(sortHistoryRuns(rows, sort).map(row => row.id)).toEqual(expected);
    expect(rows.map(row => row.id)).toEqual(before);
});

it("preset 与评分均以当前展示值为准，不使用受控 run 旧存档的过时值排序", () => {
    const job = testJob("managed", { status: "failed", last_score: null });
    const rows = historyRuns([job], [testArchive("same-archive", { preset: "advanced", rank_score: 9999 }), testArchive("zero", { rank_score: 0 })]);
    expect(historyPreset(rows.find(row => row.job)!)).toBe("default");
    expect(sortHistoryRuns(rows, { field: "score", direction: "desc" }).map(row => row.id)).toEqual(["zero", "same-archive"]);
});

it.each<[HistorySort, string[]]>([
    [{ field: "preset", direction: "asc" }, ["advanced", "default", "full", "unknown"]],
    [{ field: "preset", direction: "desc" }, ["full", "default", "advanced", "unknown"]],
    [{ field: "status", direction: "asc" }, ["default", "full", "advanced", "unknown"]],
    [{ field: "status", direction: "desc" }, ["advanced", "full", "default", "unknown"]],
])("表头排序 %s 使用 preset 和当前显示状态，未记录值不参与正常值排序", (sort, expected) => {
    const rows = historyRuns([testJob("default", { archive_id: null, status: "failed" })], [
        testArchive("full", { preset: "full", status: "completed" }),
        testArchive("advanced", { preset: "advanced", status: "interrupted" }),
        testArchive("unknown", { preset: "", status: "" }),
    ]);
    expect(sortHistoryRuns(rows, sort).map(row => row.id)).toEqual(expected);
});
