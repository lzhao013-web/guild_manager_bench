import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Theme } from "@radix-ui/themes";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { RunDetail } from "./RunDetail";
import { testJob } from "./test-jobs";
import type { WatchPage, WatchRecord } from "./watchModel";
import type { Job, Observation } from "./types";

const initial: Observation = { gold: 100, experience_pool: 0, materials: {}, adventurers: [], party_size_limit: 3, turn: 1, max_turns: 8 };
const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-10-19T10:00:00Z", event });
function setup(records: WatchRecord[], rawRecords: WatchRecord[] = [], job: Job = testJob("sidebar", { status: "completed", cursor: 10 }),
    actions: Pick<Parameters<typeof RunDetail>[0], "onCurves" | "onPin" | "onFocus" | "onClose" | "pinned"> = {}) {
    const requests: string[] = [];
    const page: WatchPage = { cursor: 10, selected: 1, attempts: [], records };
    vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
        const path = String(input);
        requests.push(path);
        // 原始事件窗口为空，行动必须来自完整的只读回合投影。
        return new Response(JSON.stringify(path.endsWith("/watch") ? page : { events: rawRecords, cursor: 10, has_more: false }));
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<QueryClientProvider client={client}><Theme><RunDetail job={job} runId={job.id}
        onStop={vi.fn()} onClone={vi.fn()} {...actions} /></Theme></QueryClientProvider>);
    return requests;
}

it.each([false, true])("运行操作显示文字，关注状态为 %s 时按钮仍执行原有操作", pinned => {
    const actions = { onCurves: vi.fn(), onFocus: vi.fn(), onPin: vi.fn(), onClose: vi.fn(), pinned };
    setup([], [], testJob("controls"), actions);
    const group = screen.getByRole("group", { name: "运行操作" });
    const focus = within(group).getByRole("button", { name: "专注查看" });
    expect(focus).toHaveTextContent("专注查看");
    expect(focus).toHaveClass("is-primary");
    const curves = within(group).getByRole("button", { name: "查看运行曲线" });
    expect(curves).toHaveTextContent("曲线");
    const pin = within(group).getByRole("button", { name: pinned ? "取消关注" : "关注此 run" });
    expect(pin).toHaveTextContent(pinned ? "已关注" : "关注");
    expect(pin).toHaveAttribute("aria-pressed", String(pinned));
    const close = within(group).getByRole("button", { name: "关闭详情" });
    expect(close).toHaveTextContent("");
    fireEvent.click(focus);
    fireEvent.click(curves);
    fireEvent.click(pin);
    fireEvent.click(close);
    for (const callback of [actions.onFocus, actions.onCurves, actions.onPin, actions.onClose]) expect(callback).toHaveBeenCalledOnce();
});

it("侧栏提供整局时间线直达入口，不改变后台运行", () => {
    const onFocus = vi.fn();
    setup([], [], testJob("timeline-shortcut"), { onFocus });
    fireEvent.click(screen.getByRole("button", { name: "整局时间线" }));
    expect(onFocus).toHaveBeenCalledWith({ module: "actions", actionView: "timeline" });
});

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
    expect(screen.getByText("金币 -60", { exact: true })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "行动时间线" }));
    expect(screen.getAllByText("购买全局加成 验收训练场", { exact: true })).toHaveLength(1);
    expect(screen.getAllByRole("region", { name: "返回的队伍" })).toHaveLength(2);
    expect(screen.getByRole("option", { name: "查看队伍（2）" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "资源变化" })).toBeVisible();
    expect(screen.getByRole("region", { name: "实际资源变化" })).toHaveTextContent("金币-60");
    expect(screen.getByText("完整实际变化（含属性前后值）").closest("details")).not.toHaveAttribute("open");
    fireEvent.change(screen.getByLabelText("筛选侧栏工具"), { target: { value: "get_party" } });
    expect(screen.queryByText("购买全局加成 验收训练场", { exact: true })).not.toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: "返回的队伍" })).toHaveLength(2);
    expect(requests.some(path => path.endsWith("/watch"))).toBe(true);
    expect(requests.every(path => path.includes("/watch") || path.includes("/events?"))).toBe(true);
});

it("摘要不携带局面时，侧栏从回合快照显示最新队伍和资源", async () => {
    const after: Observation = { ...initial, gold: 40, experience_pool: 25, adventurers: [{
        adventurer_id: "member", template_id: "warrior", name: "局面接口中的剑士", level: 2,
        resources: { current_hp: 60, current_mp: 15 }, effective_stats: { hp: 100, mp: 20 },
    }] };
    setup([rec(1, { type: "turn_started", observation: initial }),
        rec(2, { type: "tool_result", name: "recruit_adventurer", result: { ok: true, _observation_after: after } })]);
    const party = screen.getByRole("heading", { name: "当前队伍" }).closest("section")!;
    expect(await within(party).findByText("局面接口中的剑士")).toBeVisible();
    expect(party).toHaveTextContent("1 / 3");
    expect(party).toHaveTextContent("金币 40");
    expect(party).toHaveTextContent("经验池 25");
    expect(party).toHaveTextContent("HP 60 / 100");
    expect(party).toHaveTextContent("MP 15 / 20");
});

