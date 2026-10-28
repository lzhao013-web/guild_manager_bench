import { expect, it } from "vitest";
import { roundTitle, settlement, timelineBands, timelineColumnCount, timelineItems, timelineRounds, timelineRowHeight, timelineTurns, turnSummary, upgradeDetails } from "./runTimelineModel";
import type { TrajectoryData, TrajectoryRow } from "./trajectoryModel";

function row(name: string, patch: Partial<TrajectoryRow> = {}): TrajectoryRow {
    return { id: "100:1", attempt: 100, turn: 1, ordinal: 1, cursor: 110, at: "2026-10-25", lifecycle: "confirmed", subjects: [], names: {},
        event: { type: "tool_result", name, result: { ok: true } }, ...patch };
}
const data = (entries: TrajectoryRow[]): TrajectoryData => ({ cursor: 900, origin: "archive", warning: null, entries });

it("辅助调用仅按相邻顺序折叠，失败查询与实际操作不被藏掉", () => {
    const rows = [row("get_party"), row("get_inventory", { id: "100:2" }), row("purchase_upgrade", { id: "100:3" }),
        row("get_party", { id: "100:4" }), row("get_party", { id: "100:5", event: { type: "tool_result", name: "get_party", result: { ok: false, error: "读取失败" } } }),
        row("write_memo", { id: "100:6" })];
    const round = timelineRounds(data(rows))[0];
    expect(round.blocks.map(block => block.kind)).toEqual(["auxiliary", "action", "auxiliary", "action", "auxiliary"]);
    expect(round.blocks[0]).toMatchObject({ items: [{ id: "100:1" }, { id: "100:2" }] });
    expect(round.items[2].emphasis).toBe("upgrade");
    expect(round.items[4].auxiliary).toBe(false);
    expect(round.items[4].entry.status).toBe("failed");
});

it("回合边界保留无操作、进行中与回滚尝试，只使用各自已记录的正式分", () => {
    const payload = data([row("purchase_upgrade", { lifecycle: "rolled_back" }), row("end_turn", { id: "200:1", attempt: 200 })]);
    payload.attempts = [
        { id: 100, turn: 1, attempt: 1, status: "rolled_back", score: 8, actions: 1, at: "", updated_at: "" },
        { id: 200, turn: 1, attempt: 2, status: "completed", score: 0, actions: 1, at: "", updated_at: "" },
        { id: 300, turn: 2, attempt: 1, status: "interrupted", score: null, actions: 0, at: "", updated_at: "" },
    ];
    const rounds = timelineRounds(payload);
    expect(rounds.map(round => [round.id, round.score, round.status])).toEqual([[100, 8, "rolled_back"], [200, 0, "completed"], [300, null, "interrupted"]]);
    expect(roundTitle(rounds[1])).toBe("第 1 回合 · 尝试 2");
    expect(rounds[2].items).toEqual([]);
    expect(timelineRounds(data([row("end_turn")]))[0].score).toBeNull();
});

it("批量提交展示真实子动作与其中的全局升级，但子动作仍定位同一个提交游标", () => {
    const original = row("execute_plan", { event: { type: "tool_result", name: "execute_plan", result: { ok: true, already_executed: false,
        _action_output: { global_upgrades: [{ upgrade_id: "training", name: "训练场", description: "全体成员攻击提升", stats: { attack: 2, defense: 0 }, party_size_bonus: 1 }] },
        events: [
            { type: "preparation_applied", action: { type: "craft", recipe_id: "sword" }, summary: "制作铁剑" },
            { type: "preparation_applied", action: { type: "purchase_upgrade", upgrade_id: "training" }, summary: "购买训练场", changes: [{ kind: "resource", label: "金币", before: 100, after: 70 }] },
        ] } } });
    const items = timelineItems(original);
    expect(items.map(item => item.entry.event.name)).toEqual(["craft_equipment", "purchase_upgrade"]);
    expect(items[1].emphasis).toBe("upgrade");
    expect(items[1].planStep).toEqual({ index: 2, total: 2 });
    expect(items.every(item => item.row === original)).toBe(true);
    expect(upgradeDetails(items[1])).toEqual(["全体成员攻击提升", "全队属性：攻击 +2", "编队容量 +1"]);
    expect(items[1].entry.effects).toContain("金币 100 → 70（-30）");

    const repeated = timelineItems({ ...original, event: { ...original.event, result: { ...original.event.result, already_executed: true } } });
    expect(repeated).toHaveLength(1);
    expect(repeated[0].auxiliary).toBe(true);
    const failed = timelineItems({ ...original, event: { ...original.event, result: { ...original.event.result, ok: false } } });
    expect(failed).toHaveLength(1);
    expect(failed[0].entry.status).toBe("failed");
});

