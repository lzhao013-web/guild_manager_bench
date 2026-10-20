import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Theme } from "@radix-ui/themes";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { RunDetail } from "./RunDetail";
import { testJob } from "./test-jobs";
import type { WatchPage, WatchRecord } from "./watchModel";
import type { Observation } from "./types";

const initial: Observation = { gold: 100, experience_pool: 0, materials: {}, adventurers: [], party_size_limit: 3, turn: 1, max_turns: 8 };
const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-10-19T10:00:00Z", event });
function setup(records: WatchRecord[], rawRecords: WatchRecord[] = []) {
    const requests: string[] = [];
    const page: WatchPage = { cursor: 10, selected: 1, attempts: [], records };
    vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
        const path = String(input);
        requests.push(path);
        // 原始事件窗口为空，行动必须来自完整的只读回合投影。
        return new Response(JSON.stringify(path.endsWith("/watch") ? page : { events: rawRecords, cursor: 10, has_more: false }));
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<QueryClientProvider client={client}><Theme><RunDetail job={testJob("sidebar", { status: "completed", cursor: 10 })}
        onStop={vi.fn()} onClone={vi.fn()} /></Theme></QueryClientProvider>);
    return requests;
}

it("运行列表侧栏也保留具体对象与影响，不再被原始 600 事件窗口和连续查询覆盖", async () => {
    const requests = setup([
        rec(1, { type: "turn_started", observation: initial }),
        rec(2, { type: "tool_call", name: "purchase_upgrade", call_id: "buy" }),
        rec(3, { type: "tool_result", name: "purchase_upgrade", call_id: "buy", result: { ok: true,
            event: { summary: "购买全局加成 验收训练场", changes: [{ kind: "resource", label: "金币", before: 100, after: 40 }] },
            _observation_after: { ...initial, gold: 40 } } }),
        rec(4, { type: "tool_result", name: "get_party", result: { ok: true, adventurers: [] } }),
        rec(5, { type: "tool_result", name: "get_party", result: { ok: true, adventurers: [] } }),
    ]);
    expect(await screen.findByText("购买全局加成 验收训练场", { exact: true })).toBeVisible();
    expect(screen.getByText("金币 100 → 40（-60）", { exact: true })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "行动时间线" }));
    expect(screen.getAllByText("购买全局加成 验收训练场", { exact: true })).toHaveLength(1);
    expect(screen.getByText("连续查询 · 2 次", { exact: true }).closest("details")).not.toHaveAttribute("open");
    expect(screen.getByRole("heading", { name: "实际局面变化" })).toBeVisible();
    expect(screen.getAllByText("金币 100 → 40（-60）", { exact: true }).every(node => !node.closest("details"))).toBe(true);
    expect(requests.some(path => path.endsWith("/watch"))).toBe(true);
    expect(requests.every(path => path.includes("/watch") || path.includes("/events?"))).toBe(true);
});

it("侧栏只查询时不把成功查询描述为经营成果", async () => {
    setup([rec(1, { type: "turn_started", observation: initial }),
        rec(2, { type: "tool_result", name: "get_party", result: { ok: true, adventurers: [] } })]);
    expect(await screen.findByText("目前只进行了 1 次查询，尚无经营动作。", { exact: true })).toBeVisible();
    expect(within(screen.getByRole("region", { name: "构造的同名模型 的运行详情" })).getByText("返回 0 位成员", { exact: true })).toBeVisible();
});

it("列表侧栏的模型正文也附带本次调用统计，不用整局累计代替", async () => {
    setup([], [
        rec(1, { type: "model_request", turn: 1, step: 1 }),
        rec(2, { type: "model_response", turn: 1, step: 1, text: "侧栏构造正文", usage: { input_tokens: 7, output_tokens: 4 }, timing: { duration_ms: 200 } }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: /模型/ }));
    const stats = await screen.findByRole("group", { name: "本次模型调用概览" });
    expect(stats).toHaveTextContent("输入 7");
    expect(stats).toHaveTextContent("输出 4 Token");
    expect(stats).toHaveTextContent("耗时 200 ms");
    expect(screen.getByText("侧栏构造正文")).toBeVisible();
});
