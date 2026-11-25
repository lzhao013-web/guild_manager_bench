import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ActionResults } from "./ActionResultPanel";
import { actionEntries } from "./actionModel";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchRecord } from "./watchModel";

const guard = { adventurer_id: "guard", name: "步行骑士", template_id: "foot_knight", level: 3, resources: { current_hp: 80, current_mp: 0 }, effective_stats: { hp: 120, mp: 0 } };
const mage = { ...guard, adventurer_id: "mage", name: "烈焰法师", template_id: "pyromancer", resources: { current_hp: 70, current_mp: 20 }, effective_stats: { hp: 100, mp: 30 } };
const cleric = { ...mage, adventurer_id: "cleric", name: "牧师", template_id: "cleric", resources: { current_hp: 30, current_mp: 9 } };
const before: Observation = { turn: 5, max_turns: 35, gold: 100, experience_pool: 40, materials: { iron_ore: 2 }, party_size_limit: 6,
    adventurers: [guard, mage, cleric], monsters: [{ monster_id: "slime", name: "史莱姆" }, { monster_id: "ogre", name: "食人魔" }] };
const after: Observation = { ...before, turn: 6, gold: 130, experience_pool: 60, materials: { iron_ore: 3 }, adventurers: [
    { ...guard, resources: { current_hp: 64, current_mp: 0 } }, { ...mage, resources: { current_hp: 14, current_mp: 4 } }, { ...cleric, resources: { current_hp: 45, current_mp: 15 } },
] };
const battles = [
    { adventurer_id: "guard", adventurer_name: "步行骑士", monster_id: "slime", monster_name: "史莱姆", won: true, reward: { gold: 30, experience: 20, materials: { iron_ore: 1 } } },
    { adventurer_id: "mage", adventurer_name: "烈焰法师", monster_id: "ogre", monster_name: "食人魔", won: false, reward: { gold: 0, experience: 0, materials: {} } },
];
const hunts = [{ adventurer_id: "guard", monster_id: "slime" }, { adventurer_id: "mage", monster_id: "ogre" }];
const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-11-13T10:00:00Z", event });
function entry(result: Record<string, any>, args?: Record<string, any>, observation: Observation | null = before) {
    return actionEntries(watchFrames([...(observation ? [rec(1, { type: "turn_started", observation })] : []),
        rec(2, { type: "tool_result", name: "end_turn", ...(args !== undefined ? { arguments: args } : {}), result })])).at(-1)!;
}
const settled = () => ({ ok: true, event: { turn: 5, action: { type: "end_turn", hunts }, changes: [
    { kind: "resource", label: "金币", before: 100, after: 130 }, { kind: "resource", label: "经验池", before: 40, after: 60 },
    { kind: "material", label: "iron_ore", before: 2, after: 3 },
] }, turn_result: { battles }, _observation_after: after });
const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map(cell => cell.textContent);

