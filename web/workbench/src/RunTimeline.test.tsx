import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Theme } from "@radix-ui/themes";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { RunTimeline } from "./RunTimeline";
import { TrajectoryProvider } from "./TrajectoryContext";
import { WatchRun } from "./WatchRun";
import { testJob } from "./test-jobs";
import type { TrajectoryData, TrajectoryRow } from "./trajectoryModel";
import type { Observation } from "./types";
import type { WatchPage, WatchPosition } from "./watchModel";

function row(ordinal: number, name: string, result: Record<string, any>, patch: Partial<TrajectoryRow> = {}): TrajectoryRow {
    return { id: `100:${ordinal}`, attempt: 100, turn: 1, ordinal, cursor: 100 + ordinal * 10, at: "2026-10-25T10:00:00Z", lifecycle: "confirmed", names: {}, subjects: [],
        event: { type: "tool_result", name, result: { ok: true, ...result } }, ...patch };
}
const buy = row(3, "purchase_upgrade", { event: { summary: "购买全局加成 训练场", changes: [
    { kind: "resource", label: "金币", before: 100, after: 80 },
    { kind: "adventurer", label: "战士 攻击", before: 10, after: 12 },
] }, _action_output: { global_upgrades: [{ upgrade_id: "training", name: "训练场", description: "全体成员攻击提高2点", stats: { attack: 2 } }] } });
const payload: TrajectoryData = { cursor: 500, origin: "archive", warning: null, entries: [
    row(1, "get_party", { _collection_counts: { adventurers: 1 } }), row(2, "get_party", { _collection_counts: { adventurers: 1 } }), buy,
    row(4, "equip_item", { event: { summary: "战士 装备 铁剑" } }),
    row(5, "end_turn", { event: { summary: "结束第1回合", changes: [{ kind: "resource", label: "金币", before: 80, after: 120 }] },
        turn_result: { battles: [{ adventurer_name: "战士", monster_name: "史莱姆", won: true, reward: { gold: 40, experience: 12, materials: { iron_ore: 1 } } }] } }),
    row(1, "purchase_upgrade", { event: { summary: "回滚尝试购买铁甲" } }, { id: "200:1", attempt: 200, turn: 2, lifecycle: "rolled_back", cursor: 210 }),
    row(1, "get_crafting", { ok: false, error: "配方查询失败" }, { id: "300:1", attempt: 300, turn: 2, lifecycle: "uncommitted", cursor: 310 }),
], attempts: [
    { id: 100, turn: 1, attempt: 1, status: "completed", score: 0, actions: 5, at: "", updated_at: "" },
    { id: 200, turn: 2, attempt: 1, status: "rolled_back", score: null, actions: 1, at: "", updated_at: "" },
    { id: 300, turn: 2, attempt: 2, status: "running", score: null, actions: 1, at: "", updated_at: "" },
    { id: 400, turn: 3, attempt: 1, status: "interrupted", score: null, actions: 0, at: "", updated_at: "" },
] };
function setup(data = payload, status = 200) {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data), { status }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const onInspect = vi.fn(), onLocate = vi.fn();
    render(<QueryClientProvider client={client}><Theme><TrajectoryProvider job={testJob("timeline", { status: "completed" })} enabled viewTurn={1} onLocate={onLocate}>
        <RunTimeline onInspect={onInspect} />
    </TrajectoryProvider></Theme></QueryClientProvider>);
    return { fetch, onInspect, onLocate };
}