it("侧栏只查询时不把成功查询描述为经营成果", async () => {
    setup([rec(1, { type: "turn_started", observation: initial }),
        rec(2, { type: "tool_result", name: "get_party", result: { ok: true, adventurers: [] } })]);
    expect(await screen.findByText("目前只进行了 1 次查询，尚无经营动作。", { exact: true })).toBeVisible();
    expect(within(screen.getByRole("region", { name: "构造的同名模型 的运行详情" })).getByText("返回 0 位成员", { exact: true })).toBeVisible();
});

function rerunJob(patch: Partial<Job> = {}): Job {
    const base = testJob("rerun", { status: "completed", cursor: 10 });
    return { ...base, config: { ...base.config, rerun_run_id: "20261020-171620-522173_gpt-6.1-sol", rerun_from_turn: 18 }, data: { protocol_status: "mixed" }, ...patch };
}

it("重跑和跨版本说明收进标题内的小标记，不占正文上方的提示框", () => {
    const requests = setup([], [], rerunJob());
    const sidebar = screen.getByRole("region", { name: "构造的同名模型 的运行详情" });
    const notices = within(sidebar).getByRole("group", { name: "重跑与版本信息" });
    expect(notices.closest(".detail-header")).not.toBeNull();
    expect(within(notices).getByRole("button", { name: "T18 分支重跑，查看来源" })).toHaveAttribute("aria-expanded", "false");
    expect(within(notices).getByRole("button", { name: "跨版本执行，查看说明" })).toHaveClass("is-warning");
    expect(sidebar.querySelector(":scope > .callout")).toBeNull();
    expect(screen.queryByText(/原记录保持不变/)).not.toBeInTheDocument();
    expect(screen.queryByText(/不作为同版本标准评测比较/)).not.toBeInTheDocument();
    expect(requests.every(path => path.includes("/watch") || path.includes("/events?"))).toBe(true);
});

it("悬停查看完整来源，可移入浮层阅读，切换版本标记只显示对应说明", async () => {
    setup([], [], rerunJob());
    const rerun = screen.getByRole("button", { name: "T18 分支重跑，查看来源" });
    fireEvent.pointerEnter(rerun, { pointerType: "mouse" });
    const detail = await screen.findByRole("dialog", { name: "T18 分支重跑详情" });
    expect(detail).toHaveTextContent("来源：20261020-171620-522173_gpt-6.1-sol。原记录保持不变。");
    expect(rerun).toHaveAttribute("aria-controls", detail.id);
    expect(screen.getByRole("region", { name: "构造的同名模型 的运行详情" })).not.toContainElement(detail);
    fireEvent.pointerLeave(rerun);
    fireEvent.pointerEnter(detail);
    await act(() => new Promise(resolve => setTimeout(resolve, 150)));
    expect(detail).toBeVisible();
    fireEvent.pointerLeave(detail);
    const mixed = screen.getByRole("button", { name: "跨版本执行，查看说明" });
    fireEvent.pointerEnter(mixed, { pointerType: "mouse" });
    const warning = await screen.findByRole("dialog", { name: "跨版本详情" });
    expect(warning).toHaveTextContent("这次运行续跑时使用了不同版本的游戏规则。");
    expect(warning).not.toHaveTextContent("20261020-171620");
    expect(rerun).toHaveAttribute("aria-expanded", "false");
    fireEvent.pointerLeave(mixed);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("键盘聚焦或点击可固定说明，支持 Escape、关闭和点外部收起", async () => {
    setup([], [], rerunJob());
    const badge = screen.getByRole("button", { name: "跨版本执行，查看说明" });
    fireEvent.focus(badge);
    expect(await screen.findByRole("dialog", { name: "跨版本详情" })).toBeVisible();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(badge);
    expect(await screen.findByRole("dialog")).toBeVisible();
    fireEvent.pointerLeave(badge);
    await act(() => new Promise(resolve => setTimeout(resolve, 150)));
    expect(screen.getByRole("dialog")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "关闭运行信息" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(badge);
    expect(await screen.findByRole("dialog")).toBeVisible();
    fireEvent.pointerDown(document.body);
    fireEvent.click(document.body);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("普通运行不增加信息行，真实运行错误和存档失败仍完整显示", () => {
    setup([], [], testJob("failed", { status: "failed", error: "模型请求失败", checkpoint_error: "检查点写入失败" }));
    expect(screen.queryByRole("group", { name: "重跑与版本信息" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("模型请求失败");
    expect(screen.getByRole("alert")).toHaveTextContent("存档失败：检查点写入失败");
    expect(screen.getByText("模型请求失败")).toHaveClass("callout", "error");
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
