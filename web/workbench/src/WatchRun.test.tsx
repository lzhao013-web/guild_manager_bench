import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Theme } from "@radix-ui/themes";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
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
    budget: null,
    recent_actions: [],
});
function setup(count = 1, initialPage = first, historyPage = first, status: Job["status"] = "running") {
    let live = initialPage;
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
        return new Response(JSON.stringify(path.includes("attempt=") ? historyPage : live), {
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
                                job={{ ...job(id), status }}
                                runId={id}
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
    const view = render(<Harness />);
    return {
        client,
        setStatus: (value: Job["status"]) => { status = value; view.rerender(<Harness />); },
        requests,
        onStop,
        onFocus,
        update: async (value = next) => {
            live = value;
            await act(() => client.refetchQueries({ queryKey: ["watch"] }));
        },
    };
}

it.each([
    ["completed", "最终局面"], ["failed", "最后记录局面"], ["interrupted", "最后记录局面"],
] as const)("%s 的单局与多路都区分终态和历史，仍可回到%s", async (status, label) => {
    setup(1, first, first, status);
    await screen.findByText(label, { exact: true });
    for (const focus of [false, true]) {
        if (focus) await userEvent.click(screen.getByRole("button", { name: "专注查看" }));
        expect(screen.getByText(label, { exact: true })).toBeVisible();
        expect(screen.queryByRole("button", { name: "暂停跟随" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "停止运行" })).not.toBeInTheDocument();
        if (focus) {
            expect(document.querySelector(".spectator-runtime")).not.toHaveTextContent(/工具预算|等待模型响应/);
            expect(screen.queryByText("运行已结束，可按步骤回看")).not.toBeInTheDocument();
        }
        await userEvent.click(screen.getByRole("button", { name: "上一步" }));
        expect(screen.getByText("历史局面", { exact: true })).toBeVisible();
        await userEvent.click(screen.getByRole("button", { name: `返回${label}` }));
        expect(screen.getByText(label, { exact: true })).toBeVisible();
        expect(screen.queryByText("实时局面", { exact: true })).not.toBeInTheDocument();
    }
});

it("暂停的运行结束时保留历史位置，只把返回入口改为终态回看", async () => {
    const { setStatus } = setup();
    await screen.findByText("实时局面", { exact: true });
    await userEvent.click(screen.getByRole("button", { name: "暂停跟随" }));
    setStatus("completed");
    expect(screen.getByText("历史局面", { exact: true })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "返回最终局面" }));
    expect(screen.getByText("最终局面", { exact: true })).toBeVisible();
    expect(screen.queryByRole("button", { name: "暂停跟随" })).not.toBeInTheDocument();
});

it("编队贡献来自所选位置之前的结算，回看旧回合不使用未来或已回滚的贡献", async () => {
    const attempt = first.attempts[0];
    const attempts = [
        { ...attempt, id: 10, turn: 1, score: 100, rank_contributions: { turn: 1, scores: { a1: 100 } } },
        { ...attempt, id: 15, turn: 1, score: 999, status: "rolled_back", rank_contributions: { turn: 1, scores: { a1: 999 } } },
        { ...attempt, id: 20, turn: 2, score: 200, rank_contributions: { turn: 2, scores: { a1: 200 } } },
        { ...attempt, id: 30, turn: 3, score: 300, rank_contributions: { turn: 3, scores: { a1: 300 } } },
    ];
    const page = (turn: number, id: number): WatchPage => ({ cursor: id + 2, selected: id, attempts, records: [
        record(id, { type: "turn_started", turn, observation: { ...recruited, turn } }),
        record(id + 1, { type: "tool_result", name: "end_turn", result: { ok: true, _observation_after: { ...recruited, turn: turn + 1 } } }),
        record(id + 2, { type: "turn_scored", turn, rank_score: turn * 100, adventurer_rank_scores: { a1: turn * 100 } }),
    ] });
    setup(1, page(3, 30), page(2, 20));
    fireEvent.click(await screen.findByRole("button", { name: "专注查看" }));
    fireEvent.click(await screen.findByRole("button", { name: "展开编队" }));
    const detail = () => screen.getByRole("region", { name: "验收剑士 的详情" });
    expect(within(detail()).getByText("300", { exact: true })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "上一步" }));
    fireEvent.click(screen.getByRole("button", { name: "上一步" }));
    await waitFor(() => expect(within(detail()).getByText("200", { exact: true })).toBeVisible());
    fireEvent.change(screen.getByLabelText("选择观战回合"), { target: { value: "20" } });
    await waitFor(() => expect(screen.getByText("回合 2")).toBeVisible());
    fireEvent.click(screen.getByRole("button", { name: "上一步" }));
    fireEvent.click(screen.getByRole("button", { name: "上一步" }));
    await waitFor(() => expect(within(detail()).getByText("100", { exact: true })).toBeVisible());
    expect(within(detail()).queryByText("200", { exact: true })).not.toBeInTheDocument();
    expect(within(detail()).queryByText("999", { exact: true })).not.toBeInTheDocument();
});

