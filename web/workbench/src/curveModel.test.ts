import { expect, it } from "vitest";
import { curveChoices, curveMilestoneGroups, curveSegments, latestCommonTurn, selectedCurves } from "./curveModel";
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

it("节点按指标和回合分组，切换收入余额使用同一事件，缺数值不丢节点也不补零", () => {
    const marked: CurveData = { ...data, milestones: [
        { id: "one", turn: 1, kind: "expansion", label: "扩编 1", detail: "扩编 1" },
        { id: "two", turn: 2, kind: "level_9", label: "角色 Lv9", detail: "角色 Lv9" },
        { id: "three", turn: 3, kind: "boss_victory", label: "首领", detail: "首领" },
        { id: "four", turn: 3, kind: "challenge_victory", label: "挑战", detail: "挑战" },
    ] };
    expect(curveMilestoneGroups(marked, "gold", 1, 3)).toEqual(curveMilestoneGroups(marked, "gold_income", 1, 3));
    expect(curveMilestoneGroups(marked, "experience_pool", 1, 3)[0].turn).toBe(2);
    expect(curveMilestoneGroups(marked, "rank_score", 2, 3)).toMatchObject([{ turn: 3, events: [{ id: "three" }, { id: "four" }] }]);
    expect(curveMilestoneGroups(marked, "rank_score", 1, 2)).toEqual([]);
    expect(curveMilestoneGroups(data, "gold", 1, 35)).toEqual([]);
});

it("共同回合取实际交集和当前指标，保留零值，不用最短长度或补齐缺口", () => {
    const first = { ...data, points: [point(1, 0), point(2, 20), point(4, 40)] };
    const second = { ...data, points: [point(1, 0), point(3, 30), { ...point(4, 40), gold_income: null }] };
    expect(latestCommonTurn([first, second], ["rank_score", "gold_income"], 1, 4)).toBe(1);
    expect(latestCommonTurn([first, second], ["rank_score", "gold"], 1, 4)).toBe(4);
    expect(latestCommonTurn([first, second], ["rank_score"], 2, 3)).toBeNull();
    expect(latestCommonTurn([first, undefined], ["rank_score"], 1, 4)).toBeNull();
    expect(latestCommonTurn([first, { ...data, points: [] }], ["rank_score"], 1, 4)).toBeNull();
    expect(latestCommonTurn([], ["rank_score"], 1, 4)).toBeNull();
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
