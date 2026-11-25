import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ActionResults } from "./ActionResultPanel";
import { ActionTimeline } from "./ActionTimeline";
import { ToolResultPanel } from "./ToolResultPanel";
import { actionEntries } from "./actionModel";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchRecord } from "./watchModel";

const hero = { adventurer_id: "hero", name: "守卫", template_id: "foot_knight", level: 3,
    resources: { current_hp: 80, current_mp: 0 }, effective_stats: { hp: 120, mp: 0, attack: 10, defense: 2 },
    equipment_slots: [{ slot: "main_hand", item: { instance_id: "old", name: "旧剑" } }] };
const before: Observation = { turn: 5, max_turns: 35, gold: 100, experience_pool: 40, materials: {}, party_size_limit: 6, adventurers: [hero] };
const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-10-29T10:00:00Z", event });
function action(name: string, result: Record<string, any>, args: Record<string, any> = {}) {
    return actionEntries(watchFrames([rec(1, { type: "turn_started", observation: before }), rec(2, { type: "tool_result", name, arguments: args, result })]))[1];
}

it("招募详情只展示一份资源与角色结果，完整字段和原始记录集中在底部且按需展开", async () => {
    const recruit = { adventurer_id: "recruit-guard", name: "佣兵战士·脆弱", template_id: "mercenary_warrior", level: 1,
        resources: { current_hp: 143, current_mp: 0 }, effective_stats: { hp: 143, mp: 0, attack: 18, defense: 9, speed: 8, recovery: 10 } };
    const entry = action("recruit_adventurer", { ok: true, recruited_adventurer: recruit,
        event: { changes: [{ kind: "resource", label: "金币", before: 100, after: 56 }, { kind: "adventurer", label: "新冒险者", after: `${recruit.name} (${recruit.adventurer_id})` }] },
        receipt: "已保存的招募回执",
    }, { candidate_id: "candidate" });
    render(<ActionResults entry={entry} recordDetails={<details className="json-details"><summary>原始事件与调用参数</summary><pre>原始招募记录</pre></details>} />);
    expect(screen.queryByRole("region", { name: "执行结果" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: "实际资源变化" })).toHaveLength(1);
    expect(screen.getByRole("region", { name: "实际资源变化" })).toHaveTextContent("金币-44100 → 56");
    const result = screen.getByRole("region", { name: "招募结果" });
    expect(result).toHaveTextContent("佣兵战士·脆弱");
    expect(within(result).getByText("生命").parentElement).toHaveTextContent("生命143");
    expect(within(result).getByText("法力").parentElement).toHaveTextContent("法力0");
    expect(result).not.toHaveTextContent("143 / 143");
    expect(result).not.toHaveTextContent("生命上限");
    expect(result).not.toHaveTextContent("1 条");
    expect(screen.queryByText("新冒险者")).not.toBeInTheDocument();
    const inspection = screen.getByRole("region", { name: "排查详情" });
    expect(inspection).toContainElement(screen.getByRole("region", { name: "输入参数" }));
    expect(inspection).toContainElement(screen.getByText("原始事件与调用参数"));
    expect(screen.queryByText("recruit-guard")).not.toBeInTheDocument();
    expect(screen.queryByText("已保存的招募回执")).not.toBeInTheDocument();
    fireEvent.click(within(inspection).getByText("完整角色字段"));
    expect(await within(inspection).findByText("recruit-guard")).toBeVisible();
    fireEvent.click(within(inspection).getByText("其他返回字段"));
    expect(await within(inspection).findByText("已保存的招募回执")).toBeVisible();
    expect(screen.getAllByRole("region", { name: "实际资源变化" })).toHaveLength(1);
});

