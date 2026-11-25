import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { ActionResults } from "./ActionResultPanel";
import { actionEntries, describeAction } from "./actionModel";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchRecord } from "./watchModel";

const wolf = { monster_id: "old_wolf", archetype_id: "forest_wolf", name: "森林狼", tier: "normal", stats: { hp: 50, mp: 0, attack: 10, defense: 2, speed: 14 }, skills: [], reward: { gold: 12, experience: 9, materials: {} } };
const slime = { ...wolf, monster_id: "old_slime", archetype_id: "slime", name: "史莱姆" };
const challenge = { ...wolf, monster_id: "challenge_dragon", archetype_id: "dragon", name: "【挑战】远古巨龙", tier: "challenge" };
const ogre = { ...wolf, monster_id: "new_ogre", archetype_id: "ogre", name: "食人魔", tier: "elite", stats: { hp: 140, mp: 20, attack: 26, defense: 8, speed: 7 }, skills: [{ skill_id: "heavy_blow", name: "重击" }], reward: { gold: 30, experience: 20, materials: { iron_ore: 2 } } };
const before: Observation = { turn: 6, max_turns: 35, gold: 100, experience_pool: 80, materials: {}, party_size_limit: 3, adventurers: [], monsters: [wolf, slime, challenge] };
const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-11-13T10:00:00Z", event });
function entry(result: Record<string, unknown>, name = "refresh_monsters", args: Record<string, unknown> = {}, observation: Observation | null = before) {
    return actionEntries(watchFrames([...(observation ? [rec(1, { type: "turn_started", observation })] : []),
        rec(2, { type: "tool_result", name, arguments: args, result })])).at(-1)!;
}
const paid = () => ({ ok: true, monsters: [wolf, ogre, challenge], monster_refresh: { refreshes_used: 2, next_gold_cost: 40, available: true },
    _llm_refs: { monster: { old_wolf: 1, new_ogre: 6, challenge_dragon: 3 } }, event: { changes: [
        { kind: "resource", label: "金币", before: 100, after: 80 },
        { kind: "resource", label: "本回合刷新次数", before: 1, after: 2 },
    ] } });
const preview = () => ({ ok: true, simulation_only: true, gold_cost: 20, can_afford: false, refresh_number: 2,
    monsters: [wolf, ogre, challenge].map((monster, index) => ({ ...monster, monster_id: undefined, refresh_ref: `refresh:T6:R2:slot${index + 1}`, preserved: index !== 1 })) });

it("实刷按真实编号区分新怪、锁定与挑战，费用和获胜奖励分开，完整字段收底", async () => {
    render(<ActionResults entry={entry(paid(), "refresh_monsters", { locked_monster_ids: [2] })}
        recordDetails={<details><summary>原始事件与调用参数</summary>刷新记录</details>} />);
    const costs = screen.getByRole("region", { name: "实际资源变化" });
    expect(costs).toHaveTextContent("金币-20100 → 80");
    expect(costs).not.toHaveTextContent("经验");
    expect(screen.getByText(/本回合第/)).toHaveTextContent("本回合第 2 次刷新");
    expect(screen.getByText(/下次费用/)).toHaveTextContent("金币 40");
    const fresh = screen.getByRole("region", { name: "本次新刷出的怪物" });
    expect(fresh).toHaveTextContent("食人魔");
    expect(fresh).toHaveTextContent("怪物 #6");
    expect(fresh).toHaveTextContent("攻击26");
    expect(fresh).toHaveTextContent("重击");
    expect(within(fresh).getByRole("region", { name: "食人魔的获胜奖励" })).toHaveTextContent("金币30经验20");
    expect(screen.getByRole("region", { name: "锁定保留" })).toHaveTextContent("森林狼");
    expect(screen.getByRole("region", { name: "固定挑战" })).toHaveTextContent("自动保留【挑战】远古巨龙");
    expect(screen.queryByText("史莱姆")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "执行结果" })).not.toBeInTheDocument();
    const inspection = screen.getByRole("region", { name: "排查详情" });
    expect(inspection).toContainElement(screen.getByRole("region", { name: "输入参数" }));
    expect(inspection).toContainElement(screen.getByText("原始事件与调用参数"));
    expect(within(inspection).getByText("完整怪池字段").closest("details")).not.toHaveAttribute("open");
    expect(screen.queryByText("new_ogre", { exact: true })).not.toBeInTheDocument();
    fireEvent.click(within(inspection).getByText("完整怪池字段"));
    expect(await within(inspection).findByText("new_ogre", { exact: true })).toBeVisible();
});