it("兑换额度跟随所选历史位置，不使用当前任务或未来状态", async () => {
    const before: Observation = { ...initial, turn: 12, max_turns: 35,
        material_exchange: { from_turn: 12, point_limit: 6, used_points: 0, remaining_points: 6 } };
    const after: Observation = { ...before, gold: 20, materials: { spider_silk: 2 },
        material_exchange: { ...before.material_exchange!, used_points: 4, remaining_points: 2 } };
    const page: WatchPage = { ...first, cursor: 2, records: [
        record(1, { type: "turn_started", turn: 12, observation: before }),
        record(2, { type: "tool_result", turn: 12, name: "exchange_materials",
            arguments: { material_id: "spider_silk", quantity: 2 }, result: { ok: true, _observation_after: after } }),
    ] };
    setup(1, page, page);
    expect(await screen.findByLabelText("材料兑换状态")).toHaveTextContent("剩余额度2/6点");
    expect(stateChanges(before, after)).toContain("兑换剩余额度 -4");
    fireEvent.change(screen.getByRole("slider", { name: "选择观战步骤" }), { target: { value: "0" } });
    expect(screen.getByLabelText("材料兑换状态")).toHaveTextContent("剩余额度6/6点");
    fireEvent.click(screen.getByRole("button", { name: /返回实时/ }));
    expect(screen.getByLabelText("材料兑换状态")).toHaveTextContent("剩余额度2/6点");
    fireEvent.click(screen.getByRole("button", { name: "专注查看" }));
    expect(await screen.findByLabelText("材料兑换状态")).toHaveTextContent("剩余额度2/6点");
    expect(within(screen.getByRole("region", { name: "最近行动概览" })).getByText("兑换材料", { exact: true })).toBeVisible();
});

it.each([
    [11, 0, "未开放（T12起）"],
    [35, 6, "额度已用尽（0/6点）"],
] as const)("兑换状态在 T%s 按存档规则展示", async (turn, used, label) => {
    const observation = { ...initial, turn, material_exchange: { from_turn: 12, point_limit: 6, used_points: used, remaining_points: 6-used } };
    const page = { ...first, cursor: 1, records: [record(1, { type: "turn_started", turn, observation })] };
    setup(1, page, page);
    expect(await screen.findByLabelText("材料兑换状态")).toHaveTextContent(label);
});

it("旧版观战没有兑换信息时不虚构额度", async () => {
    setup();
    await screen.findByText("验收剑士");
    expect(screen.queryByLabelText("材料兑换状态")).not.toBeInTheDocument();
});

it("多路面板明确显示专注入口，点击后进入完整局面", async () => {
    const { onFocus } = setup();
    const focus = await screen.findByRole("button", { name: "专注查看" });
    expect(focus).toHaveTextContent("专注");
    fireEvent.click(focus);
    expect(onFocus).toHaveBeenCalledWith(undefined);
    expect(await screen.findByRole("region", { name: "单局总览" })).toBeVisible();
});

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