it("试算不被展开为已执行方案，失败升级不展示假定收益", () => {
    const preview = row("preview_team_power", { event: { type: "tool_result", name: "preview_team_power", result: {
        ok: true, simulation_only: true, projected_rank_score: 20, events: [{ action: { type: "purchase_upgrade" } }],
    } } });
    expect(timelineItems(preview)).toHaveLength(1);
    expect(timelineItems(preview)[0].auxiliary).toBe(true);
    expect(upgradeDetails(timelineItems(row("purchase_upgrade", { event: { type: "tool_result", name: "purchase_upgrade", result: {
        ok: false, _action_output: { global_upgrades: [{ description: "不能当成成功" }] },
    } } }))[0])).toEqual([]);
});

it("主表每回合只占一行，选择最近未回滚尝试，旧尝试不混入操作和收益", () => {
    const payload = data([
        row("purchase_upgrade", { lifecycle: "rolled_back", event: { type: "tool_result", name: "purchase_upgrade", result: { ok: true, event: { summary: "购买旧天赋" } } } }),
        row("get_party", { id: "200:1", attempt: 200 }),
        row("recruit_adventurer", { id: "200:2", attempt: 200 }),
        row("end_turn", { id: "200:3", attempt: 200, event: { type: "tool_result", name: "end_turn", result: { ok: true, turn_result: { battles: [] } } } }),
    ]);
    payload.attempts = [
        { id: 100, turn: 1, attempt: 1, status: "rolled_back", score: 8, actions: 1, at: "", updated_at: "" },
        { id: 200, turn: 1, attempt: 2, status: "completed", score: 0, actions: 3, at: "", updated_at: "" },
    ];
    const turns = timelineTurns(timelineRounds(payload));
    expect(turns).toHaveLength(1);
    expect(turns[0].attempts).toHaveLength(2);
    expect(turns[0].primary.id).toBe(200);
    expect(turnSummary(turns[0])).toMatchObject({ upgrades: [], operationText: "招募×1", auxiliary: 1, endText: "0胜0负" });
    expect(turnSummary(turns[0]).battle!.gold).toBe(0);
});

it("战力变化只比较相邻的已完成有效回合，保留零分、下降且不跨缺失记录", () => {
    const payload = data([row("preview_team_power", { attempt: 2, turn: 2, event: { type: "tool_result", name: "preview_team_power", result: { ok: true, projected_rank_score: 9999, rank_score_delta: 8888 } } })]);
    payload.attempts = [0, 12, 7.5, null, 30].map((score, index) => ({
        id: index + 1, turn: index + 1, attempt: 1, status: "completed", score, actions: 1, at: "", updated_at: "",
    }));
    const turns = timelineTurns(timelineRounds(payload));
    expect(turns.map(turn => turn.rankScoreDelta)).toEqual([null, 12, -4.5, null, null]);
    expect(turns[1].previousRankScore).toBe(0);

    payload.attempts = [
        { id: 1, turn: 1, attempt: 1, status: "completed", score: 10, actions: 1, at: "", updated_at: "" },
        { id: 9, turn: 2, attempt: 1, status: "rolled_back", score: 900, actions: 1, at: "", updated_at: "" },
        { id: 2, turn: 2, attempt: 2, status: "completed", score: 10, actions: 1, at: "", updated_at: "" },
        { id: 3, turn: 3, attempt: 1, status: "running", score: 80, actions: 1, at: "", updated_at: "" },
        { id: 4, turn: 4, attempt: 1, status: "completed", score: 90, actions: 1, at: "", updated_at: "" },
    ];
    expect(timelineTurns(timelineRounds(payload)).map(turn => turn.rankScoreDelta)).toEqual([null, 0, null, null]);
    const gap = timelineTurns(timelineRounds({ ...payload, entries: [], attempts: [payload.attempts[0], payload.attempts[4]] }));
    expect(gap[1].rankScoreDelta).toBeNull();
});

it("经验获得量仅取结算奖励，不抵扣培养支出，重复执行方案不重复累计", () => {
    const plan = row("execute_plan", { event: { type: "tool_result", name: "execute_plan", result: { ok: true, already_executed: false, events: [
        { action: { type: "allocate_experience", adventurer_id: "hero", amount: 100 }, summary: "分配经验", changes: [{ kind: "resource", label: "经验池", before: 100, after: 0 }] },
    ] } } });
    const ended = row("end_turn", { id: "100:3", event: { type: "tool_result", name: "end_turn", result: { ok: true,
        event: { changes: [{ kind: "resource", label: "经验池", before: 0, after: 149 }] },
        turn_result: { battles: [{ won: true, reward: { gold: 1, experience: 149, materials: {} } }] },
    } } });
    const repeated = { ...plan, id: "100:2", event: { ...plan.event, result: { ...plan.event.result, already_executed: true } } };
    const turn = timelineTurns(timelineRounds(data([plan, repeated, ended])))[0];
    expect(turnSummary(turn).experienceGained).toBe(149);
    expect(turnSummary(turn).battle!.experience).toBe(149);

    plan.event.result.events[0].changes[0].before = 200;
    expect(turnSummary(timelineTurns(timelineRounds(data([plan, ended])))[0]).experienceGained).toBe(149);
    plan.event.result.events[0].changes[0].before = 149;
    expect(turnSummary(timelineTurns(timelineRounds(data([plan, ended])))[0]).experienceGained).toBe(149);
});