it.each(["preview_monster_refresh", "refresh_monsters"])("%s 预览使用保留标记，不把候选当作真实编号或实际支出", name => {
    const result: Record<string, unknown> = preview();
    if (name === "refresh_monsters") delete result.simulation_only;
    render(<ActionResults entry={entry(result, name, name === "refresh_monsters" ? { preview: true } : {}, null)} />);
    expect(screen.getByText("仅预览，未扣费，真实怪池未改变。")).toBeVisible();
    expect(screen.getByRole("region", { name: "预计刷新费用" })).toHaveTextContent("金币20金币不足，暂不能实际刷新");
    expect(screen.queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    const fresh = screen.getByRole("region", { name: "待替换候选" });
    expect(fresh).toHaveTextContent("食人魔");
    expect(fresh).toHaveTextContent("候选 2");
    expect(fresh).not.toHaveTextContent("怪物 #");
    expect(screen.getByRole("region", { name: "锁定保留" })).toHaveTextContent("森林狼");
    expect(screen.getByRole("region", { name: "固定挑战" })).toHaveTextContent("【挑战】远古巨龙");
    expect(screen.getByText("候选仅供预览，不能直接用于锁定或讨伐。")).toBeVisible();
    expect(screen.queryByText(/下次费用/)).not.toBeInTheDocument();
    expect(screen.getByText("完整候选字段").closest("details")).not.toHaveAttribute("open");
});

it("同名怪物按编号判断替换，不按名称或请求中的锁定名单推断", () => {
    const result = { ...paid(), monsters: [{ ...slime, monster_id: "replacement_slime" }, challenge] };
    render(<ActionResults entry={entry(result, "refresh_monsters", { locked_monster_ids: ["old_slime"] })} />);
    expect(screen.getByRole("region", { name: "本次新刷出的怪物" })).toHaveTextContent("史莱姆");
    expect(screen.queryByRole("region", { name: "锁定保留" })).not.toBeInTheDocument();
});

it("缺少原怪池不猜测分类，缺少实际扣款不拿下次标价冒充，缺失资料明确显示", () => {
    const partial = { monster_id: "unknown_monster", name: "未完整记录的怪物", tier: "normal" };
    render(<ActionResults entry={entry({ ok: true, monsters: [partial], monster_refresh: { next_gold_cost: 80 } }, "refresh_monsters", { locked_monster_ids: ["unknown_monster"] }, null)} />);
    expect(screen.getByText("实际消耗未记录")).toBeVisible();
    const result = screen.getByRole("region", { name: "未分类的返回怪物" });
    expect(result).toHaveTextContent("无法区分保留与替换");
    expect(result).toHaveTextContent("属性未记录");
    expect(result).toHaveTextContent("奖励未记录");
    expect(result).toHaveTextContent("技能未记录");
    expect(screen.queryByRole("region", { name: "本次新刷出的怪物" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "锁定保留" })).not.toBeInTheDocument();
    expect(screen.getByText(/下次费用/)).toHaveTextContent("金币 80");
    expect(screen.queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
});

it("旧实刷记录可用本条保存的结果快照，不把刷新前快照当成结果", () => {
    const { rerender } = render(<ActionResults entry={entry({ ok: true, _observation_after: { ...before, gold: 90, monsters: [wolf, ogre, challenge] } })} />);
    expect(screen.getByRole("region", { name: "本次新刷出的怪物" })).toHaveTextContent("食人魔");
    expect(screen.getByRole("region", { name: "实际资源变化" })).toHaveTextContent("-10");
    rerender(<ActionResults entry={entry({ ok: true })} />);
    expect(screen.getByText("刷新后的怪池未记录")).toBeVisible();
    expect(screen.queryByText("森林狼")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "本次新刷出的怪物" })).not.toBeInTheDocument();
});

it("较大怪池默认只铺开前三个目标，其余结果可展开，完整字段不丢失", () => {
    const monsters = Array.from({ length: 5 }, (_, index) => ({ ...ogre, monster_id: `ogre_${index}`, name: `精英食人魔 ${index + 1}` }));
    render(<ActionResults entry={entry({ ...paid(), monsters })} />);
    const group = screen.getByRole("region", { name: "本次新刷出的怪物" });
    const targets = within(group).getAllByRole("article");
    expect(targets).toHaveLength(5);
    targets.slice(0, 3).forEach(target => expect(target).toBeVisible());
    targets.slice(3).forEach(target => expect(target).not.toBeVisible());
    const more = within(group).getByText("其余 2 个目标");
    expect(more.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(more);
    targets.forEach(target => expect(target).toBeVisible());
    expect(screen.getByText("完整怪池字段").closest("details")).not.toHaveAttribute("open");
});

it("预览缺少字段不显示免费或金币足够，也不把真实快照冒充候选", () => {
    render(<ActionResults entry={entry({ ok: true }, "preview_monster_refresh")} />);
    expect(screen.getByRole("region", { name: "预计刷新费用" })).toHaveTextContent("费用未记录");
    expect(screen.getByText("刷新候选未记录")).toBeVisible();
    expect(screen.queryByText("金币足够")).not.toBeInTheDocument();
    expect(screen.queryByText("森林狼")).not.toBeInTheDocument();
});

it("执行中、失败与仅文本的历史记录保留原展示，不提前显示刷新已生效", () => {
    const frame = watchFrames([rec(1, { type: "turn_started", observation: before })])[0];
    const pending = describeAction({ ...frame, record: rec(2, { type: "tool_call", name: "refresh_monsters", arguments: { locked_monster_ids: [] } }) });
    const { rerender } = render(<ActionResults entry={pending} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("等待工具返回");
    expect(screen.queryByRole("region", { name: "本次新刷出的怪物" })).not.toBeInTheDocument();
    rerender(<ActionResults entry={entry({ ok: false, error: "至少需要刷新一个普通怪物位置" })} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("至少需要刷新一个普通怪物位置");
    const textOnly = describeAction({ ...frame, record: rec(2, { type: "tool_result", name: "refresh_monsters", content: "成功 refresh_monsters：历史刷新文本" }) });
    rerender(<ActionResults entry={textOnly} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("历史刷新文本");
});
