import { expect, it } from "vitest";
import { actionEntries, actionGroups, actionSummary, recentActions } from "./actionModel";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchRecord } from "./watchModel";

const before: Observation = {
    gold: 175, experience_pool: 100, materials: { iron_ore: 3 }, turn: 1, max_turns: 8, party_size_limit: 3,
    adventurers: [{ adventurer_id: "a1", name: "验收剑士", template_id: "warrior", level: 1,
        effective_stats: { hp: 100, mp: 0, attack: 12 }, resources: { current_hp: 100, current_mp: 0 } }],
    equipment_inventory: [{ instance_id: "sword", name: "验收铁剑", equipped_by: null }],
    global_upgrades: [{ upgrade_id: "training", name: "验收训练场", unlocked: false }],
};
const after: Observation = { ...before, gold: 110, materials: { iron_ore: 1 },
    adventurers: before.adventurers.map(member => ({ ...member, effective_stats: { ...member.effective_stats, attack: 15 } })),
    global_upgrades: [{ upgrade_id: "training", name: "验收训练场", unlocked: true }],
};
const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-10-19T10:00:00Z", event });
const initial = rec(1, { type: "turn_started", observation: before, turn: 1 });
const purchase = rec(3, { type: "tool_result", name: "purchase_upgrade", call_id: "buy", arguments: { upgrade_id: 5 },
    result: { ok: true, _observation_after: after, event: { summary: "购买全局加成 验收训练场", changes: [
        { kind: "resource", label: "金币", before: 175, after: 110 },
        { kind: "material", label: "iron_ore", before: 3, after: 1 },
        { kind: "adventurer", label: "验收剑士 攻击", before: 12, after: 15 },
    ] } } });

it("购买动作保留具体升级名称，资源消耗与成员属性变化单独展示，不再只剩金币差值", () => {
    const entries = actionEntries(watchFrames([initial, purchase]));
    expect(entries[1]).toMatchObject({ kind: "action", title: "购买全局加成 验收训练场", status: "succeeded" });
    expect(entries[1].effects).toEqual(["金币 175 → 110（-65）", "铁矿石 3 → 1（-2）", "验收剑士 攻击 12 → 15（+3）"]);
    expect(actionSummary(entries[1])).toContain("攻击 12 → 15");
});

it("调用和结果按 call_id 合并，返回前显示执行中，回看不会提前看到之后的结果", () => {
    const call = rec(2, { type: "tool_call", name: "purchase_upgrade", call_id: "buy", arguments: { upgrade_id: "training" } });
    const frames = watchFrames([initial, call, purchase]);
    expect(actionEntries(frames.slice(0, 2))[1]).toMatchObject({ status: "pending", frame: { cursor: 2 } });
    const entries = actionEntries(frames);
    expect(entries).toHaveLength(2);
    expect(entries[1]).toMatchObject({ start: 2, frame: { cursor: 3 }, status: "succeeded" });
    expect(frames).toHaveLength(3);
});

it("重复工具名不混淆调用，不同 call_id 的失败结果不能覆盖另一次成功操作", () => {
    const frames = watchFrames([initial,
        rec(2, { type: "tool_call", name: "equip_item", call_id: "a" }),
        rec(3, { type: "tool_result", name: "equip_item", call_id: "a", result: { ok: false, error: "职业不匹配" } }),
        rec(4, { type: "tool_call", name: "equip_item", call_id: "b" }),
        rec(5, { type: "tool_result", name: "equip_item", call_id: "b", result: { ok: true, event: { summary: "验收剑士 装备 验收铁剑" } } }),
    ]);
    const entries = actionEntries(frames).filter(entry => entry.kind === "action");
    expect(entries.map(entry => entry.status)).toEqual(["failed", "succeeded"]);
    expect(entries[0].summary).toBe("职业不匹配");
    expect(entries[0].effects).toEqual([]);
    expect(entries[1].title).toBe("验收剑士 装备 验收铁剑");
});

