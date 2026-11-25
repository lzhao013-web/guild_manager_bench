import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { ActionResults } from "./ActionResultPanel";
import { actionEntries } from "./actionModel";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchRecord } from "./watchModel";

const before: Observation = { turn: 3, max_turns: 35, gold: 100, experience_pool: 80, materials: { bone: 2 }, party_size_limit: 3,
    adventurers: [{ adventurer_id: "hero", name: "验收剑士", template_id: "mercenary_warrior", level: 1,
        resources: { current_hp: 100, current_mp: 0 }, effective_stats: { hp: 100, mp: 0, attack: 10 } }],
    equipment_inventory: [{ instance_id: "old-sword", name: "旧库存铁剑", stats: { attack: 99 } }],
    global_upgrades: [{ upgrade_id: "old-talent", name: "旧天赋", unlocked: true, stats: { defense: 99 } }] };
const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-11-13T10:00:00Z", event });
function entry(name: string, result: Record<string, unknown>) {
    return actionEntries(watchFrames([rec(1, { type: "turn_started", observation: before }), rec(2, { type: "tool_result", name, arguments: {}, result })]))[1];
}

it("制作详情突出本次成品、实际消耗、属性和技能，不重复铺开获得装备提示，原始字段在底部可查", async () => {
    const equipment = { instance_id: "new-sword", template_id: "t1_heavy_sword", name: "新制铁剑", slot: "main_hand", stats: { attack: 7, mp: 0 },
        equipped_by: null, allowed_class_names: ["佣兵战士"], skills: [{ skill_id: "power_strike", name: "强力打击" }] };
    const action = entry("craft_equipment", { ok: true, _action_output: { equipment_inventory: [equipment] }, event: { changes: [
        { kind: "resource", label: "金币", before: 100, after: 75 },
        { kind: "material", label: "bone", before: 2, after: 1 },
        { kind: "equipment", label: "获得装备", after: "新制铁剑 (new-sword)" },
    ] }, receipt: "已保存的制作回执" });
    render(<ActionResults entry={action} recordDetails={<details><summary>原始事件与调用参数</summary><pre>制作记录</pre></details>} />);
    expect(screen.queryByRole("region", { name: "执行结果" })).not.toBeInTheDocument();
    const resources = screen.getByRole("region", { name: "实际资源变化" });
    expect(resources).toHaveTextContent("金币-25100 → 75");
    expect(resources).toHaveTextContent("骨头-12 → 1");
    const result = screen.getByRole("region", { name: "实际制作出的装备" });
    expect(result).toHaveTextContent("新制铁剑");
    expect(result).toHaveTextContent("T1");
    expect(result).toHaveTextContent("主手");
    expect(result).toHaveTextContent("未穿戴");
    expect(within(result).getByText("攻击").parentElement).toHaveTextContent("攻击7");
    expect(result).toHaveTextContent("强力打击");
    expect(result).toHaveTextContent("职业限制佣兵战士");
    expect(result).not.toHaveTextContent("旧库存铁剑");
    expect(result).not.toHaveTextContent("1 条");
    expect(result).not.toHaveTextContent("获得装备");
    const inspection = screen.getByRole("region", { name: "排查详情" });
    expect(inspection).toContainElement(screen.getByText("原始事件与调用参数"));
    expect(inspection).toContainElement(screen.getByRole("region", { name: "输入参数" }));
    expect(screen.queryByText("new-sword", { exact: true })).not.toBeInTheDocument();
    fireEvent.click(within(inspection).getByText("完整装备字段"));
    expect(await within(inspection).findByText("new-sword", { exact: true })).toBeVisible();
    fireEvent.click(within(inspection).getByText("其他返回字段"));
    expect(await within(inspection).findByText("已保存的制作回执")).toBeVisible();
    expect(screen.getAllByRole("region", { name: "实际资源变化" })).toHaveLength(1);
});

