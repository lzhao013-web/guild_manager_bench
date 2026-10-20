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

it("默认展示可读输入、原值和完整执行结果，点击仍定位真实结果帧", () => {
    const { frames, onFrame } = setup([
        rec(2, { type: "tool_call", name: "allocate_experience", call_id: "xp", arguments: { adventurer_id: "hero", amount: 12 } }),
        rec(3, { type: "tool_result", name: "allocate_experience", call_id: "xp", result: { ok: true, event: { summary: "为验收剑士分配经验", changes: [
            { kind: "resource", label: "经验池", before: 50, after: 38 },
            { kind: "adventurer", label: "验收剑士 等级", before: 1, after: 2 },
        ] } } }),
    ]);
    const detail = screen.getByRole("region", { name: "所选行动详情" });
    const input = within(detail).getByRole("region", { name: "输入参数" });
    expect(within(input).getByText("验收剑士", { exact: true })).toBeVisible();
    expect(within(input).getByText("原值：hero", { exact: true })).toBeVisible();
    expect(within(input).getByText("12", { exact: true })).toBeVisible();
    expect(within(detail).getByRole("region", { name: "执行结果" })).toHaveTextContent("经验池 50 → 38（-12）");
    expect(within(detail).getByRole("region", { name: "执行结果" })).toHaveTextContent("等级 1 → 2（+1）");
    expect(input.closest("details")).toBeNull();
    const row = screen.getByRole("button", { name: "定位行动 为验收剑士分配经验" });
    expect(row).toHaveTextContent("输入 · 成员：验收剑士，分配经验数量：12");
    expect(row).toHaveTextContent("等级 1 → 2");
    fireEvent.click(row);
    expect(onFrame).toHaveBeenCalledWith(frames.at(-1));
    expect(frames.at(-1)!.record.event).not.toHaveProperty("arguments");
});

it("回看调用时显示当时输入和执行中，不提前展示之后的结果", () => {
    const { select } = setup([
        rec(2, { type: "tool_call", name: "allocate_experience", call_id: "xp", arguments: { adventurer_id: "hero", amount: 10 } }),
        rec(3, { type: "tool_result", name: "allocate_experience", call_id: "xp", result: { ok: false, error: "余额不足" } }),
    ]);
    select(2);
    const detail = screen.getByRole("region", { name: "所选行动详情" });
    expect(within(detail).getByRole("region", { name: "输入参数" })).toHaveTextContent("10");
    expect(within(detail).getByRole("region", { name: "执行结果" })).toHaveTextContent("尚未记录执行结果");
    expect(within(detail).queryByText("余额不足", { exact: true })).not.toBeInTheDocument();
});

it("派遣参数按成员和怪物逐项展示，实际战果与输入分开", () => {
    setup([rec(2, { type: "tool_result", name: "end_turn", arguments: { hunts: [{ adventurer_id: "hero", monster_id: "slime" }] },
        result: { ok: true, turn_result: { battles: [{ adventurer_name: "验收剑士", monster_name: "史莱姆", won: true }] } } })]);
    const input = screen.getByRole("region", { name: "输入参数" });
    expect(within(input).getByText("派遣 1")).toBeVisible();
    expect(within(input).getByText("验收剑士", { exact: true })).toBeVisible();
    expect(within(input).getByText("史莱姆", { exact: true })).toBeVisible();
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("验收剑士 → 史莱姆：胜利");
});

it("空输入与缺失输入明确区分，正文按文本渲染且不截掉长备忘", () => {
    const text = '<img src=x onerror="alert(1)">\n' + "完整备忘".repeat(150);
    const { select } = setup([
        rec(2, { type: "tool_result", name: "get_party", arguments: {}, result: { adventurers: [] } }),
        rec(3, { type: "tool_result", name: "write_memo", result: { ok: true } }),
        rec(4, { type: "tool_result", name: "write_memo", arguments: { content: text }, result: { ok: true, memo: { content: text } } }),
    ], 2);
    expect(screen.getByRole("region", { name: "输入参数" })).toHaveTextContent("无输入参数（已记录为空对象）");
    select(3);
    expect(screen.getByRole("region", { name: "输入参数" })).toHaveTextContent("原日志未记录输入参数");
    select(4);
    expect(screen.getByRole("region", { name: "输入参数" }).textContent).toContain(text);
    expect(document.querySelector("img")).not.toBeInTheDocument();
});