it("默认每回合六个对齐字段，不混排缩写与数字，查询次数和其它操作留在提示中", async () => {
    const { fetch, onLocate } = setup();
    const first = await screen.findByRole("button", { name: "查看第 1 回合概览" });
    expect(first).toHaveTextContent("训练场");
    expect(first.children).toHaveLength(6);
    expect(first.querySelector(".run-process-battle")).toHaveTextContent("1/0");
    expect(first.querySelector(".run-process-gold")).toHaveTextContent("40");
    expect(first.querySelector(".run-process-experience")).toHaveTextContent("12");
    expect(first.querySelector(".run-process-rank")).toHaveTextContent("未知");
    expect(first).toHaveAttribute("title", expect.stringContaining("装备×1"));
    expect(first).toHaveAttribute("title", expect.stringContaining("辅助调用2次"));
    expect(first).not.toHaveTextContent("查2");
    expect(screen.getAllByText("经验获得", { exact: true }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /查看第 .* 回合概览/ })).toHaveLength(3);
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.queryByText("全体成员攻击提高2点")).not.toBeInTheDocument();
    expect(screen.queryByText("战士 → 史莱姆")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("所选回合操作")).not.toBeInTheDocument();
    expect(onLocate).not.toHaveBeenCalled();
    expect(fetch.mock.calls.every(([path]) => String(path).endsWith("/trajectories"))).toBe(true);
});

it("只有点选回合才展示详情，其它回合仍留在总览，原始定位按需执行", async () => {
    const { onInspect, onLocate } = setup();
    const first = await screen.findByRole("button", { name: "查看第 1 回合概览" });
    fireEvent.click(first);
    const details = screen.getByRole("region", { name: "所选回合操作" });
    expect(within(details).getByText("Rank Score 0", { exact: true })).toBeVisible();
    expect(within(details).getByText("全体成员攻击提高2点")).toBeVisible();
    expect(within(details).getByText("金币 100 → 80（-20）")).toBeVisible();
    expect(within(details).getByText("战士 → 史莱姆")).toBeVisible();
    expect(within(details).getByText("奖励材料 · 铁矿石 +1")).toBeVisible();
    expect(screen.getAllByRole("button", { name: /查看第 .* 回合概览/ })).toHaveLength(3);
    expect(onLocate).not.toHaveBeenCalled();
    fireEvent.click(within(details).getByRole("button", { name: "查看详情 购买全局加成 训练场" }));
    expect(onInspect).toHaveBeenCalledWith(buy);
    fireEvent.keyDown(details, { key: "Escape" });
    expect(screen.queryByLabelText("所选回合操作")).not.toBeInTheDocument();
    await waitFor(() => expect(first).toHaveFocus());
});

it.each<[number, number, string, string]>([
    [20, 30, "30", "+10"], [10, 20, "20", "0"], [5, 15, "15", "-5"], [5, 0, "0", "-5"],
])("经验展示获得量，不减培养消耗；战力仍展示正式差值，当前Rank为%s，奖励经验为%s", async (score, reward, experienceText, rankText) => {
    const first = row(1, "end_turn", { event: { changes: [{ kind: "resource", label: "经验池", before: 0, after: 20 }] }, turn_result: { battles: [{ won: true, reward: { gold: 1, experience: 20, materials: {} } }] } });
    const prepare = row(1, "allocate_experience", { event: { summary: "分配20经验", changes: [{ kind: "resource", label: "经验池", before: 20, after: 0 }] } }, { id: "200:1", attempt: 200, turn: 2 });
    const ended = row(2, "end_turn", { event: { changes: [{ kind: "resource", label: "经验池", before: 0, after: reward }] }, turn_result: { battles: [{ won: true, reward: { gold: 2, experience: reward, materials: {} } }] } }, { id: "200:2", attempt: 200, turn: 2 });
    setup({ ...payload, entries: [first, prepare, ended], attempts: [
        { id: 100, turn: 1, attempt: 1, status: "completed", score: 10, actions: 1, at: "", updated_at: "" },
        { id: 200, turn: 2, attempt: 1, status: "completed", score, actions: 2, at: "", updated_at: "" },
    ] });
    const second = await screen.findByRole("button", { name: "查看第 2 回合概览" });
    expect(second.querySelector(".run-process-experience")).toHaveTextContent(experienceText);
    expect(second.querySelector(".run-process-rank")).toHaveTextContent(rankText);
    expect(second.querySelector(".run-process-experience")).not.toHaveClass("is-negative");
    expect(within(second).getByTitle(/本回合讨伐获得经验/)).toHaveAttribute("title", expect.stringContaining(`获得经验 ${reward}`));
    expect(screen.getByRole("button", { name: "查看第 1 回合概览" }).querySelector(".run-process-rank")).toHaveTextContent("未知");
    fireEvent.click(second);
    const growth = screen.getByRole("region", { name: "所选回合操作" }).querySelector(".run-process-detail-growth");
    expect(growth).toHaveTextContent(`获得经验 ${reward}`);
    expect(growth).toHaveTextContent(`正式战力Δ ${rankText}`);
    expect(growth).not.toHaveTextContent("经验池Δ");
    expect(screen.getAllByRole("button", { name: /查看第 .* 回合概览/ })).toHaveLength(2);
});

