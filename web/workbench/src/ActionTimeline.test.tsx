import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ActionTimeline } from "./ActionTimeline";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchFrame, WatchRecord } from "./watchModel";

const observation: Observation = { turn: 1, max_turns: 8, gold: 100, experience_pool: 50, materials: {}, party_size_limit: 3,
    adventurers: [{ adventurer_id: "hero", name: "验收剑士", template_id: "warrior", level: 1,
        effective_stats: { hp: 100, mp: 10 }, resources: { current_hp: 100, current_mp: 10 } }],
    monsters: [{ monster_id: "slime", name: "史莱姆" }] };
const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-10-20T10:00:00Z", event });
function setup(records: WatchRecord[], selected?: number) {
    const frames = watchFrames([rec(1, { type: "turn_started", observation }), ...records]);
    const onFrame = vi.fn();
    const node = (frame: WatchFrame) => <ActionTimeline frames={frames} frame={frame} active following={false} rolledBack={false} onFrame={onFrame} renderDetails={() => <span>原始记录入口</span>} />;
    const view = render(node(frames.find(frame => frame.cursor === selected) || frames.at(-1)!));
    return { frames, onFrame, select: (cursor: number) => view.rerender(node(frames.find(frame => frame.cursor === cursor)!)) };
}

it("时间线只展示摘要，资源与成员明细保留在右侧，点击仍定位真实结果帧", () => {
    const { frames, onFrame } = setup([
        rec(2, { type: "tool_call", name: "allocate_experience", call_id: "xp", arguments: { adventurer_id: "hero", amount: 12 } }),
        rec(3, { type: "tool_result", name: "allocate_experience", call_id: "xp", result: { ok: true, event: { summary: "为验收剑士分配经验", changes: [
            { kind: "resource", label: "经验池", before: 50, after: 38 },
            { kind: "adventurer", label: "验收剑士 等级", before: 1, after: 2 },
        ] } } }),
    ]);
    const detail = screen.getByRole("region", { name: "所选行动详情" });
    const input = within(detail).getByRole("region", { name: "输入参数" });
    expect(input.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(input.closest("details")!.querySelector("summary")!);
    expect(within(input).getByText("验收剑士", { exact: true })).toBeVisible();
    expect(within(input).getByText("原值：hero", { exact: true })).toBeVisible();
    expect(within(input).getByText("12", { exact: true })).toBeVisible();
    expect(within(detail).getByRole("region", { name: "实际资源变化" })).toHaveTextContent("经验池-12");
    expect(within(detail).getByRole("region", { name: "实际成员变化" })).toHaveTextContent("1 → 2");
    const row = screen.getByRole("button", { name: "定位行动 为验收剑士分配经验" });
    expect(row).not.toHaveTextContent("输入 ·");
    expect(row).toHaveTextContent("经验池 -12");
    expect(within(screen.getByRole("region", { name: "观战行动时间线" })).queryByRole("region", { name: "业务返回" })).not.toBeInTheDocument();
    fireEvent.click(row);
    expect(onFrame).toHaveBeenCalledWith(frames.at(-1));
    expect(frames.at(-1)!.record.event).not.toHaveProperty("arguments");
});

it("招募记录不重复铺开角色属性，切换摘要仍能查看各自的完整招募结果", () => {
    const recruit = { adventurer_id: "archer", name: "林地射手·迟钝", template_id: "archer", level: 1,
        effective_stats: { hp: 82, mp: 0, attack: 19, defense: 4, speed: 18 }, resources: { current_hp: 82, current_mp: 0 } };
    const { frames, onFrame, select } = setup([
        rec(2, { type: "tool_result", name: "recruit_adventurer", result: { ok: true, recruited_adventurer: recruit,
            event: { summary: `招募 ${recruit.name}`, changes: [{ kind: "resource", label: "金币", before: 100, after: 57 }] } } }),
        rec(3, { type: "tool_result", name: "recruit_adventurer", result: { ok: true,
            recruited_adventurer: { ...recruit, adventurer_id: "warrior", name: "佣兵战士·脆弱" },
            event: { summary: "招募 佣兵战士·脆弱", changes: [{ kind: "resource", label: "金币", before: 57, after: 13 }] } } }),
    ], 2);
    const timeline = screen.getByRole("region", { name: "观战行动时间线" });
    const archer = within(timeline).getByRole("button", { name: "定位行动 招募 林地射手·迟钝" });
    const warrior = within(timeline).getByRole("button", { name: "定位行动 招募 佣兵战士·脆弱" });
    expect(archer).toHaveTextContent("金币 -43");
    expect(warrior).toHaveTextContent("金币 -44");
    expect(archer).toHaveAttribute("aria-pressed", "true");
    expect(warrior).toHaveAttribute("aria-pressed", "false");
    expect(within(timeline).queryByRole("region", { name: "招募结果" })).not.toBeInTheDocument();
    expect(within(timeline).queryByText("完整字段")).not.toBeInTheDocument();
    const result = () => within(screen.getByRole("region", { name: "所选行动详情" })).getByRole("region", { name: "招募结果" });
    expect(result()).toHaveTextContent("林地射手·迟钝");
    expect(within(result()).getByText("生命").parentElement).toHaveTextContent("生命82");
    fireEvent.click(warrior);
    expect(onFrame).toHaveBeenCalledWith(frames.at(-1));
    select(3);
    expect(warrior).toHaveAttribute("aria-pressed", "true");
    expect(archer).toHaveAttribute("aria-pressed", "false");
    expect(result()).toHaveTextContent("佣兵战士·脆弱");
    expect(result()).not.toHaveTextContent("林地射手·迟钝");
});

it("回看调用时显示当时输入和执行中，不提前展示之后的结果", () => {
    const { select } = setup([
        rec(2, { type: "tool_call", name: "allocate_experience", call_id: "xp", arguments: { adventurer_id: "hero", amount: 10 } }),
        rec(3, { type: "tool_result", name: "allocate_experience", call_id: "xp", result: { ok: false, error: "余额不足" } }),
    ]);
    select(2);
    const detail = screen.getByRole("region", { name: "所选行动详情" });
    const input = within(detail).getByRole("region", { name: "输入参数" });
    fireEvent.click(input.closest("details")!.querySelector("summary")!);
    expect(input).toHaveTextContent("10");
    expect(within(detail).getByRole("region", { name: "执行结果" })).toHaveTextContent("等待工具返回");
    expect(within(detail).queryByText("余额不足", { exact: true })).not.toBeInTheDocument();
});

it("运行事件默认收起，定位系统帧不自动展开，手动开关状态保持可控", () => {
    const { frames, onFrame, select } = setup([
        rec(2, { type: "tool_result", name: "get_party", result: { adventurers: [] } }),
        rec(3, { type: "model_request", step: 1 }),
        rec(4, { type: "turn_scored", turn: 1, rank_score: 12.5 }),
    ], 4);
    const group = screen.getByText(/运行事件 ·/).closest("details")!;
    const summary = group.querySelector("summary")!;
    expect(group).not.toHaveAttribute("open");
    expect(within(group).getByRole("button", { name: "定位行动 评分已记录", hidden: true })).not.toBeVisible();
    fireEvent.click(summary);
    expect(group).toHaveAttribute("open");
    fireEvent.click(within(group).getByRole("button", { name: "定位行动 请求模型" }));
    expect(onFrame).toHaveBeenCalledWith(frames.find(frame => frame.cursor === 3));
    select(3);
    expect(group).toHaveAttribute("open");
    fireEvent.click(summary);
    expect(group).not.toHaveAttribute("open");
    select(4);
    expect(group).not.toHaveAttribute("open");
    expect(screen.getByRole("region", { name: "所选行动详情" })).toHaveTextContent("评分已记录");
});

it("所有工具默认可见且按具体工具筛选汇总，调用与结果合并计一次", () => {
    setup([
        rec(2, { type: "tool_call", name: "get_crafting", call_id: "query", arguments: {} }),
        rec(3, { type: "tool_result", name: "get_crafting", call_id: "query", content: "成功 get_crafting", result: { crafting_recipes: [] } }),
        rec(4, { type: "tool_result", name: "preview_monster_refresh", result: { ok: true, simulation_only: true } }),
        rec(5, { type: "tool_result", name: "refresh_monsters", result: { ok: true } }),
        rec(6, { type: "tool_result", name: "write_memo", result: { ok: false, error: "内容为空" } }),
        rec(7, { type: "tool_result", name: "dismiss_adventurer", result: { ok: true } }),
        rec(8, { type: "tool_call", name: "future_custom_tool", arguments: {} }),
    ]);
    const timeline = screen.getByRole("region", { name: "观战行动时间线" });
    expect(within(timeline).getAllByRole("button", { name: "定位行动 查看制作配方" })).toHaveLength(1);
    expect(within(timeline).getByRole("button", { name: "定位行动 刷新怪物" })).toBeVisible();
    expect(within(timeline).getByRole("button", { name: "定位行动 记录备忘" })).toBeVisible();
    expect(within(timeline).getByRole("button", { name: "定位行动 解雇冒险者" })).toBeVisible();
    expect(within(timeline).getByRole("button", { name: "定位行动 预览怪物刷新" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("筛选本回合工具"), { target: { value: "preview_monster_refresh" } });
    expect(within(timeline).getAllByRole("button", { name: /定位行动/ })).toHaveLength(1);
    const summary = screen.getByText(/按工具汇总/).closest("summary")!;
    fireEvent.click(summary);
    const stats = screen.getByRole("region", { name: "本回合工具汇总" });
    expect(within(stats).getByRole("button", { name: "筛选工具 查看制作配方" }).closest("tr")).toHaveTextContent("1");
    fireEvent.click(within(stats).getByRole("button", { name: "筛选工具 future_custom_tool" }));
    expect(screen.getByLabelText("筛选本回合工具")).toHaveValue("future_custom_tool");
    expect(within(timeline).getByRole("button", { name: "定位行动 future_custom_tool" })).toHaveTextContent("执行中");
    fireEvent.change(screen.getByLabelText("筛选本回合状态"), { target: { value: "succeeded" } });
    expect(within(timeline).queryByRole("button", { name: /定位行动/ })).not.toBeInTheDocument();
});

it("旧式刷新结果没有重复参数时，预览仍可筛选且不计为付费刷新", () => {
    setup([
        rec(2, { type: "tool_call", name: "refresh_monsters", call_id: "preview", arguments: { locked_monster_ids: [], preview: true } }),
        rec(3, { type: "tool_result", name: "refresh_monsters", call_id: "preview", result: { ok: true } }),
    ]);
    fireEvent.change(screen.getByLabelText("筛选本回合工具"), { target: { value: "category:preview" } });
    const timeline = screen.getByRole("region", { name: "观战行动时间线" });
    expect(within(timeline).getByRole("button", { name: "定位行动 预览怪物刷新" })).toHaveTextContent("预览");
    fireEvent.change(screen.getByLabelText("筛选本回合工具"), { target: { value: "category:refresh" } });
    expect(within(timeline).queryByRole("button", { name: /定位行动/ })).not.toBeInTheDocument();
});

it("派遣参数按成员和怪物逐项展示，实际战果与输入分开", () => {
    setup([rec(2, { type: "tool_result", name: "end_turn", arguments: { hunts: [{ adventurer_id: "hero", monster_id: "slime" }] },
        result: { ok: true, turn_result: { battles: [{ adventurer_name: "验收剑士", monster_name: "史莱姆", won: true }] } } })]);
    const input = screen.getByRole("region", { name: "输入参数" });
    fireEvent.click(input.closest("details")!.querySelector("summary")!);
    expect(within(input).getByText("派遣 1")).toBeVisible();
    expect(within(input).getByText("验收剑士", { exact: true })).toBeVisible();
    expect(within(input).getByText("史莱姆", { exact: true })).toBeVisible();
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("本轮已结算");
    const battles = within(screen.getByRole("region", { name: "所选行动详情" })).getByRole("region", { name: "战斗结果" });
    expect(within(battles).getByLabelText("战果概览")).toHaveTextContent("场数1胜1负0");
    expect(within(battles).getByRole("article", { name: "验收剑士 → 史莱姆" })).toHaveTextContent("胜利");
});

it("实时追加只标记新行动，暂停期间的新记录不会在恢复时补播", () => {
    const initialFrames = watchFrames([
        rec(1, { type: "turn_started", observation }),
        rec(2, { type: "tool_result", name: "allocate_experience", result: { ok: true } }),
    ]);
    const nextFrames = watchFrames([
        rec(1, { type: "turn_started", observation }),
        rec(2, { type: "tool_result", name: "allocate_experience", result: { ok: true } }),
        rec(3, { type: "tool_result", name: "purchase_upgrade", result: { ok: true } }),
    ]);
    const pausedFrames = watchFrames([
        rec(1, { type: "turn_started", observation }),
        rec(2, { type: "tool_result", name: "allocate_experience", result: { ok: true } }),
        rec(3, { type: "tool_result", name: "purchase_upgrade", result: { ok: true } }),
        rec(4, { type: "tool_result", name: "write_memo", result: { ok: true } }),
    ]);
    const node = (frames: WatchFrame[], following = true) => <ActionTimeline frames={frames} frame={frames.at(-1)} active following={following}
        rolledBack={false} onFrame={() => {}} renderDetails={() => null} />;
    const view = render(node(initialFrames));
    expect(document.querySelectorAll(".motion-new")).toHaveLength(0);
    view.rerender(node(nextFrames));
    expect(screen.getByRole("button", { name: "定位行动 分配经验" })).not.toHaveClass("motion-new");
    expect(screen.getByRole("button", { name: "定位行动 购买升级" })).toHaveClass("motion-new");
    view.rerender(node(pausedFrames, false));
    expect(screen.getByRole("button", { name: "定位行动 记录备忘" })).not.toHaveClass("motion-new");
    view.rerender(node(pausedFrames, true));
    expect(screen.getByRole("button", { name: "定位行动 记录备忘" })).not.toHaveClass("motion-new");
});

it("空输入与缺失输入明确区分，正文按文本渲染且不截掉长备忘", () => {
    const text = '<img src=x onerror="alert(1)">\n' + "完整备忘".repeat(150);
    const { select } = setup([
        rec(2, { type: "tool_result", name: "get_party", arguments: {}, result: { adventurers: [] } }),
        rec(3, { type: "tool_result", name: "write_memo", result: { ok: true } }),
        rec(4, { type: "tool_result", name: "write_memo", arguments: { content: text }, result: { ok: true, memo: { content: text } } }),
    ], 2);
    expect(screen.getByRole("region", { name: "输入参数" })).toHaveTextContent("无输入参数");
    select(3);
    expect(screen.getByRole("region", { name: "输入参数" })).toHaveTextContent("没有保存这次操作的参数");
    select(4);
    expect(screen.getByRole("region", { name: "输入参数" }).textContent).toContain(text);
    expect(screen.getByRole("region", { name: "所选行动详情" }).querySelector(".return-memo pre")!.textContent).toBe(text);
    expect(document.querySelector("img")).not.toBeInTheDocument();
});
