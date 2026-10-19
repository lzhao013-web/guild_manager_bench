import { expect, it } from "vitest";
import { conditionWarning, curveChoices, curveSegments, selectedCurves } from "./curveModel";
import type { CurveData, CurvePoint } from "./curveModel";
import { buildRunCatalog } from "./runCatalog";
import { testArchive, testJob } from "./test-jobs";

const point = (turn: number, value: number | null): CurvePoint => ({ turn, rank_score: value, gold: value, experience_pool: value, gold_income: value, experience_income: value });
const data: CurveData = { run_id: "one", status: "completed", max_turns: 35, points: [point(1, 0)], updated_at: null, warning: null,
    conditions: { preset: "full", data_hash: "data", protocol_hash: "rules", settings_hash: "settings", game_seed: 7, scoring_seed: 11 } };

it("曲线只连接连续且有数值的回合，零是真实值，缺失不补零也不跨缺口连线", () => {
    const points = [point(1, 0), point(2, null), point(3, 3), point(4, 4), point(6, 6)];
    expect(curveSegments(points, "rank_score", 1, 6).map(segment => segment.map(point => point.turn))).toEqual([[1], [3, 4], [6]]);
    expect(curveSegments(points, "gold", 3, 4).map(segment => segment.map(point => point.gold))).toEqual([[3, 4]]);
});

it("首次运行、续跑、旧链接和同一份 CLI 存档只能占一条曲线", () => {
    const old = testJob("first", { status: "interrupted" });
    const current = testJob("resumed", { created_at: "2026-10-19T10:00:00Z" });
    const catalog = buildRunCatalog([current, old]);
    const choices = curveChoices(catalog, [testArchive(), testArchive("cli")]);
    expect(choices).toHaveLength(2);
    expect(selectedCurves(["first", "resumed", "archive:same-archive"], choices, catalog)).toHaveLength(1);
    expect(selectedCurves(["archive:same-archive"], choices, catalog)[0].url).toBe("/api/workbench/curves?job_id=resumed");
    expect(choices[1].url).toBe("/api/workbench/curves?run_id=cli");
});

it("同模型但不同存档保留独立曲线，最多比较四路", () => {
    const catalog = buildRunCatalog(Array.from({ length: 5 }, (_, i) => testJob(`job-${i}`, { archive_id: `archive-${i}` })));
    const choices = curveChoices(catalog, []);
    expect(choices).toHaveLength(5);
    expect(selectedCurves(choices.map(choice => choice.key), choices, catalog)).toHaveLength(4);
});

it("同条件不制造警告，不同规则、种子和上限都明确提醒", () => {
    expect(conditionWarning([data, { ...data, run_id: "two" }])).toBeNull();
    expect(conditionWarning([data, { ...data, conditions: { ...data.conditions, protocol_hash: "other" } }])).toContain("规则");
    expect(conditionWarning([data, { ...data, conditions: { ...data.conditions, game_seed: 2 } }])).toContain("种子");
    expect(conditionWarning([data, { ...data, max_turns: 8 }])).toContain("回合上限");
});

it("旧存档缺协议资料或随机种子未固定时不声称同条件", () => {
    expect(conditionWarning([data, { ...data, conditions: {} }])).not.toBeNull();
    expect(conditionWarning([{ ...data, conditions: {} }, { ...data, conditions: {} }])).toContain("未核实");
});