it("运行事件单独收起，不打断连续查询，失败不被通用拒绝摘要盖掉对象", () => {
    const frames = watchFrames([initial,
        rec(2, { type: "tool_result", name: "get_party", result: { ok: true, adventurers: before.adventurers } }),
        rec(3, { type: "model_request", step: 2 }),
        rec(4, { type: "model_response", step: 2, text: "构造响应" }),
        rec(5, { type: "tool_result", name: "get_party", result: { ok: true, adventurers: before.adventurers } }),
        rec(6, { type: "tool_result", name: "equip_item", arguments: { adventurer_id: "a1", equipment_id: "sword" },
            result: { ok: false, error: "职业不匹配", event: { summary: "动作被拒绝" } } }),
    ]);
    const groups = actionGroups(actionEntries(frames));
    expect(groups.map(group => [group.kind, group.entries.length])).toEqual([["query", 2], ["action", 1], ["system", 3]]);
    expect(groups[1].entries[0]).toMatchObject({ title: "验收剑士 装备 验收铁剑", summary: "职业不匹配" });
});

it("旧记录没有 call_id 时仍按顺序配对，有结果无调用也能独立显示", () => {
    const entries = actionEntries(watchFrames([initial,
        rec(2, { type: "tool_call", name: "get_party" }),
        rec(3, { type: "tool_result", name: "get_party", result: { ok: true, adventurers: before.adventurers } }), purchase,
    ]));
    expect(entries.filter(entry => entry.kind === "query")).toHaveLength(1);
    expect(entries[1].summary).toBe("返回 1 位成员：验收剑士");
    expect(entries[2].title).toContain("验收训练场");
});

it("查询只能连续折叠，不跨经营动作合并，也不会挤掉最近一次真实经营动作", () => {
    const query = (cursor: number) => rec(cursor, { type: "tool_result", name: "get_party", result: { ok: true, adventurers: before.adventurers } });
    const frames = watchFrames([initial, query(2), purchase, query(4), query(5)]);
    expect(actionGroups(actionEntries(frames)).map(group => [group.kind, group.entries.length])).toEqual([["query", 1], ["action", 1], ["query", 2], ["system", 1]]);
    expect(recentActions(frames).actions[0].title).toContain("验收训练场");
    expect(recentActions(frames).queries).toHaveLength(3);
});

it("只有查询时明确没有经营动作，试算战斗不会被当成真实获胜", () => {
    const result = recentActions(watchFrames([initial, rec(2, { type: "tool_result", name: "preview_battle", result: { ok: true,
        preview: { adventurer_name: "验收剑士", monster_name: "验收史莱姆", won: true } } })]));
    expect(result.actions).toEqual([]);
    expect(result.queries[0].summary).toBe("验收剑士 对 验收史莱姆：模拟胜利，不是实际讨伐");
});

it("查询失败直接显示错误但仍不算经营动作，技术重试也不会被算成经营成果", () => {
    const recent = recentActions(watchFrames([initial,
        rec(2, { type: "tool_result", name: "get_party", result: { ok: false, error: "构造查询失败" } }),
        rec(3, { type: "retry", reason: "empty_response" }),
    ]));
    expect(recent.queryOnly).toBe(true);
    expect(actionGroups(recent.entries).find(group => group.entries.some(entry => entry.frame.cursor === 2))!.kind).toBe("action");
    expect(recent.actions.some(entry => entry.summary === "构造查询失败")).toBe(true);
});

it("结束回合显示真实派遣双方和胜负，零场战斗不伪造战绩", () => {
    const describe = (battles: object[]) => actionEntries(watchFrames([initial, rec(2, { type: "tool_result", name: "end_turn", result: { ok: true, turn_result: { battles } } })]))[1];
    expect(describe([{ adventurer_name: "验收剑士", monster_name: "验收史莱姆", won: true }]).summary).toContain("验收剑士 → 验收史莱姆：胜利");
    expect(describe([]).summary).toBe("本回合没有安排讨伐");
});

