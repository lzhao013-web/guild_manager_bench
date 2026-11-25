import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { ActionResults } from "./ActionResultPanel";
import { actionEntries } from "./actionModel";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchRecord } from "./watchModel";

const skills = [
    { skill_id: "fireball", name: "烈焰火球" }, { skill_id: "storm", name: "烈焰风暴" },
    { skill_id: "spark", name: "魔力爆发" }, { skill_id: "heal", name: "紧急治疗" },
    { skill_id: "blast", name: "爆燃冲击" }, { skill_id: "mana_regen", name: "自动回魔" },
];
const member = { adventurer_id: "mage", name: "烈焰法师", template_id: "pyromancer", level: 6,
    resources: { current_hp: 100, current_mp: 30 }, effective_stats: { hp: 120, mp: 40, attack: 35 }, skills,
    tactics: { skill_priority: ["fireball", "storm", "spark"], disabled_skills: ["heal", "mana_regen"], available_active_skills: skills } };
const before: Observation = { turn: 6, max_turns: 35, gold: 100, experience_pool: 80, materials: {}, party_size_limit: 3, adventurers: [member] };
const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-11-13T10:00:00Z", event });
function entry(result: Record<string, unknown>, args: Record<string, unknown> = {}, observation: Observation | null = before) {
    const records = observation ? [rec(1, { type: "turn_started", observation })] : [];
    return actionEntries(watchFrames([...records, rec(2, { type: "tool_result", name: "configure_tactics", arguments: args, result })])).at(-1)!;
}

it("战术详情使用实际返回展示优先顺序和启停差异，角色属性与原始参数收到底部", async () => {
    const after = { ...member, tactics: { skill_priority: ["storm", "fireball", "heal"], disabled_skills: ["blast", "mana_regen"], available_active_skills: skills } };
    const action = entry({ ok: true, _action_output: { adventurers: [after] }, receipt: "已保存的战术回执" },
        { adventurer_id: "mage", skill_priority: ["fireball"], disabled_skills: [] });
    render(<ActionResults entry={action} recordDetails={<details><summary>原始事件与调用参数</summary><pre>战术记录</pre></details>} />);
    const result = screen.getByRole("region", { name: "实际战术配置" });
    const order = within(result).getByRole("region", { name: "执行后的技能顺序" });
    const rows = within(order).getAllByRole("listitem").slice(0, 3);
    expect(rows[0]).toHaveTextContent("烈焰风暴原第 2 位");
    expect(rows[1]).toHaveTextContent("烈焰火球原第 1 位");
    expect(rows[2]).toHaveTextContent("紧急治疗新增优先");
    expect(order).toHaveTextContent("恢复默认优先级魔力爆发");
    const disabled = within(result).getByRole("region", { name: "执行后的停用技能" });
    expect(within(disabled).getByText("爆燃冲击").closest("li")).toHaveTextContent("本次停用");
    expect(within(disabled).getByText("自动回魔").closest("li")).not.toHaveTextContent("本次停用");
    expect(within(result).getByRole("region", { name: "本次重新启用的技能" })).toHaveTextContent("紧急治疗");
    expect(result).not.toHaveTextContent("当前生命");
    expect(result).not.toHaveTextContent("攻击35");
    expect(screen.queryByRole("region", { name: "执行结果" })).not.toBeInTheDocument();
    const inspection = screen.getByRole("region", { name: "排查详情" });
    expect(inspection).toContainElement(screen.getByRole("region", { name: "输入参数" }));
    expect(inspection).toContainElement(screen.getByText("原始事件与调用参数"));
    expect(screen.queryByText("mage", { exact: true })).not.toBeInTheDocument();
    fireEvent.click(within(inspection).getByText("完整角色字段"));
    expect(await within(inspection).findByText("mage", { exact: true })).toBeVisible();
    fireEvent.click(within(inspection).getByText("其他返回字段"));
    expect(await within(inspection).findByText("已保存的战术回执")).toBeVisible();
});

