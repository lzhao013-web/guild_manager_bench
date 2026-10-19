import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Theme } from "@radix-ui/themes";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { WatchRun, livePosition } from "./WatchRun";
import { stateChanges, watchFrames } from "./watchModel";
import type { WatchPage, WatchPosition, WatchRecord } from "./watchModel";
import type { EventRecord, Job, Observation } from "./types";
import type { WatchFocus } from "./sceneNavigation";

const initial: Observation = {
    gold: 100,
    experience_pool: 0,
    materials: {},
    adventurers: [],
    party_size_limit: 3,
    turn: 1,
    max_turns: 8,
};
const recruited: Observation = {
    ...initial,
    gold: 60,
    adventurers: [
        {
            adventurer_id: "a1",
            template_id: "warrior",
            name: "验收剑士",
            level: 1,
            resources: { current_hp: 80, current_mp: 10 },
            effective_stats: { hp: 80, mp: 10, attack: 12 },
        },
    ],
};
const record = (cursor: number, event: EventRecord["event"]): WatchRecord => ({
    cursor,
    after: cursor - 1,
    at: "2026-10-18T10:00:00Z",
    event,
});
const first: WatchPage = {
    cursor: 5,
    selected: 1,
    attempts: [
        {
            id: 1,
            turn: 1,
            attempt: 1,
            status: "completed",
            score: 0,
            actions: 1,
            at: "2026-10-18T10:00:00Z",
            updated_at: "2026-10-18T10:00:00Z",
        },
    ],
    records: [
        record(1, { type: "turn_started", turn: 1, observation: initial }),
        record(2, { type: "model_request", turn: 1, step: 1 }),
        record(3, {
            type: "model_response",
            turn: 1,
            step: 1,
            text: "先招募一位剑士。",
            usage: { input_tokens: 10 },
        }),
        record(4, {
            type: "tool_result",
            turn: 1,
            name: "recruit_adventurer",
            result: { ok: true, _observation_after: recruited },
        }),
        record(5, { type: "turn_scored", turn: 1, rank_score: 0 }),
    ],
};
const next: WatchPage = {
    ...first,
    cursor: 6,
    records: [
        ...first.records,
        record(6, {
            type: "tool_result",
            turn: 1,
            name: "write_memo",
            result: { ok: true, _observation_after: { ...recruited, gold: 25 } },
        }),
    ],
};
const job = (id = "one"): Job => ({
    id,
    name: `构造观战样例 ${id}`,
    batch_id: "batch",
    batch_name: "非真实评测",
    status: "running",
    phase: "waiting_model",
    phase_started_at: "2026-10-18T10:00:00Z",
    created_at: "2026-10-18T10:00:00Z",
    updated_at: "2026-10-18T10:00:00Z",
    started_at: null,
    ended_at: null,
    config: {
        preset: "default",
        provider: "openai",
        model: "test-model",
        base_url: "http://127.0.0.1:1/v1",
        reasoning_effort: null,
        timeout: 180,
        objective: "",
        max_tool_calls_per_turn: 24,
        game_seed: null,
        scoring_seed: null,
    },
    effective_config: {},
    data: {},
    archive_id: null,
    turn: 1,
    max_turns: 8,
    last_score: 0,
    scores: [],
    calls: 1,
    tools: 1,
    usage: { input_tokens: null, output_tokens: null, reported: 0, responses: 0 },
    retries: 0,
    tool_failures: 0,
    error: null,
    notice: null,
    cursor: 5,
    checkpoint_at: null,
    checkpoint_error: null,
    observation: recruited,
    budget: null,
    recent_actions: [],
});
function setup(count = 1) {
    let live = first;
    const requests: string[] = [];
    const onStop = vi.fn();
    const onFocus = vi.fn();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
        const path = String(input);
        requests.push(path);
        if (path.includes("/events?"))
            return new Response(
                JSON.stringify({
                    events: [
                        {
                            event: {
                                type: "model_request",
                                request: { messages: ["构造验收输入"] },
                            },
                        },
                    ],
                    cursor: 100,
                    has_more: false,
                }),
                { status: 200 },
            );
        return new Response(JSON.stringify(path.includes("attempt=") ? first : live), {
            status: 200,
        });
    });
    const client = new QueryClient({
        defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    function Harness() {
        const [positions, setPositions] = useState<Record<string, WatchPosition>>({});
        const [focus, setFocus] = useState(false);
        const [focusTarget, setFocusTarget] = useState<WatchFocus>();
        return (
            <QueryClientProvider client={client}>
                <Theme>
                    <button onClick={() => setFocus(!focus)}>切换专注</button>
                    {Array.from({ length: focus ? 1 : count }, (_, i) => {
                        const id = String(i);
                        return (
                            <WatchRun
                                key={`${id}-${focus}`}
                                job={job(id)}
                                compact={!focus}
                                initialFocus={focus ? focusTarget : undefined}
                                onFocus={!focus ? target => { onFocus(target); setFocusTarget(target); setFocus(true); } : undefined}
                                position={positions[id] || livePosition}
                                onPosition={(p) => setPositions((prev) => ({ ...prev, [id]: p }))}
                                onStop={onStop}
                                onClone={vi.fn()}
                            />
                        );
                    })}
                </Theme>
            </QueryClientProvider>
        );
    }
    render(<Harness />);
    return {
        client,
        requests,
        onStop,
        onFocus,
        update: async (value = next) => {
            live = value;
            await act(() => client.refetchQueries({ queryKey: ["watch"] }));
        },
    };
}

it("点击历史行动同步局面，后台新事件不会改变已冻结的位置，返回实时后追上", async () => {
    const { update, requests } = setup();
    await screen.findByText("验收剑士");
    fireEvent.change(screen.getByRole("slider", { name: "选择观战步骤" }), { target: { value: "0" } });
    expect(screen.getByText("历史局面", { exact: true })).toBeVisible();
    expect(screen.getByText("此时尚未招募冒险者")).toBeVisible();
    expect(within(screen.getByRole("region", { name: "行动摘要" })).queryByRole("button", { name: "定位行动 招募冒险者" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看完整模型输出" })).toHaveTextContent("尚无模型调用");
    expect(within(screen.getByLabelText("当前观看位置的资源")).getByText("100")).toBeVisible();
    await update();
    expect(within(screen.getByLabelText("当前观看位置的资源")).getByText("100")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /返回实时/ }));
    expect(screen.getByText("验收剑士")).toBeVisible();
    expect(within(screen.getByLabelText("当前观看位置的资源")).getByText("25")).toBeVisible();
    expect(requests.every((path) => path.includes("/watch"))).toBe(true);
});

it("两路独立跟随，进入专注再返回保留原位置", async () => {
    const { update, onStop } = setup(2);
    await screen.findAllByText("验收剑士");
    const firstPanel = screen.getByRole("region", { name: "构造观战样例 0 的运行详情" });
    const secondPanel = screen.getByRole("region", { name: "构造观战样例 1 的运行详情" });
    fireEvent.click(within(firstPanel).getByRole("button", { name: "暂停跟随" }));
    expect(onStop).not.toHaveBeenCalled();
    await update();
    expect(within(firstPanel).getByText("60", { exact: true })).toBeVisible();
    expect(await within(secondPanel).findByText("25", { exact: true })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "切换专注" }));
    await screen.findByText("历史局面", { exact: true });
    expect(screen.getByText("60", { exact: true })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "切换专注" }));
    await waitFor(() => expect(screen.getAllByText("验收剑士")).toHaveLength(2));
    expect(
        within(screen.getByRole("region", { name: "构造观战样例 0 的运行详情" })).getByText("60", {
            exact: true,
        }),
    ).toBeVisible();
});