it("暂停后流式正文、思考和工具参数不提示新事件，实际工具行动仍提示且不改变历史局面", async () => {
    let page: WatchPage = { ...first, attempts: [{ ...first.attempts[0], status: "running", score: null }], records: [
        ...first.records.slice(0, 4), record(5, { type: "model_request", turn: 1, step: 2 }),
    ] };
    const { update } = setup(1, page);
    await screen.findByText("验收剑士");
    fireEvent.click(screen.getByRole("button", { name: "暂停跟随" }));
    for (const type of ["model_reasoning_delta", "model_delta", "tool_call_delta"]) {
        const cursor = page.cursor + 1;
        page = { ...page, cursor, records: type === "tool_call_delta" ? page.records
            : [...page.records, record(cursor, { type, text: "流式输出尚未结束" })] };
        await update(page);
        expect(screen.getByRole("button", { name: "返回实时" })).toBeVisible();
        expect(screen.getByText("历史局面", { exact: true })).toBeVisible();
    }
    const cursor = page.cursor;
    await update({ ...page, cursor: cursor + 3, records: [...page.records,
        record(cursor + 1, { type: "model_response", turn: 1, step: 2, tool_calls: [{ id: "buy", name: "purchase_upgrade", arguments: { upgrade_id: "training" } }] }),
        record(cursor + 2, { type: "tool_call", name: "purchase_upgrade", call_id: "buy", arguments: { upgrade_id: "training" } }),
        record(cursor + 3, { type: "tool_result", name: "purchase_upgrade", call_id: "buy", result: { ok: true, _observation_after: { ...recruited, gold: 25 } } }),
    ] });
    expect(await screen.findByRole("button", { name: "返回实时（有新事件）" })).toBeVisible();
    expect(within(screen.getByLabelText("当前观看位置的资源")).getByText("60", { exact: true })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "返回实时（有新事件）" }));
    expect(within(screen.getByLabelText("当前观看位置的资源")).getByText("25", { exact: true })).toBeVisible();
});