it("先看本轮安排，再看战果奖励与成员结算状态，完整战报按需展开且不执行工具", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const trace = "战报末尾技能";
    const result = { ...settled(), turn_result: { battles: [battles[0], { ...battles[1], combat: { outcome: "right_win", actions_taken: 0, time_elapsed: 0, events: [{ skill_name: trace }] } }] } };
    render(<ActionResults entry={entry(result, { hunts: [] })} recordDetails={<details><summary>原始事件与调用参数</summary>结算记录</details>} />);
    const orders = screen.getByRole("region", { name: "本轮讨伐安排" });
    expect(orders).toHaveTextContent("步行骑士史莱姆");
    expect(orders).toHaveTextContent("烈焰法师食人魔");
    expect(orders).toHaveTextContent("休整牧师");
    const results = screen.getByRole("region", { name: "本轮结算结果" });
    expect(orders.compareDocumentPosition(results) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(screen.getByLabelText("战果概览")).toHaveTextContent("场数2胜1负1");
    expect(within(results).getByRole("article", { name: "烈焰法师 → 食人魔" })).toHaveTextContent("行动 0 次耗时 0");
    const rewards = screen.getByRole("region", { name: "讨伐奖励" });
    expect(cells(within(rewards).getByRole("row", { name: /金币/ }))).toEqual(["+30", "100 → 130"]);
    expect(cells(within(rewards).getByRole("row", { name: /经验/ }))).toEqual(["+20", "40 → 60"]);
    expect(cells(within(rewards).getByRole("row", { name: /铁矿石/ }))).toEqual(["+1", "2 → 3"]);
    const party = screen.getByRole("region", { name: "成员结算状态" });
    expect(party).toHaveTextContent("后者包含回合末恢复");
    expect(cells(within(party).getByRole("row", { name: /步行骑士/ }))).toEqual(["80 → 64", "0 → 0"]);
    expect(cells(within(party).getByRole("row", { name: /牧师/ }))).toEqual(["30 → 45", "9 → 15"]);
    expect(within(party).getByRole("row", { name: /牧师/ })).toHaveTextContent("休整");
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("进入第 6 回合");
    expect(screen.queryByText(trace)).not.toBeInTheDocument();
    const inspection = screen.getByRole("region", { name: "排查详情" });
    expect(results.compareDocumentPosition(inspection) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(inspection).toContainElement(screen.getByText("原始事件与调用参数"));
    expect(within(inspection).getByText("输入参数", { selector: "summary" }).closest("details")).not.toHaveAttribute("open");
    fireEvent.click(within(inspection).getByText("完整战果与战报"));
    expect(await within(inspection).findByText(trace)).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
});

it("只有真实零奖励才显示 +0，休整空战果不冒充未知，未变化余额仍可读取", () => {
    render(<ActionResults entry={entry({ ok: true, event: { turn: 5, action: { type: "end_turn", hunts: [] } }, turn_result: { battles: [] }, _observation_after: { ...before, turn: 6 } })} />);
    expect(screen.getByRole("region", { name: "本轮讨伐安排" })).toHaveTextContent("本轮未安排讨伐");
    expect(screen.getByRole("region", { name: "战斗结果" })).toHaveTextContent("本轮无战斗");
    const rewards = screen.getByRole("region", { name: "讨伐奖励" });
    expect(cells(within(rewards).getByRole("row", { name: /金币/ }))).toEqual(["+0", "100 → 100"]);
    expect(cells(within(rewards).getByRole("row", { name: /经验/ }))).toEqual(["+0", "40 → 40"]);
    expect(rewards).toHaveTextContent("无材料奖励");
    const party = screen.getByRole("region", { name: "成员结算状态" });
    expect(within(party).getAllByRole("row")).toHaveLength(4);
    expect(within(party).getAllByText("休整", { exact: true })).toHaveLength(3);
});

it("部分奖励缺失时不汇总部分数据或用余额变化补成奖励，平局与未知结果分开", () => {
    const result = { ...settled(), turn_result: { battles: [battles[0], { ...battles[1], won: false, outcome: "draw" }, { adventurer_name: "未完整记录的成员", monster_name: "古龙" }] } };
    render(<ActionResults entry={entry(result)} />);
    expect(screen.getByLabelText("战果概览")).toHaveTextContent("场数3胜1负0平1未记录1");
    const rewards = screen.getByRole("region", { name: "讨伐奖励" });
    expect(cells(within(rewards).getByRole("row", { name: /金币/ }))).toEqual(["未记录", "100 → 130"]);
    expect(cells(within(rewards).getByRole("row", { name: /经验/ }))).toEqual(["未记录", "40 → 60"]);
    expect(rewards).toHaveTextContent("材料奖励未完整记录");
    expect(within(rewards).queryByText("+30")).not.toBeInTheDocument();
    expect(within(rewards).queryByText("无材料奖励")).not.toBeInTheDocument();
});

it("结果没有战果时不拿提交的名单推算胜负、奖励或结算后的生命", () => {
    render(<ActionResults entry={entry({ ok: true }, { hunts })} />);
    expect(screen.getByRole("region", { name: "本轮讨伐安排" })).toHaveTextContent("步行骑士史莱姆");
    expect(screen.getByText("战斗结果与奖励未记录")).toBeVisible();
    expect(screen.queryByLabelText("战果概览")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "讨伐奖励" })).not.toBeInTheDocument();
    expect(cells(within(screen.getByRole("region", { name: "成员结算状态" })).getByRole("row", { name: /步行骑士/ }))).toEqual(["80 → 未记录", "0 → 未记录"]);
});