it("多路只挂载摘要，点选人物后直达对应模块和对象，不读取完整模型输入", async () => {
    const { onFocus, requests } = setup();
    fireEvent.click(await screen.findByRole("button", { name: "查看成员 验收剑士" }));
    expect(onFocus).toHaveBeenCalledWith({ module: "party", selection: { kind: "member", id: "a1" } });
    expect(await screen.findByRole("heading", { name: "编队模块标题" })).toBeVisible();
    expect(document.querySelector(".scene-inline-inspector")).toBeInTheDocument();
    expect(requests.every(path => path.includes("/watch"))).toBe(true);
});

it("点击模型摘要直达模型模块，完整输入仍按需读取", async () => {
    const { onFocus, requests } = setup();
    fireEvent.click(await screen.findByRole("button", { name: "查看完整模型输出" }));
    expect(onFocus).toHaveBeenCalledWith({ module: "models" });
    expect(await screen.findByRole("heading", { name: "模型输出模块标题" })).toBeVisible();
    expect(within(screen.getByRole("region", { name: "所选位置的模型调用" })).getByText("先招募一位剑士。")).toBeVisible();
    expect(requests.every(path => path.includes("/watch"))).toBe(true);
});

it("连续查询不会刷走多路摘要中的失败操作，也不挂载长日志滚动区", async () => {
    const { update } = setup();
    await screen.findByText("验收剑士");
    const failure = record(7, { type: "tool_result", turn: 1, name: "equip_item", result: { ok: false, error: "构造装备失败" } });
    await update({ ...first, cursor: 30, records: [...first.records, failure,
        ...Array.from({ length: 20 }, (_, i) => record(i + 8, { type: "tool_result", turn: 1, name: "get_party", result: { ok: true } }))] });
    expect(await within(screen.getByRole("region", { name: "行动摘要" })).findByRole("button", { name: "定位行动 穿戴装备" })).toHaveTextContent("构造装备失败");
    expect(document.querySelector(".spectator-timeline-scroll, .spectator-model-content, .scene-navigation")).not.toBeInTheDocument();
});

it("按回合接口加载完整历史，而不是依赖当前面板中的最近事件", async () => {
    const { requests } = setup();
    await screen.findByText("验收剑士");
    fireEvent.change(screen.getByLabelText("选择观战回合"), { target: { value: "1" } });
    await waitFor(() =>
        expect(requests.some((path) => path.includes("watch?attempt=1"))).toBe(true),
    );
    await screen.findByText("历史局面", { exact: true });
    expect(screen.getByRole("button", { name: /返回实时/ })).toBeEnabled();
});