it("跟随时流式思考只更新模型输出，不把行动时间线拉到底，实际工具行动仍自动跟随", async () => {
    const pending: WatchPage = { ...first, attempts: [{ ...first.attempts[0], status: "running", score: null }], records: [
        ...first.records.slice(0, 4), record(5, { type: "model_request", turn: 1, step: 2 }),
    ] };
    const { update } = setup(1, pending);
    fireEvent.click(await screen.findByRole("button", { name: "专注查看" }));
    fireEvent.click(await screen.findByRole("button", { name: "展开最近行动" }));
    const timeline = document.querySelector<HTMLDivElement>(".spectator-timeline-scroll")!;
    Object.defineProperty(timeline, "scrollHeight", { configurable: true, value: 900 });
    timeline.scrollTop = 100;
    fireEvent.scroll(timeline);
    const streaming: WatchPage = { ...pending, cursor: 7, records: [
        ...pending.records, record(6, { type: "model_reasoning_delta", text: "仍在权衡下一步操作" }),
        record(7, { type: "model_delta", text: "仍在生成的正文" }),
    ] };
    await update(streaming);
    await screen.findByText("仍在生成的正文", { exact: true });
    expect(timeline.scrollTop).toBe(100);
    expect(screen.getByRole("button", { name: "暂停跟随" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("切换展开模块"), { target: { value: "models" } });
    fireEvent.click(screen.getByText("API 提供的推理文本或摘要"));
    expect(screen.getByText("仍在权衡下一步操作", { exact: true })).toBeVisible();
    expect(screen.getByRole("option", { name: "调用 2 / 尚无完整响应" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("切换展开模块"), { target: { value: "actions" } });
    timeline.scrollTop = 100;
    fireEvent.scroll(timeline);
    await update({ ...streaming, cursor: 10, records: [...streaming.records,
        record(8, { type: "model_response", turn: 1, step: 2, text: "仍在生成的正文", assistant_metadata: { reasoning_content: "仍在权衡下一步操作" }, tool_calls: [{ id: "memo", name: "write_memo", arguments: { content: "完成思考" } }] }),
        record(9, { type: "tool_call", name: "write_memo", call_id: "memo", arguments: { content: "完成思考" } }),
        record(10, { type: "tool_result", name: "write_memo", call_id: "memo", result: { ok: true, memo: { content: "完成思考" } } }),
    ] });
    expect(await within(screen.getByRole("region", { name: "观战行动时间线" })).findByRole("button", { name: "定位行动 记录备忘" })).toBeVisible();
    expect(timeline.scrollTop).toBe(900);
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

it("购买对象不会被资源差值盖掉，时间线合并调用结果且逐条展示查询，点击仍定位真实局面", async () => {
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
    const queries = within(timeline).getAllByRole("button", { name: "定位行动 查看队伍" });
    expect(queries).toHaveLength(2);
    for (const query of queries) {
        expect(query).toBeVisible();
        expect(query.closest("details")).toBeNull();
    }
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

async function openReasoningFromRecentTool(reasoning: string | undefined, multiple = false) {
    const tools = multiple ? [{ name: "recruit_adventurer", call_id: "hire" }, { name: "get_party", call_id: "party" }] : [{ name: "recruit_adventurer", call_id: "hire" }];
    const scenario: WatchPage = { ...first, cursor: 7, records: [
        first.records[0], first.records[1],
        record(3, { ...first.records[2].event, assistant_metadata: reasoning === undefined ? {} : { reasoning_content: reasoning }, tool_calls: tools }),
        first.records[3], first.records[4],
        record(6, { type: "model_request", turn: 1, step: 2 }),
        record(7, { type: "model_response", turn: 1, step: 2, text: "不相关的新正文", assistant_metadata: { reasoning_content: "不相关的新调用推理" } }),
    ] };
    const controls = setup();
    await screen.findByText("验收剑士");
    await controls.update(scenario);
    fireEvent.click(screen.getByRole("button", { name: "切换专注" }));
    // 先手动查看其它模型调用，再从较早的工具跳转，不能沿用旧选择。
    fireEvent.click(await screen.findByRole("button", { name: "展开模型输出" }));
    expect(screen.getByLabelText("选择模型调用")).toHaveValue("6");
    fireEvent.click(screen.getByRole("button", { name: "返回总览" }));
    fireEvent.click(screen.getByRole("button", { name: "展开最近行动" }));
    fireEvent.click(screen.getByRole("button", { name: "查看对应模型推理" }));
    await screen.findByRole("heading", { name: "模型输出模块标题" });
    return controls;
}

it("从较早工具直接定位所属模型推理，不跳到最新调用，返回后仍是该操作", async () => {
    const { requests, onStop } = await openReasoningFromRecentTool("这段是已记录的招募调用 API 推理", true);
    expect(screen.getByLabelText("选择模型调用")).toHaveValue("2");
    const reasoning = screen.getByText("这段是已记录的招募调用 API 推理", { exact: true });
    expect(reasoning).toBeVisible();
    expect(reasoning.closest("details")).toHaveAttribute("open");
    const body = within(screen.getByRole("region", { name: "所选位置的模型调用" })).getByText("先招募一位剑士。", { exact: true });
    expect(reasoning.compareDocumentPosition(body) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(screen.getByText(/这 2 个工具调用共用下面这段模型思考/)).toBeVisible();
    expect(screen.queryByText("不相关的新调用推理", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByText("历史局面", { exact: true })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "返回该工具" }));
    expect(screen.getByRole("region", { name: "所选行动详情" })).toHaveTextContent("招募 验收剑士");
    fireEvent.click(screen.getByRole("button", { name: "查看对应模型推理" }));
    expect(screen.getByText("这段是已记录的招募调用 API 推理").closest("details")).toHaveAttribute("open");
    expect(onStop).not.toHaveBeenCalled();
    expect(requests.every(path => path.includes("/watch"))).toBe(true);
});

it("对应调用未保存 API 推理时明确说明，不拿正文或后一次推理替代", async () => {
    await openReasoningFromRecentTool(undefined);
    expect(screen.getByText(/这次调用没有保存思考过程/)).toBeVisible();
    expect(screen.getByText(/这次调用没有保存思考过程/).closest("details")).toHaveAttribute("open");
    expect(within(screen.getByRole("region", { name: "所选位置的模型调用" })).getByText("先招募一位剑士。", { exact: true })).toBeVisible();
    expect(screen.queryByText("不相关的新调用推理", { exact: true })).not.toBeInTheDocument();
});

it("从工具定位后仍可手动选择其它模型调用，不被来源链接锁住", async () => {
    setup(1, { ...first, cursor: 8, records: [
        first.records[0], first.records[1],
        record(3, { ...first.records[2].event, assistant_metadata: { reasoning_content: "第一次调用的已记录推理" } }),
        first.records[3], first.records[4],
        record(6, { type: "model_request", step: 2 }),
        record(7, { type: "model_response", step: 2, text: "第二次调用正文", assistant_metadata: { reasoning_content: "第二次调用的已记录推理" } }),
        record(8, { type: "tool_result", name: "write_memo", result: { ok: true, memo: { content: "构造备忘" } } }),
    ] });
    await screen.findByText("验收剑士");
    fireEvent.click(screen.getByRole("button", { name: "切换专注" }));
    fireEvent.click(await screen.findByRole("button", { name: "展开最近行动" }));
    fireEvent.click(screen.getByRole("button", { name: "查看对应模型推理" }));
    expect(screen.getByText("第二次调用的已记录推理", { exact: true })).toBeVisible();
    fireEvent.change(screen.getByLabelText("选择模型调用"), { target: { value: "2" } });
    expect(screen.getByLabelText("选择模型调用")).toHaveValue("2");
    expect(screen.queryByRole("button", { name: "返回该工具" })).not.toBeInTheDocument();
    const model = within(screen.getByRole("region", { name: "所选位置的模型调用" }));
    const body = model.getByText("先招募一位剑士。", { exact: true });
    const reasoning = model.getByText("第一次调用的已记录推理", { exact: true });
    expect(body).toBeVisible();
    expect(reasoning.closest("details")).not.toHaveAttribute("open");
    expect(body.compareDocumentPosition(reasoning) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
});

it("缺少模型关联的旧工具记录不能跳到附近的其它响应", async () => {
    setup(1, { ...first, cursor: 7, records: [first.records[0],
        record(6, { type: "model_response", step: 1, text: "未关联的正文", assistant_metadata: { reasoning_content: "未关联的推理" } }),
        record(7, first.records[3].event),
    ] });
    await screen.findByText("验收剑士");
    fireEvent.click(screen.getByRole("button", { name: "切换专注" }));
    fireEvent.click(await screen.findByRole("button", { name: "展开最近行动" }));
    expect(screen.getByRole("button", { name: "未记录模型关联" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "查看对应模型推理" })).not.toBeInTheDocument();
});

it("正在执行的工具也可以定位已返回的模型推理，不提前显示执行结果", async () => {
    setup(1, { ...first, cursor: 6, records: [first.records[0], first.records[1],
        record(3, { ...first.records[2].event, assistant_metadata: { reasoning_content: "待执行工具的构造 API 推理" } }),
        record(6, { type: "tool_call", name: "recruit_adventurer", call_id: "pending", arguments: { candidate_id: 1 } }),
    ] });
    await screen.findByText("此时尚未招募冒险者");
    fireEvent.click(screen.getByRole("button", { name: "切换专注" }));
    fireEvent.click(await screen.findByRole("button", { name: "展开最近行动" }));
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("尚未记录执行结果");
    fireEvent.click(screen.getByRole("button", { name: "查看对应模型推理" }));
    expect(screen.getByText("待执行工具的构造 API 推理", { exact: true })).toBeVisible();
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