it("天赋详情区分直接属性与经济、容量加成，实际成员收益按需查看，费用不重复展示", async () => {
    const talent = { upgrade_id: "guild-training", name: "公会训练", description: "强化公会的战斗与经营能力。", unlocked: true,
        branch: "guild", tier: 2, stats: { attack: 3, hp: 0 }, party_size_bonus: 1, craft_gold_discount_percent: 10,
        hunt_experience_bonus_percent: 20, hunt_gold_bonus_percent: 7.5, elite_boss_gold_bonus_percent: 15, monster_refresh_discount_percent: 5,
        gold_cost: 70, experience_cost: 20, skills: [{ skill_id: "training_aura", name: "训练光环" }] };
    const after = { ...before, gold: 30, experience_pool: 60, party_size_limit: 4,
        adventurers: [{ ...before.adventurers[0], effective_stats: { hp: 100, mp: 0, attack: 13 } }] };
    const action = entry("purchase_upgrade", { ok: true, _action_output: { global_upgrades: [talent] }, _observation_after: after });
    render(<ActionResults entry={action} />);
    const resources = screen.getByRole("region", { name: "实际资源变化" });
    expect(resources).toHaveTextContent("金币-70100 → 30");
    expect(resources).toHaveTextContent("经验池-2080 → 60");
    expect(resources).not.toHaveTextContent("队伍人数上限");
    const result = screen.getByRole("region", { name: "本次已解锁天赋" });
    expect(within(result).getByText("攻击").parentElement).toHaveTextContent("攻击+3");
    expect(within(result).getByText("编队容量").parentElement).toHaveTextContent("编队容量+1");
    expect(within(result).getByText("制作金币减免").parentElement).toHaveTextContent("10%");
    expect(within(result).getByText("讨伐经验加成").parentElement).toHaveTextContent("20%");
    expect(within(result).getByText("讨伐金币加成").parentElement).toHaveTextContent("7.5%");
    expect(within(result).getByText("精英、首领金币额外加成").parentElement).toHaveTextContent("15%");
    expect(within(result).getByText("刷新基础价减免").parentElement).toHaveTextContent("5%");
    expect(result).toHaveTextContent("训练光环");
    expect(result).not.toHaveTextContent("旧天赋");
    expect(result).not.toHaveTextContent("金币费用");
    expect(screen.queryByRole("region", { name: "执行结果" })).not.toBeInTheDocument();
    const inspection = screen.getByRole("region", { name: "排查详情" });
    const members = within(inspection).getByRole("region", { name: "实际成员变化", hidden: true });
    expect(members).not.toBeVisible();
    fireEvent.click(within(inspection).getByText("对现有成员的影响（1 位）"));
    expect(members).toBeVisible();
    expect(members).toHaveTextContent("验收剑士");
    expect(members).toHaveTextContent("攻击+3");
    fireEvent.click(within(inspection).getByText("完整天赋字段"));
    expect(await within(inspection).findByText("guild-training")).toBeVisible();
});

it("没有记录产物或实际费用时不拿库存、候选天赋与标价冒充本次成果", () => {
    const { rerender } = render(<ActionResults entry={entry("craft_equipment", { ok: true, equipment_inventory: before.equipment_inventory })} />);
    expect(screen.getByRole("region", { name: "返回的装备库存" })).toHaveTextContent("旧库存铁剑");
    expect(screen.queryByRole("heading", { name: "制作成品" })).not.toBeInTheDocument();
    rerender(<ActionResults entry={entry("purchase_upgrade", { ok: true, global_upgrades: before.global_upgrades })} />);
    expect(screen.queryByRole("heading", { name: "本次解锁" })).not.toBeInTheDocument();
    rerender(<ActionResults entry={entry("purchase_upgrade", { ok: true, _action_output: { global_upgrades: [
        { upgrade_id: "capacity", name: "扩编", unlocked: true, gold_cost: 100, stats: {}, party_size_bonus: 1 },
    ] } })} />);
    expect(screen.queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    const result = screen.getByRole("region", { name: "本次已解锁天赋" });
    expect(result).toHaveTextContent("编队容量+1");
    expect(result).not.toHaveTextContent("100");
    expect(result).not.toHaveTextContent("效果明细未记录");
});

it("失败不展示成品，旧返回的零属性与未知穿戴者仍明确显示", () => {
    const equipment = { instance_id: "zero", name: "旧记录护符", slot: "accessory", stats: { attack: 0 } };
    const { rerender } = render(<ActionResults entry={entry("craft_equipment", { ok: true, _action_output: { equipment_inventory: [equipment] } })} />);
    const result = screen.getByRole("region", { name: "实际制作出的装备" });
    expect(within(result).getByText("攻击").parentElement).toHaveTextContent("攻击0");
    expect(result).toHaveTextContent("穿戴者未记录");
    rerender(<ActionResults entry={entry("craft_equipment", { ok: false, error: "金币不足" })} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("金币不足");
    expect(screen.queryByRole("region", { name: "实际制作出的装备" })).not.toBeInTheDocument();
});