it("旧完整战果使用记录的结算前后资源，缺少成员编号时不按同名猜测参战身份", () => {
    const { rerender } = render(<ActionResults entry={entry({ ok: true, event: { battles: [{ ...battles[0], adventurer_before_resources: { current_hp: 80, current_mp: 0 }, adventurer_after_resources: { current_hp: 64, current_mp: 0 } }] } }, undefined, null)} />);
    expect(cells(within(screen.getByRole("region", { name: "成员结算状态" })).getByRole("row", { name: /步行骑士/ }))).toEqual(["80 → 64", "0 → 0"]);
    rerender(<ActionResults entry={entry({ ok: true, turn_result: { battles: [{ adventurer_name: "步行骑士", monster_name: "史莱姆", won: true, reward: { gold: 0, experience: 0, materials: {} } }] } }, undefined)} />);
    const party = screen.getByRole("region", { name: "成员结算状态" });
    expect(within(party).getByRole("row", { name: /步行骑士/ })).toHaveTextContent("安排未记录");
    expect(within(party).queryByText("休整", { exact: true })).not.toBeInTheDocument();
});

it("同名成员按真实编号保留各自状态，投影返回的成员集合不冒充完整队伍", () => {
    const sameName = { ...mage, name: "步行骑士" };
    const observed = { ...before, adventurers: [guard, sameName, cleric] };
    const result = { ok: true, turn_result: { battles: [battles[0]] }, _action_output: { adventurers: [{ ...guard, resources: { current_hp: 64, current_mp: 0 } }] } };
    render(<ActionResults entry={entry(result, undefined, observed)} />);
    const rows = within(screen.getByRole("region", { name: "成员结算状态" })).getAllByRole("row", { name: /步行骑士/ });
    expect(rows).toHaveLength(2);
    expect(cells(rows[0])).toEqual(["80 → 64", "0 → 0"]);
    expect(cells(rows[1])).toEqual(["70 → 未记录", "20 → 未记录"]);
    expect(rows[0]).toHaveTextContent("出战");
    expect(rows[1]).toHaveTextContent("休整");
});

it("最终回合只说明经营阶段结束，不显示不存在的下一回合或旧分数", () => {
    const result = { ok: true, turn_result: { battles: [] }, _observation_after: { ...before, turn: 36, finished: true } };
    render(<ActionResults entry={entry(result, { hunts: [] }, { ...before, turn: 35 })} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("经营阶段已结束");
    expect(screen.queryByText(/进入第/)).not.toBeInTheDocument();
    expect(screen.queryByText(/战力|Rank Score/)).not.toBeInTheDocument();
});

it("空队伍与未知成员分开，执行中和失败继续显示状态与具体原因", () => {
    const { rerender } = render(<ActionResults entry={entry({ ok: true, turn_result: { battles: [] }, _observation_after: { ...after, adventurers: [] } })} />);
    expect(screen.getByText("结算后队伍为空")).toBeVisible();
    rerender(<ActionResults entry={entry({ ok: true }, undefined, null)} />);
    expect(screen.getByText("结算后成员状态未记录")).toBeVisible();
    expect(screen.getByText("讨伐安排未记录")).toBeVisible();
    const pending = actionEntries(watchFrames([rec(1, { type: "turn_started", observation: before }), rec(2, { type: "tool_call", name: "end_turn", arguments: { hunts } })])).at(-1)!;
    rerender(<ActionResults entry={pending} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("等待工具返回");
    expect(screen.queryByRole("region", { name: "本轮结算结果" })).not.toBeInTheDocument();
    rerender(<ActionResults entry={entry({ ok: false, error: "每名冒险者只能出战一次" }, { hunts })} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("每名冒险者只能出战一次");
    expect(screen.queryByRole("region", { name: "本轮结算结果" })).not.toBeInTheDocument();
});

it("六场讨伐全部直接展示，成员和目标名称按原记录保留", () => {
    const party = Array.from({ length: 6 }, (_, index) => ({ ...guard, adventurer_id: `hero-${index}`, name: `远征成员${index + 1}` }));
    const results = party.map((hero, index) => ({ adventurer_id: hero.adventurer_id, adventurer_name: hero.name,
        monster_id: `target-${index}`, monster_name: `讨伐目标${index + 1}`, won: index % 2 === 0, reward: { gold: 0, experience: 0, materials: {} } }));
    render(<ActionResults entry={entry({ ok: true, turn_result: { battles: results }, _observation_after: { ...before, turn: 6, adventurers: party } }, undefined, { ...before, adventurers: party })} />);
    const battles = screen.getByRole("region", { name: "战斗结果" });
    expect(within(battles).getAllByRole("article")).toHaveLength(6);
    expect(within(battles).getByRole("article", { name: "远征成员6 → 讨伐目标6" })).toBeVisible();
    expect(screen.getByLabelText("战果概览")).toHaveTextContent("场数6胜3负3");
});