it("旧事件缺少中文摘要时用真实对象和引用表还原目标，不把短编号误当成对象 ID", () => {
    const entry = actionEntries(watchFrames([initial, rec(2, { type: "tool_result", name: "equip_item", arguments: { adventurer_id: 1, equipment_id: 2 },
        result: { ok: false, error: "本回合无法换装", _llm_refs: { adventurer: { a1: 1 }, equipment: { sword: 2 } } } })]))[1];
    expect(entry.title).toBe("验收剑士 装备 验收铁剑");
    expect(entry.summary).toBe("本回合无法换装");
});

it("没有服务端变化清单时比较实际快照的属性与天赋，缺快照不伪造变化", () => {
    const entry = actionEntries(watchFrames([initial, rec(2, { type: "tool_result", name: "purchase_upgrade", result: { ok: true, _observation_after: after } })]))[1];
    expect(entry.title).toBe("购买升级 验收训练场");
    expect(entry.effects).toContain("验收剑士 攻击 12 → 15（+3）");
    expect(entry.effects).toContain("解锁 验收训练场");
    expect(actionEntries(watchFrames([rec(1, { type: "tool_result", name: "equip_item", result: { ok: true } })]))[0].effects).toEqual([]);
});

it("战术事件没有变化清单时仍展示真实的技能顺序和停用项，并翻译已记录技能名", () => {
    const old: Observation = { ...before, adventurers: before.adventurers.map(member => ({ ...member,
        skills: [{ skill_id: "slash", name: "验收重斩" }], tactics: { skill_priority: [], disabled_skills: [] } })) };
    const changed: Observation = { ...old, adventurers: old.adventurers.map(member => ({ ...member,
        tactics: { skill_priority: ["slash"], disabled_skills: ["slash"] } })) };
    const entry = actionEntries(watchFrames([rec(1, { type: "turn_started", observation: old }), rec(2, {
        type: "tool_result", name: "configure_tactics", result: { ok: true, _observation_after: changed,
            event: { summary: "验收剑士 停用 slash", changes: [] } },
    })]))[1];
    expect(entry.title).toBe("验收剑士 停用 验收重斩");
    expect(entry.effects).toContain("验收剑士 技能顺序：验收重斩");
    expect(entry.effects).toContain("验收剑士 停用技能：验收重斩");
});

it("旧战斗没有胜负字段时标为未知，不把缺字段当成战败", () => {
    const entry = actionEntries(watchFrames([initial, rec(2, { type: "tool_result", name: "end_turn", result: { ok: true,
        turn_result: { battles: [{ adventurer_name: "验收剑士", monster_name: "验收史莱姆" }] } } })]))[1];
    expect(entry.summary).toContain("结果未记录");
    expect(entry.summary).not.toContain("失败");
});

it("全局方案试算不伪造战力，凭据执行与重复提交展示不同结果", () => {
    const entries = actionEntries(watchFrames([initial,
        rec(2, { type: "tool_result", name: "preview_team_power", result: {
            ok: true, simulation_only: true, operation_count: 2, plan_id: "p1", required_calls: 2, remaining_calls: 3, can_execute: true,
        } }),
        rec(3, { type: "tool_result", name: "execute_plan", result: { ok: true, operation_count: 2, already_executed: false } }),
        rec(4, { type: "tool_result", name: "execute_plan", result: { ok: true, operation_count: 2, already_executed: true } }),
    ]));
    expect(entries[1]).toMatchObject({ kind: "query", title: "方案试算" });
    expect(entries[1].summary).toContain("不估算战力");
    expect(entries[1].summary).not.toContain("未知");
    expect(entries[2]).toMatchObject({ kind: "action", title: "执行方案" });
    expect(entries[2].summary).toContain("已执行 2 步方案，不扣调用预算");
    expect(entries[3].summary).toContain("本次未重复执行");
});

it("回滚保留当时的成功事实，但所有本次经营动作都标记不再生效", () => {
    const entries = actionEntries(watchFrames([initial, purchase, rec(4, { type: "turn_retry", reason: "构造回滚原因" })]));
    expect(entries[1]).toMatchObject({ status: "succeeded", rolledBack: true });
    expect(entries[0].rolledBack).toBe(false);
});