it("空列表表示默认顺序与无停用技能，停用列表只换顺序不算配置变化", () => {
    const reset = { ...member, tactics: { skill_priority: [], disabled_skills: [] } };
    const { rerender } = render(<ActionResults entry={entry({ ok: true, _action_output: { adventurers: [reset] } })} />);
    const result = () => screen.getByRole("region", { name: "实际战术配置" });
    expect(result()).toHaveTextContent("默认顺序");
    expect(result()).toHaveTextContent("无停用技能");
    expect(within(result()).getByRole("region", { name: "本次重新启用的技能" })).toHaveTextContent("紧急治疗自动回魔");
    expect(result()).not.toHaveTextContent("配置未变化");
    const same = { ...member, tactics: { ...member.tactics, disabled_skills: ["mana_regen", "heal"] } };
    rerender(<ActionResults entry={entry({ ok: true, _action_output: { adventurers: [same] } })} />);
    expect(result()).toHaveTextContent("配置未变化");
    expect(within(result()).queryByRole("region", { name: "本次重新启用的技能" })).not.toBeInTheDocument();
    expect(result()).not.toHaveTextContent("本次停用");
});

it("缺失原配置时只展示已记录结果，缺失停用字段不冒充空列表，也不按请求推算", () => {
    const partial = { ...member, tactics: { skill_priority: ["unrecorded_skill"] } };
    render(<ActionResults entry={entry({ ok: true, _action_output: { adventurers: [partial] } },
        { adventurer_id: "mage", skill_priority: ["fireball"], disabled_skills: [] }, null)} />);
    const result = screen.getByRole("region", { name: "实际战术配置" });
    expect(result).toHaveTextContent("unrecorded_skill");
    expect(result).toHaveTextContent("原顺序未记录");
    expect(result).toHaveTextContent("停用配置未记录");
    expect(result).not.toHaveTextContent("无停用技能");
    expect(result).not.toHaveTextContent("配置未变化");
    expect(result).not.toHaveTextContent("本次停用");
    expect(result).not.toHaveTextContent("烈焰火球");
});

it("旧记录缺少完整返回时使用本条保存的快照或事件变化，被停用的技能不标为恢复默认优先级", () => {
    const after = { ...member, tactics: { skill_priority: ["storm"], disabled_skills: ["fireball"] } };
    const { rerender } = render(<ActionResults entry={entry({ ok: true, _observation_after: { ...before, adventurers: [after] } }, { adventurer_id: "mage" })} />);
    let result = screen.getByRole("region", { name: "实际战术配置" });
    expect(within(result).getByRole("region", { name: "执行后的技能顺序" })).toHaveTextContent("烈焰风暴");
    expect(within(result).getByRole("region", { name: "执行后的停用技能" })).toHaveTextContent("烈焰火球本次停用");
    const order = within(result).getByRole("region", { name: "执行后的技能顺序" });
    expect(order).toHaveTextContent("恢复默认优先级魔力爆发");
    expect(order).not.toHaveTextContent("烈焰火球");
    rerender(<ActionResults entry={entry({ ok: true, event: { changes: [
        { kind: "adventurer", label: "烈焰法师 技能顺序", before: ["fireball", "storm", "spark"], after: ["storm"] },
        { kind: "adventurer", label: "烈焰法师 停用技能", before: ["heal", "mana_regen"], after: ["fireball"] },
    ] } })} />);
    result = screen.getByRole("region", { name: "实际战术配置" });
    expect(within(result).getByRole("region", { name: "执行后的技能顺序" })).toHaveTextContent("烈焰风暴原第 2 位");
    expect(within(result).getByRole("region", { name: "执行后的停用技能" })).toHaveTextContent("烈焰火球本次停用");
    rerender(<ActionResults entry={entry({ ok: true, adventurers: [after] }, { adventurer_id: "mage" })} />);
    result = screen.getByRole("region", { name: "实际战术配置" });
    expect(within(result).getByRole("region", { name: "执行后的技能顺序" })).toHaveTextContent("烈焰风暴原第 2 位");
    expect(within(result).getByRole("region", { name: "执行后的停用技能" })).toHaveTextContent("烈焰火球本次停用");
});

it("执行中不展示输入为实际战术，失败仍展示具体原因", () => {
    const frames = watchFrames([rec(1, { type: "turn_started", observation: before }),
        rec(2, { type: "tool_call", name: "configure_tactics", arguments: { adventurer_id: "mage", skill_priority: ["storm"], disabled_skills: [] } })]);
    const { rerender } = render(<ActionResults entry={actionEntries(frames).at(-1)!} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("等待工具返回");
    expect(screen.queryByRole("region", { name: "实际战术配置" })).not.toBeInTheDocument();
    rerender(<ActionResults entry={entry({ ok: false, error: "不是该角色当前拥有的主动技能" }, { adventurer_id: "mage" })} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("不是该角色当前拥有的主动技能");
    expect(screen.queryByRole("region", { name: "实际战术配置" })).not.toBeInTheDocument();
});
