import { expect, it } from "vitest";
import { buildRunCatalog, canonicalRunIds, historyRuns } from "./runCatalog";
import { testArchive, testJob } from "./test-jobs";

it("同一存档只保留最新状态，顺序不依赖 UUID，也不重复累加续跑恢复的用量", () => {
    const first = testJob("zzz-first", { status: "interrupted" });
    const resumed = testJob("aaa-resumed", {
        created_at: "2026-10-18T11:00:00Z", status: "completed", turn: 35, last_score: 2290.3388,
        batch_id: "resume-batch", batch_name: "续跑批次",
        usage: { input_tokens: 4000, output_tokens: 800, reported: 4, responses: 4 },
    });
    const catalog = buildRunCatalog([resumed, first]);
    expect(catalog.runs).toHaveLength(1);
    expect(catalog.runs[0]).toMatchObject({ id: "aaa-resumed", status: "completed", turn: 35, last_score: 2290.3388, batch_id: first.batch_id, batch_name: first.batch_name, created_at: first.created_at });
    expect(catalog.runs[0].usage.output_tokens).toBe(800);
    expect(catalog.byId.get(first.id)).toBe(catalog.byId.get(resumed.id));
    expect(canonicalRunIds([resumed.id, first.id, "removed"], catalog)).toEqual([first.id]);
    expect(historyRuns(catalog.runs, [testArchive()])).toHaveLength(1);
    expect(first.status).toBe("interrupted");
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