it("招募角色保留受伤数值，未记录当前状态时只展示上限，失败仍展示原因", () => {
    const recruit = { adventurer_id: "recruit-guard", name: "佣兵战士·脆弱", level: 1,
        resources: { current_hp: 90, current_mp: 0 }, effective_stats: { hp: 143, mp: 0, attack: 18 } };
    const { rerender } = render(<ActionResults entry={action("recruit_adventurer", { ok: true, recruited_adventurer: recruit })} />);
    expect(screen.getByRole("region", { name: "招募结果" })).toHaveTextContent("生命90 / 143");
    expect(screen.queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    const { resources: _resources, ...withoutResources } = recruit;
    rerender(<ActionResults entry={action("recruit_adventurer", { ok: true, recruited_adventurer: withoutResources })} />);
    const result = screen.getByRole("region", { name: "招募结果" });
    expect(result).toHaveTextContent("生命上限143");
    expect(result).toHaveTextContent("法力上限0");
    expect(result).not.toHaveTextContent("143 / 143");
    rerender(<ActionResults entry={action("recruit_adventurer", { ok: false, error: "金币不足", recruited_adventurer: recruit })} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("金币不足");
    expect(screen.queryByRole("region", { name: "招募结果" })).not.toBeInTheDocument();
});

it("单次穿戴复用资源与成员变化展示，槽位显示前后装备，原始参数默认收起", () => {
    const after: Observation = { ...before, adventurers: [{ ...hero, effective_stats: { ...hero.effective_stats, attack: 20, defense: 5 },
        equipment_slots: [{ slot: "main_hand", item: { instance_id: "new", name: "新剑" } }] }] };
    render(<ActionResults entry={action("equip_item", { ok: true, _observation_after: after }, { adventurer_id: "hero", equipment_instance_id: "new" })} />);
    const changes = screen.getByRole("region", { name: "实际成员变化" });
    const attack = within(changes).getByRole("row", { name: /攻击/ });
    expect(within(attack).getByRole("cell", { name: "10" })).toBeVisible();
    expect(within(attack).getByRole("cell", { name: "20" })).toBeVisible();
    expect(within(attack).getByRole("cell", { name: "+10" })).toBeVisible();
    expect(within(changes).getByRole("row", { name: /防御/ })).toHaveTextContent("+3");
    expect(within(changes).getByText("主手：旧剑")).toBeVisible();
    expect(within(changes).getByText("主手：新剑")).toBeVisible();
    expect(changes).toHaveTextContent("Lv.3");
    const input = screen.getByRole("region", { name: "输入参数" });
    expect(input.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(input.closest("details")!.querySelector("summary")!);
    expect(within(input).getByText("原值：hero")).toBeVisible();
    fireEvent.click(screen.getByText("完整实际变化（含属性前后值）"));
    expect(screen.getByText("攻击：10 → 20")).toBeVisible();
});

it("查询时间线只保留摘要，详情每页八行，展开字段和筛选不会触发时间定位", () => {
    const inventory = Array.from({ length: 12 }, (_, i) => ({ instance_id: `weapon-${i}`, name: `训练剑${i}`, slot: "main_hand", stats: { attack: i + 1, mp: 0 }, equipped_by: null, skills: [] }));
    const frames = watchFrames([rec(1, { type: "turn_started", observation: before }), rec(2, { type: "tool_result", name: "get_inventory", arguments: {}, result: { ok: true, equipment_inventory: inventory } })]);
    const onFrame = vi.fn();
    render(<ActionTimeline frames={frames} frame={frames.at(-1)} active following={false} rolledBack={false} onFrame={onFrame} renderDetails={() => null} />);
    const timeline = screen.getByRole("region", { name: "观战行动时间线" });
    expect(within(timeline).queryByRole("region", { name: "业务返回" })).not.toBeInTheDocument();
    const query = within(timeline).getByRole("button", { name: "定位行动 查看仓库" });
    expect(within(query.closest("article")!).queryByRole("table")).not.toBeInTheDocument();
    const detail = screen.getByRole("region", { name: "所选行动详情" });
    const table = within(detail).getByRole("region", { name: "返回的装备库存" });
    expect(within(table).getAllByRole("row")).toHaveLength(9);
    const object = within(table).getByRole("button", { name: "训练剑0" });
    expect(object).not.toHaveTextContent("weapon-0");
    fireEvent.click(object);
    expect(within(table).getByText("weapon-0", { exact: true })).toBeVisible();
    fireEvent.change(within(table).getByRole("searchbox"), { target: { value: "weapon-11" } });
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(table).toHaveTextContent("训练剑11");
    expect(onFrame).not.toHaveBeenCalled();
    fireEvent.click(within(timeline).getByRole("button", { name: "定位行动 查看仓库" }));
    expect(onFrame).toHaveBeenCalledWith(frames.at(-1));
});

it("战果汇总区分零奖励与缺失奖励，模拟不展示实际资源变化", () => {
    const simulated = action("preview_battle", { ok: true, simulation_only: true,
        preview: { adventurer_name: "守卫", monster_name: "史莱姆", won: true, reward: { gold: 0, experience: 0, materials: {} } },
    });
    const { rerender } = render(<ActionResults entry={simulated} />);
    expect(screen.getByRole("heading", { name: "预览结果" })).toBeVisible();
    expect(screen.getByLabelText("战果概览")).toHaveTextContent("场数1胜1负0奖励金币+0奖励经验+0");
    expect(screen.queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    rerender(<ActionResults entry={action("end_turn", { ok: true, turn_result: { battles: [
        { adventurer_name: "守卫", monster_name: "史莱姆", won: true, reward: { gold: 5, experience: 7, materials: {} } },
        { adventurer_name: "术士", monster_name: "巨蛛", won: false },
    ] } })} />);
    expect(screen.getByLabelText("战果概览")).toHaveTextContent("场数2胜1负1");
    const rewards = screen.getByRole("region", { name: "讨伐奖励" });
    expect(within(within(rewards).getByRole("row", { name: /金币/ })).getAllByRole("cell")[0]).toHaveTextContent("未记录");
    expect(within(within(rewards).getByRole("row", { name: /经验/ })).getAllByRole("cell")[0]).toHaveTextContent("未记录");
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("本轮已结算");
});

it("完整模拟战报在展开时才挂载，默认结果不铺开底层事件", async () => {
    const trace = "完整战报末尾技能";
    render(<ActionResults entry={action("preview_battle", { ok: true, preview: { adventurer_name: "守卫", monster_name: "史莱姆", won: true,
        combat: { outcome: "left_win", events: [{ action_type: "skill", skill_id: trace, damage: 10 }] } } })} />);
    expect(screen.queryByText(trace)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("其他返回字段"));
    expect(await screen.findByText(trace)).toBeVisible();
});

it("批量备忘的返回和行动摘要显示块数与已用字符", () => {
    const entry = action("write_memo", { ok: true, memo: { content: "[计划] 创建 T1，更新 T2，有效至 T5（含）\n保存", updated_turn: 2,
        blocks: [{ key: "计划", content: "保存", created_turn: 1, updated_turn: 2, expires_turn: 5 }], used_chars: 4, max_chars: 4000,
    } }, { operations: [{ op: "update", key: "计划", content: "保存" }] });
    expect(entry.summary).toBe("备忘已更新 · 1 块 · 已用 4/4000 字符");
    render(<ToolResultPanel entry={entry} />);
    expect(screen.getByRole("region", { name: "备忘正文" })).toHaveTextContent("已用 4/4000 字符");
    expect(screen.getByRole("region", { name: "备忘正文" })).toHaveTextContent("有效至 T5（含）");
});

it("清空备忘的行动摘要明确显示已清空", () => {
    const entry = action("write_memo", { ok: true, memo: { content: "", updated_turn: 5, max_chars: 500 } }, { content: "" });
    render(<ActionResults entry={entry} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("备忘已清空");
    expect(screen.queryByText("备忘已保存")).not.toBeInTheDocument();
});

it("备忘正文使用实际返回文本，长正文可展开且始终按文本渲染", () => {
    const saved = '<img src=x onerror="alert(1)">\n' + "已保存正文".repeat(60) + "末尾";
    const entry = action("write_memo", { ok: true, memo: { content: saved } }, { content: "请求正文" });
    const { rerender } = render(<ToolResultPanel entry={entry} compact />);
    const memo = screen.getByRole("region", { name: "备忘正文" });
    expect(memo.querySelector("pre")).not.toBeVisible();
    fireEvent.click(within(memo).getByText("完整正文"));
    expect(memo.querySelector("pre")!.textContent).toBe(saved);
    expect(memo).not.toHaveTextContent("请求正文");
    expect(document.querySelector("img")).not.toBeInTheDocument();
    rerender(<ToolResultPanel entry={entry} />);
    expect(screen.getByRole("region", { name: "备忘正文" }).querySelector("pre")).toBeVisible();
});