it("35回合的数百次查询只生成35条总览行，不默认挂载全部操作详情", async () => {
    const entries: TrajectoryRow[] = [];
    for (let turn = 1; turn <= 35; turn++) {
        for (let query = 1; query <= 10; query++) entries.push(row(query, "get_party", {}, { id: `${turn}:${query}`, attempt: turn, turn }));
        entries.push(row(11, "end_turn", { turn_result: { battles: [] } }, { id: `${turn}:11`, attempt: turn, turn }));
    }
    setup({ ...payload, entries, attempts: undefined });
    await screen.findByRole("button", { name: "查看第 35 回合概览" });
    expect(screen.getAllByRole("button", { name: /查看第 .* 回合概览/ })).toHaveLength(35);
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(document.querySelectorAll(".timeline-auxiliary")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "查看第 35 回合概览" })).toHaveAttribute("title", expect.stringContaining("辅助调用10次"));
});

it("回滚尝试不增加主表行或冒充有效升级，失败和无工具记录仍可见", async () => {
    setup();
    const second = await screen.findByRole("button", { name: "查看第 2 回合概览" });
    expect(within(second).getByTitle("1次失败调用")).toHaveTextContent("!");
    expect(second).not.toHaveTextContent("铁甲");
    expect(second).toHaveTextContent("↻2");
    expect(screen.getByRole("button", { name: "查看第 3 回合概览" })).toHaveTextContent("无工具记录");
    fireEvent.click(second);
    const details = screen.getByRole("region", { name: "所选回合操作" });
    expect(within(details).getByText("配方查询失败")).toBeVisible();
    fireEvent.change(within(details).getByLabelText("选择回合尝试"), { target: { value: "200" } });
    expect(within(details).getByText("此尝试已回滚，不再生效。")).toBeVisible();
    expect(within(details).getByText("回滚尝试购买铁甲", { exact: true })).toBeVisible();
    expect(second).not.toHaveTextContent("铁甲");
});

it("批量方案中的全局升级进入总览，重复提交不重复计数或展示收益", async () => {
    const batch = row(1, "execute_plan", { already_executed: false, events: [
        { type: "preparation_applied", action: { type: "craft", recipe_id: "sword" }, summary: "方案制作铁剑" },
        { type: "preparation_applied", action: { type: "purchase_upgrade", upgrade_id: "training" }, summary: "方案解锁训练场" },
    ] });
    setup({ ...payload, attempts: payload.attempts!.slice(0, 1), entries: [batch,
        { ...batch, id: "100:2", ordinal: 2, event: { ...batch.event, result: { ...batch.event.result, already_executed: true } } }, row(3, "end_turn", {}),
    ] });
    const first = await screen.findByRole("button", { name: "查看第 1 回合概览" });
    expect(first).toHaveTextContent("方案解锁训练场");
    expect(first).toHaveAttribute("title", expect.stringContaining("制作×1"));
    expect(first).toHaveAttribute("title", expect.stringContaining("辅助调用1次"));
    expect(first.querySelector(".run-process-battle")).toHaveTextContent("未知");
    expect(first.querySelector(".run-process-battle")).toHaveAttribute("title", "已结束 / 战果缺失");
    fireEvent.click(first);
    const upgrade = screen.getByRole("article", { name: "方案解锁训练场" });
    expect(within(upgrade).getByText("方案第 2 / 2 步 · 共用一次原子提交")).toBeVisible();
    expect(within(upgrade).getByRole("button", { name: "查看详情 方案解锁训练场" })).toHaveTextContent("查看所属方案");
    expect(screen.getAllByText("方案制作铁剑", { exact: true })).toHaveLength(1);
    expect(screen.getByText("战果与奖励未记录，不按零场或零收益处理。")).toBeVisible();
});