it("模型模块返回总览后不自动读取新调用的完整输入，重新展开后再读取", async () => {
    const { requests, update } = setup();
    await screen.findByText("验收剑士");
    fireEvent.click(screen.getByRole("button", { name: "切换专注" }));
    fireEvent.click(await screen.findByRole("button", { name: "展开模型输出" }));
    fireEvent.click(screen.getByText("完整模型输入", { exact: true }));
    await waitFor(() =>
        expect(requests.filter((path) => path.includes("/events?")).length).toBe(1),
    );
    fireEvent.click(screen.getByRole("button", { name: "返回总览" }));
    await update({
        ...next,
        cursor: 8,
        records: [
            ...first.records,
            record(7, { type: "model_request", turn: 1, step: 2 }),
            record(8, { type: "model_delta", text: "新的构造模型正文" }),
        ],
    });
    expect(
        await within(screen.getByRole("region", { name: "模型输出概览" })).findByText(
            "新的构造模型正文",
        ),
    ).toBeVisible();
    expect(requests.filter((path) => path.includes("/events?")).length).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "展开模型输出" }));
    await waitFor(() =>
        expect(requests.filter((path) => path.includes("/events?")).length).toBe(2),
    );
});

it("购买对象不会被资源差值盖掉，时间线合并调用结果并折叠查询，点击仍定位真实局面", async () => {
    const { update, requests } = setup();
    await screen.findByText("验收剑士");
    const changed = { ...recruited, gold: 20 };
    await update({ ...first, cursor: 11, records: [...first.records,
        record(6, { type: "tool_call", name: "purchase_upgrade", call_id: "buy", arguments: { upgrade_id: "training" } }),
        record(7, { type: "tool_result", name: "purchase_upgrade", call_id: "buy", result: { ok: true,
            event: { summary: "购买全局加成 验收训练场", changes: [{ kind: "resource", label: "金币", before: 60, after: 20 }] }, _observation_after: changed } }),
        record(8, { type: "tool_call", name: "get_party", call_id: "q1" }),
        record(9, { type: "tool_result", name: "get_party", call_id: "q1", result: { ok: true, adventurers: changed.adventurers } }),
        record(10, { type: "tool_call", name: "get_party", call_id: "q2" }),
        record(11, { type: "tool_result", name: "get_party", call_id: "q2", result: { ok: true, adventurers: changed.adventurers } }),
    ] });
    const summary = screen.getByRole("region", { name: "行动摘要" });
    expect(await within(summary).findByRole("button", { name: "定位行动 购买全局加成 验收训练场" })).toHaveTextContent("金币 60 → 20");
    fireEvent.click(screen.getByRole("button", { name: "切换专注" }));
    fireEvent.click(await screen.findByRole("button", { name: "展开最近行动" }));
    const timeline = screen.getByRole("region", { name: "观战行动时间线" });
    expect(within(timeline).getAllByRole("button", { name: "定位行动 购买全局加成 验收训练场" })).toHaveLength(1);
    fireEvent.click(within(timeline).getByRole("button", { name: "定位行动 购买全局加成 验收训练场" }));
    expect(screen.getByText("历史局面", { exact: true })).toBeVisible();
    expect(within(screen.getByLabelText("当前观看位置的资源")).getByText("20", { exact: true })).toBeVisible();
    expect(screen.getByRole("region", { name: "所选行动详情" })).toHaveTextContent("金币 60 → 20");
    expect(within(timeline).getByText(/连续查询 · 2 次/)).toBeVisible();
    for (const query of within(timeline).getAllByRole("button", { name: "定位行动 查看队伍" }))
        expect(query.closest("details")).not.toHaveAttribute("open");
    expect(requests.every(path => path.includes("/watch"))).toBe(true);
});

it("操作前后的变化来自快照，零分不当作缺失，读取操作不伪造变化", () => {
    const frames = watchFrames(first.records);
    expect(frames.at(-1)?.score).toBe(0);
    expect(frames[0].score).toBeNull();
    expect(watchFrames(first.records, 7)[0].score).toBe(7);
    expect(stateChanges(initial, recruited)).toEqual(["金币 -40", "招募 验收剑士"]);
    expect(stateChanges(recruited, recruited)).toEqual([]);
    expect(frames[0].observation).toEqual(initial);
    expect(frames[3].observation).toEqual(recruited);
});

it("终态事件结束评分提示，并使用实际返回的最终评分", () => {
    const frames = watchFrames([
        ...first.records,
        record(6, { type: "scoring_started", scope: "final" }),
        record(7, { type: "run_completed", score: { rank_score: 12 } }),
    ]);
    expect(frames.at(-1)?.title).toBe("运行已完成");
    expect(frames.at(-1)?.score).toBe(12);
    expect(frames.at(-1)?.observation).toEqual(recruited);
});
