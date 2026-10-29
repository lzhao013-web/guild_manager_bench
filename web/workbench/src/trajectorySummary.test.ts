import { expect, it } from "vitest";
import { confirmed, historyAction, matchesOperation, matchesTrajectoryState, operationCategory, planSteps, summarizeTrajectories, trajectorySearch } from "./trajectoryModel";
import type { TrajectoryRow } from "./trajectoryModel";

function records() {
    const ordinals: Record<string, number> = {};
    const make = (name: string, lifecycle: TrajectoryRow["lifecycle"] = "confirmed", ok: boolean | "pending" = true): TrajectoryRow => {
        const attempt = lifecycle === "confirmed" ? 100 : lifecycle === "rolled_back" ? 200 : 300;
        const ordinal = (ordinals[attempt] || 0) + 1;
        ordinals[attempt] = ordinal;
        return { id: `${attempt}:${ordinal}`, attempt, turn: attempt === 100 ? 1 : 2, ordinal, cursor: attempt * 100 + ordinal,
            lifecycle, at: "2026-10-19T12:00:00Z", subjects: [], names: {},
            event: { type: ok === "pending" ? "tool_call" : "tool_result", name, result: ok === "pending" ? {} : { ok } } };
    };
    return [
        make("purchase_upgrade"), make("craft_equipment"),
        make("equip_item"), make("unequip_item"), make("allocate_experience"), make("configure_tactics"),
        make("end_turn"), make("write_memo"), make("dismiss_adventurer"),
        make("craft_equipment", "rolled_back"), make("craft_equipment", "rolled_back", false),
        make("craft_equipment", "uncommitted", false), make("craft_equipment", "uncommitted", "pending"),
    ];
}
it("统计一次操作而非受影响对象，成功只计已提交且未回滚的真实成功", () => {
    const rows = records();
    rows[2].subjects = [{ kind: "equipment", id: "eq-1", name: "铁剑" }, { kind: "equipment", id: "eq-2", name: "铁剑" }];
    const before = structuredClone(rows);
    const summary = summarizeTrajectories(rows);
    expect(summary.total).toBe(13);
    expect(summary.states).toEqual({ confirmed: 9, uncommitted: 2, failed: 2, rolled_back: 2, succeeded: 10, pending: 1, recorded: 0 });
    expect(summary.categories.find(category => category.id === "craft")).toMatchObject({ attempts: 5, confirmed: 1 });
    expect(summary.categories.find(category => category.id === "equipment")).toMatchObject({ attempts: 2, confirmed: 2 });
    expect(summary.categories.find(category => category.id === "memo")).toMatchObject({ attempts: 1, confirmed: 1 });
    expect(summary.categories.find(category => category.id === "dismiss")).toMatchObject({ attempts: 1, confirmed: 1 });
    expect(rows).toEqual(before);
});
it("概览分类与筛选一致，失败和回滚是可以重叠的维度", () => {
    const rows = records();
    const summary = summarizeTrajectories(rows);
    for (const category of summary.categories) {
        const matching = rows.filter(row => matchesOperation(row, `category:${category.id}`));
        expect(matching).toHaveLength(category.attempts);
        expect(matching.filter(row => matchesTrajectoryState(row, "confirmed"))).toHaveLength(category.confirmed);
    }
    expect(rows.filter(row => matchesOperation(row, "equip_item"))).toHaveLength(1);
    expect(rows.filter(row => matchesOperation(row, "category:equipment"))).toHaveLength(2);
    const rolledFailure = rows.find(row => row.lifecycle === "rolled_back" && row.event.result.ok === false)!;
    expect(matchesTrajectoryState(rolledFailure, "failed")).toBe(true);
    expect(matchesTrajectoryState(rolledFailure, "rolled_back")).toBe(true);
    expect(matchesTrajectoryState(rolledFailure, "confirmed")).toBe(false);
    expect(operationCategory({ type: "tool_result", name: "write_memo", result: { ok: true } })).toBe("memo");
});
it("旧式刷新预览和实刷均可统计筛选，只读预览不混同付费动作", () => {
    const preview: TrajectoryRow = { ...records()[0], id: "100:10", ordinal: 10, cursor: 10100,
        event: { type: "tool_result", name: "refresh_monsters", arguments: { locked_monster_ids: [], preview: true }, result: { ok: true } } };
    const refresh: TrajectoryRow = { ...preview, id: "100:11", ordinal: 11, cursor: 10200,
        event: { ...preview.event, arguments: { locked_monster_ids: [], preview: false } } };
    const summary = summarizeTrajectories([preview, refresh]);
    expect(summary.total).toBe(2);
    expect(summary.states.confirmed).toBe(2);
    expect(summary.categories.find(category => category.id === "preview")).toMatchObject({ attempts: 1, confirmed: 1 });
    expect(summary.categories.find(category => category.id === "refresh")).toMatchObject({ attempts: 1, confirmed: 1 });
    expect(summary.tools).toHaveLength(1);
    expect(summary.tools[0]).toMatchObject({ name: "refresh_monsters", total: 2, succeeded: 2 });
    expect(matchesOperation(preview, "all")).toBe(true);
    expect(matchesOperation(preview, "refresh_monsters")).toBe(true);
    expect(matchesOperation(refresh, "category:refresh")).toBe(true);
    expect(historyAction(preview)).toMatchObject({ kind: "query", title: "预览怪物刷新", summary: "" });
});
it("方案步骤共享真实位置与生命周期，独立检索输入，不继承整批对象或改变原记录", () => {
    const parent: TrajectoryRow = { ...records()[0], names: { hero: "伊莲", sword: "青铜剑" }, subjects: [{ kind: "recipe", id: "sword", name: "青铜剑" }],
        event: { type: "tool_result", name: "execute_plan", result: { ok: true, events: [
            { action: { type: "craft", recipe_id: "sword" }, summary: "制作青铜剑" },
            { action: { type: "allocate_experience", adventurer_id: "hero", amount: 20 }, summary: "分配经验" },
        ] } } };
    const before = structuredClone(parent);
    const steps = planSteps(parent);
    expect(steps.map(step => step.event.name)).toEqual(["craft_equipment", "allocate_experience"]);
    expect(steps[1]).toMatchObject({ attempt: parent.attempt, ordinal: parent.ordinal, cursor: parent.cursor, turn: parent.turn, planStep: { index: 2, total: 2 } });
    expect(trajectorySearch(steps[1])).toContain("伊莲");
    expect(trajectorySearch(steps[1])).toContain("hero");
    expect(trajectorySearch(steps[1])).not.toContain("青铜剑");
    expect(confirmed(planSteps({ ...parent, lifecycle: "rolled_back" })[0])).toBe(false);
    expect(matchesTrajectoryState(planSteps({ ...parent, lifecycle: "uncommitted" })[0], "uncommitted")).toBe(true);
    const summary = summarizeTrajectories([parent]);
    expect(summary.total).toBe(1);
    expect(summary.states.confirmed).toBe(1);
    expect(summary.tools).toMatchObject([{ name: "execute_plan", total: 1 }]);
    expect(summary.categories.find(category => category.id === "craft")).toMatchObject({ attempts: 0, confirmed: 0, planSteps: 1, planConfirmed: 1 });
    const rolledBack = summarizeTrajectories([{ ...parent, lifecycle: "rolled_back" }]);
    expect(rolledBack.categories.find(category => category.id === "craft")).toMatchObject({ planSteps: 1, planConfirmed: 0 });
    expect(parent).toEqual(before);
});
it("已读取的空日志可得到零计数，不能修改原始数据或虚构成功", () => {
    const summary = summarizeTrajectories([]);
    expect(summary.total).toBe(0);
    expect(summary.categories).toHaveLength(15);
    expect(summary.categories.find(category => category.id === "plan")).toMatchObject({ attempts: 0, confirmed: 0 });
    expect(summary.categories.find(category => category.id === "exchange")).toMatchObject({ attempts: 0, confirmed: 0 });
    expect(summary.tools).toEqual([]);
    expect(summary.categories.every(category => category.attempts === 0 && category.confirmed === 0)).toBe(true);
});
