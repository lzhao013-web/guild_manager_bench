import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { ActionResults } from "./ActionResultPanel";
import { actionEntries } from "./actionModel";
import { watchFrames } from "./watchModel";
import type { Observation, PartyMember } from "./types";
import type { WatchRecord } from "./watchModel";

const hero: PartyMember = { adventurer_id: "hero", name: "验收剑士", template_id: "mercenary_warrior", level: 2,
    resources: { current_hp: 80, current_mp: 0 }, effective_stats: { hp: 100, mp: 0, attack: 10, defense: 2 }, skills: [],
    equipment_slots: [{ slot: "main_hand", item: { instance_id: "old", name: "旧剑" } }] };
const before: Observation = { turn: 2, max_turns: 35, gold: 100, experience_pool: 40, materials: {}, party_size_limit: 3, adventurers: [hero] };
const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-11-13T10:00:00Z", event });
function action(name: string, result: Record<string, unknown>, args: Record<string, unknown> = {}, observation = before) {
    return actionEntries(watchFrames([rec(1, { type: "turn_started", observation }), rec(2, { type: "tool_result", name, arguments: args, result })]))[1];
}

it("卸下装备展示槽位前后值和负向属性变化，保留零值与移除技能，完整返回收到底部", async () => {
    const skilled = { ...hero, skills: [{ skill_id: "gear_slash", name: "装备斩击" }] };
    const after = { ...hero, effective_stats: { ...hero.effective_stats, attack: 0 }, equipment_slots: [{ slot: "main_hand", item: null }] };
    const entry = action("unequip_item", { ok: true, _observation_after: { ...before, adventurers: [after] },
        _action_output: { adventurers: [after], equipment_inventory: [{ instance_id: "old", name: "旧剑", equipped_by: null }] },
    }, { adventurer_id: "hero", slot: "main_hand" }, { ...before, adventurers: [skilled] });
    render(<ActionResults entry={entry} />);
    expect(screen.queryByRole("region", { name: "执行结果" })).not.toBeInTheDocument();
    const changes = screen.getByRole("region", { name: "实际成员变化" });
    expect(within(changes).getByText("主手：旧剑")).toBeVisible();
    expect(within(changes).getByText("主手：空槽位")).toBeVisible();
    const attack = within(changes).getByRole("row", { name: /攻击/ });
    expect(within(attack).getByRole("cell", { name: "10" })).toBeVisible();
    expect(within(attack).getByRole("cell", { name: "0" })).toBeVisible();
    expect(within(attack).getByRole("cell", { name: "-10" })).toBeVisible();
    expect(changes).toHaveTextContent("移除技能装备斩击");
    expect(within(changes).queryByText("当前生命")).not.toBeInTheDocument();
    const inspection = screen.getByRole("region", { name: "排查详情" });
    expect(inspection).toContainElement(screen.getByText("完整角色字段"));
    expect(inspection).toContainElement(screen.getByText("操作后的装备完整字段"));
    expect(screen.queryByText("hero", { exact: true })).not.toBeInTheDocument();
    fireEvent.click(within(inspection).getByText("完整角色字段"));
    expect(await within(inspection).findByText("hero", { exact: true })).toBeVisible();
    expect(screen.getAllByRole("region", { name: "实际成员变化" })).toHaveLength(1);
});

it("经验分配突出实际消耗、升级与属性增长，跨级经验结转不被标为经验损失", () => {
    const after = { ...hero, level: 3, experience: 20, effective_stats: { ...hero.effective_stats, hp: 120, attack: 13 },
        resources: { current_hp: 100, current_mp: 0 }, skills: [{ skill_id: "power_strike", name: "强力打击" }] };
    const entry = action("allocate_experience", { ok: true, _action_output: { adventurers: [after] },
        _observation_after: { ...before, experience_pool: 10, adventurers: [after] }, event: { changes: [
            { kind: "resource", label: "经验池", before: 40, after: 10 },
            { kind: "adventurer", label: "验收剑士 等级", after: 3 },
            { kind: "adventurer", label: "验收剑士 经验", before: 90, after: 20 },
        ] },
    }, { adventurer_id: "hero", amount: 30 });
    render(<ActionResults entry={entry} />);
    expect(screen.getByRole("region", { name: "实际资源变化" })).toHaveTextContent("经验池-3040 → 10");
    const changes = screen.getByRole("region", { name: "实际成员变化" });
    expect(changes).toHaveTextContent("Lv.2 → 3");
    expect(within(changes).getByRole("row", { name: /攻击/ })).toHaveTextContent("+3");
    expect(within(changes).getByRole("row", { name: /生命上限/ })).toHaveTextContent("+20");
    const experience = within(changes).getByRole("row", { name: /经验进度/ });
    expect(within(experience).getByRole("cell", { name: "90" })).toBeVisible();
    expect(within(experience).getByRole("cell", { name: "20" })).toBeVisible();
    expect(experience).toHaveTextContent("跨级结转");
    expect(experience).not.toHaveTextContent("-70");
    expect(changes).toHaveTextContent("获得技能强力打击");
    expect(screen.queryByRole("region", { name: "操作后的成员" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "执行结果" })).not.toBeInTheDocument();
});

it("没有事件清单但保留前后快照时，未升级的经验增长仍可见，不把当前等级当成升级结果", () => {
    const after = { ...hero, experience: 20 };
    const entry = action("allocate_experience", { ok: true, _action_output: { adventurers: [after] },
        _observation_after: { ...before, experience_pool: 30, adventurers: [after] },
    }, { adventurer_id: "hero", amount: 10 }, { ...before, adventurers: [{ ...hero, experience: 10 }] });
    render(<ActionResults entry={entry} />);
    const changes = screen.getByRole("region", { name: "实际成员变化" });
    expect(changes).toHaveTextContent("Lv.2");
    expect(changes).not.toHaveTextContent("Lv.2 →");
    expect(within(changes).getByRole("row", { name: /经验进度/ })).toHaveTextContent("+10");
    expect(within(changes).queryByText("跨级结转")).not.toBeInTheDocument();
});

it("缺失历史属性时明确标为未记录，不从输入推算收益，失败仍保留原因", () => {
    const entry = action("equip_item", { ok: true, event: { changes: [{ kind: "adventurer", label: "验收剑士 攻击", after: 0 }] } }, { adventurer_id: "hero" });
    const { rerender } = render(<ActionResults entry={entry} />);
    const changes = screen.getByRole("region", { name: "实际成员变化" });
    const attack = within(changes).getByRole("row", { name: /攻击/ });
    expect(within(attack).getAllByRole("cell", { name: "未记录" })).toHaveLength(2);
    expect(within(attack).getByRole("cell", { name: "0" })).toBeVisible();
    rerender(<ActionResults entry={action("allocate_experience", { ok: true }, { adventurer_id: "hero", amount: 30 })} />);
    expect(screen.getByRole("region", { name: "实际成员变化" })).toHaveTextContent("实际成员变化未记录");
    expect(screen.queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    const [legacy] = actionEntries(watchFrames([rec(3, { type: "tool_result", name: "equip_item", content: "旧日志：铁剑已交给验收剑士", result: { ok: true } })]));
    rerender(<ActionResults entry={legacy} />);
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent("旧日志：铁剑已交给验收剑士");
    rerender(<ActionResults entry={action("equip_item", { ok: false, error: "职业不匹配" }, { adventurer_id: "hero" })} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("职业不匹配");
    expect(screen.queryByRole("region", { name: "实际成员变化" })).not.toBeInTheDocument();
});