it("经验获得量不依赖变化清单，缺失奖励、未结算和回滚不当零，明确零奖励正常展示", () => {
    const ended = row("end_turn", { event: { type: "tool_result", name: "end_turn", result: { ok: true,
        event: { changes: [] }, turn_result: { battles: [{ won: true, reward: { experience: 99 } }] },
    } } });
    const summarize = (entries: TrajectoryRow[]) => turnSummary(timelineTurns(timelineRounds(data(entries)))[0]);
    expect(summarize([ended]).experienceGained).toBe(99);
    expect(summarize([row("allocate_experience"), ended]).experienceGained).toBe(99);
    ended.event.result.event.changes = [{ kind: "resource", label: "经验池", after: 99 }];
    expect(summarize([ended]).experienceGained).toBe(99);
    expect(summarize([row("get_party")]).experienceGained).toBeNull();
    delete ended.event.result.turn_result.battles[0].reward.experience;
    expect(summarize([ended]).experienceGained).toBeNull();
    ended.event.result.turn_result.battles = [];
    expect(summarize([ended]).experienceGained).toBe(0);
    const rolled = timelineTurns(timelineRounds({ ...data([ended]), attempts: [{ id: 100, turn: 1, attempt: 1, status: "rolled_back", score: 100, actions: 1, at: "", updated_at: "" }] }))[0];
    expect(turnSummary(rolled).experienceGained).toBeNull();
});

it("分栏由可用宽高决定，35回合在足够空间下完整铺开且保持逐列顺序", () => {
    const turns = timelineTurns(timelineRounds(data(Array.from({ length: 35 }, (_, index) => row("end_turn", {
        id: `${index + 1}:1`, attempt: index + 1, turn: index + 1,
    })))));
    const columns = timelineColumnCount(1200, 420, turns.length);
    expect(columns).toBe(2);
    const bands = timelineBands(turns, columns);
    expect(bands.map(band => band.length)).toEqual([18, 17]);
    expect(bands.flat().map(turn => turn.turn)).toEqual(Array.from({ length: 35 }, (_, index) => index + 1));
    expect(Math.max(...bands.map(band => band.length)) * timelineRowHeight(420, 35, columns) + 22).toBeLessThanOrEqual(420);
    expect(timelineRowHeight(382, 35, 2)).toBe(20);
    expect(timelineRowHeight(457, 35, 2)).toBe(24);
    expect(timelineRowHeight(0, 0, 1)).toBe(20);
    expect(timelineColumnCount(1200, 550, 35)).toBe(2);
    expect(timelineColumnCount(1016, 382, 35)).toBe(2);
    expect(timelineColumnCount(390, 600, 35)).toBe(1);
});

it("结算用明确战果和奖励，部分缺失不当零，零场讨伐与明确零奖励仍成立", () => {
    const item = (result: Record<string, unknown>) => timelineItems(row("end_turn", { event: { type: "tool_result", name: "end_turn", result: { ok: true, ...result } } }))[0];
    const ended = settlement(item({ turn_result: { battles: [
        { adventurer_name: "战士", monster_name: "史莱姆", won: true, reward: { gold: 10, experience: 4, materials: { iron_ore: 1 } } },
        { adventurer_name: "弓手", monster_name: "首领", won: false, reward: { gold: 0, experience: 0, materials: {} } },
        { adventurer_name: "神官", monster_name: "未知敌人", reward: { gold: 5 } },
    ] } }));
    expect(ended).toMatchObject({ wins: 1, losses: 1, unknown: 1, gold: 15, experience: null, materials: null });
    expect(ended!.battles[2].result).toBe("结果未记录");
    expect(settlement(item({}))).toBeNull();
    expect(settlement(item({ turn_result: { battles: [] } }))).toMatchObject({ battles: [], gold: 0, experience: 0, materials: [] });
    expect(settlement(timelineItems(row("end_turn", { event: { type: "tool_result", name: "end_turn", result: { ok: false, turn_result: { battles: [] } } } }))[0])).toBeNull();
});