it("加载失败不输出假零回合，旧服务覆盖提示收在按需说明中", async () => {
    const view = setup({ ...payload, origin: "execution", warning: "缺少归档，只覆盖续跑后的阶段" }, 503);
    expect(screen.queryByText(/0 回合/)).not.toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("读取失败");
    expect(view.onLocate).not.toHaveBeenCalled();
});

it("旧服务的有限覆盖始终可见，详细提示不占用总览高度，缺失评分不推算", async () => {
    setup({ ...payload, attempts: undefined, origin: "execution", warning: "仅当前阶段" });
    expect(await screen.findByText(/仅当前执行阶段/)).toBeVisible();
    const notes = screen.getByText("记录提示").closest("details")!;
    expect(notes).not.toHaveAttribute("open");
    expect(screen.getByText(/服务尚未提供回合边界/)).not.toBeVisible();
    fireEvent.click(notes.querySelector("summary")!);
    expect(screen.getByText(/服务尚未提供回合边界/)).toBeVisible();
    expect(screen.queryByText(/Rank Score/)).not.toBeInTheDocument();
});

it("侧栏直达总览，选择回合不定位局面，查看原始操作后可返回紧凑总览", async () => {
    const before: Observation = { gold: 100, experience_pool: 0, materials: {}, adventurers: [], party_size_limit: 3, turn: 1, max_turns: 8 };
    const archived: WatchPage = { cursor: 500, selected: 100, origin: "archive", attempts: payload.attempts!, records: [
        { cursor: 100, after: 0, at: "2026-10-25", event: { type: "turn_started", turn: 1, observation: before } },
        { cursor: buy.cursor, after: 100, at: "2026-10-25", event: { ...buy.event, result: { ...buy.event.result, _observation_after: { ...before, gold: 80 } } } },
    ] };
    const live: WatchPage = { ...archived, origin: undefined, selected: 400, records: [
        { cursor: 400, after: 0, at: "2026-10-25", event: { type: "turn_started", turn: 3, observation: { ...before, turn: 3, gold: 200 } } },
    ] };
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
        const path = String(input); requests.push(path);
        return new Response(JSON.stringify(path.includes("trajectories?attempt=") ? archived : path.endsWith("/trajectories") ? payload : live));
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    function Harness() {
        const [position, setPosition] = useState<WatchPosition>({ attempt: null, cursor: null });
        return <QueryClientProvider client={client}><Theme><WatchRun runId="timeline-direct" job={testJob("timeline-direct", { status: "completed", cursor: 400 })}
            position={position} onPosition={setPosition} initialFocus={{ module: "actions", actionView: "timeline" }} onStop={vi.fn()} onClone={vi.fn()} /></Theme></QueryClientProvider>;
    }
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "查看第 1 回合概览" }));
    expect(requests.some(path => path.includes("trajectories?attempt="))).toBe(false);
    expect(within(screen.getByLabelText("当前观看位置的资源")).getByText("200", { exact: true })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "查看详情 购买全局加成 训练场" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "所选回合" })).toHaveAttribute("aria-pressed", "true"));
    expect(within(screen.getByLabelText("当前观看位置的资源")).getByText("80", { exact: true })).toBeVisible();
    expect(requests.some(path => path.includes("trajectories?attempt=100"))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "整局时间线" }));
    expect(screen.getAllByRole("button", { name: /查看第 .* 回合概览/ })).toHaveLength(3);
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
});
