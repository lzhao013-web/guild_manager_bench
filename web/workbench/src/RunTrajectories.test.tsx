import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Theme } from "@radix-ui/themes";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ObjectTrajectories, RunTrajectories } from "./RunTrajectories";
import { TrajectoryProvider } from "./TrajectoryContext";
import { confirmed, objectActionTitle, subjectRows } from "./trajectoryModel";
import type { TrajectoryData, TrajectoryRow } from "./trajectoryModel";
import { WatchRun } from "./WatchRun";
import type { Observation } from "./types";
import { testJob } from "./test-jobs";
import type { WatchPage, WatchPosition } from "./watchModel";

const row = (patch: Partial<TrajectoryRow> = {}): TrajectoryRow => ({
    id: "200:1", attempt: 200, turn: 2, ordinal: 1, cursor: 250, at: "2026-10-19T12:00:00Z",
    lifecycle: "confirmed", subjects: [{ kind: "upgrade", id: "training", name: "训练场" }], names: {},
    event: { type: "tool_result", name: "purchase_upgrade", result: { ok: true, event: { summary: "购买训练场", changes: [{ kind: "resource", label: "金币", before: 100, after: 80 }] } } }, ...patch,
});
const rows = [row(), row({ id: "300:1", attempt: 300, turn: 3, lifecycle: "rolled_back", subjects: [{ kind: "recipe", id: "sword", name: "铁剑" }, { kind: "equipment", id: "eq-1", name: "铁剑" }], event: { type: "tool_result", name: "craft_equipment", result: { ok: true, event: { summary: "制作铁剑" } } } }),
    row({ id: "400:1", attempt: 400, turn: 3, subjects: [{ kind: "recipe", id: "sword", name: "铁剑" }, { kind: "equipment", id: "eq-2", name: "铁剑" }], event: { type: "tool_result", name: "craft_equipment", result: { ok: true, event: { summary: "制作铁剑" } } } })];
const payload: TrajectoryData = { cursor: 900, origin: "archive", warning: null, entries: rows };
function setup(children: React.ReactNode, patch = payload, status = 200) {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(patch), { status }));
    const onLocate = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><Theme><TrajectoryProvider job={testJob("one", { status: "completed" })} enabled viewTurn={2} onLocate={onLocate}>{children}</TrajectoryProvider></Theme></QueryClientProvider>);
    return { fetch, onLocate };
}
it("概览始终统计整局，类别与状态入口清除旧条件后定位对应记录", async () => {
    const { onLocate } = setup(<RunTrajectories />);
    const overview = screen.getByRole("region", { name: "整局经营操作概览" });
    const craft = within(overview).getByRole("button", { name: "查看装备制作记录" });
    await waitFor(() => expect(craft).toBeEnabled());
    expect(within(craft).getByText("2", { exact: true })).toBeVisible();
    expect(within(craft).getByText("已确认 1", { exact: true })).toBeVisible();
    fireEvent.change(screen.getByLabelText("搜索整局行动"), { target: { value: "训练场" } });
    fireEvent.change(screen.getByLabelText("轨迹结束回合"), { target: { value: "2" } });
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(1);
    expect(within(craft).getByText("2", { exact: true })).toBeVisible();
    fireEvent.click(craft);
    expect(screen.getByLabelText("搜索整局行动")).toHaveValue("");
    expect(screen.getByLabelText("轨迹结束回合")).toHaveValue(null);
    expect(screen.getByLabelText("筛选轨迹操作")).toHaveValue("category:craft");
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(2);
    fireEvent.click(within(overview).getByRole("button", { name: "查看已确认成功记录" }));
    expect(screen.getByLabelText("筛选轨迹操作")).toHaveValue("all");
    expect(screen.getByLabelText("筛选轨迹状态")).toHaveValue("confirmed");
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(2);
    fireEvent.click(within(overview).getByRole("button", { name: /共/ }));
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(3);
    expect(onLocate).not.toHaveBeenCalled();
});
it("概览加载期间显示未知而非假零，失败时不提供可点击的零计数", async () => {
    setup(<RunTrajectories />, payload, 503);
    const overview = screen.getByRole("region", { name: "整局经营操作概览" });
    expect(overview).toHaveAttribute("aria-busy", "true");
    expect(within(overview).queryByText("0", { exact: true })).not.toBeInTheDocument();
    await screen.findByRole("alert");
    expect(within(overview).getByText("轨迹读取失败", { exact: true })).toBeVisible();
    expect(within(overview).queryByText("0", { exact: true })).not.toBeInTheDocument();
    expect(within(overview).getByRole("button", { name: "查看装备制作记录" })).toBeDisabled();
});
it("覆盖不完整的统计明确属于当前阶段，不宣称整局零操作", async () => {
    setup(<RunTrajectories />, { ...payload, origin: "execution", entries: [], warning: "仅覆盖当前运行阶段" });
    const overview = screen.getByRole("region", { name: "整局经营操作概览" });
    await waitFor(() => expect(within(overview).getByRole("button", { name: "查看装备制作记录" })).toBeEnabled());
    expect(within(overview).getByText("仅当前运行阶段", { exact: true })).toBeVisible();
    expect(screen.getByText("仅覆盖当前运行阶段", { exact: true })).toBeVisible();
});
it("整局检索按名称、操作、状态和回合过滤，不把回滚制作算作有效成功", async () => {
    setup(<RunTrajectories />);
    await screen.findByRole("button", { name: "查看轨迹 回合2 购买训练场" });
    fireEvent.change(screen.getByLabelText("搜索整局行动"), { target: { value: "铁剑" } });
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(2);
    fireEvent.change(screen.getByLabelText("筛选轨迹状态"), { target: { value: "confirmed" } });
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(1);
    expect(screen.getByText("实例 eq-2")).toBeVisible();
    fireEvent.change(screen.getByLabelText("轨迹结束回合"), { target: { value: "2" } });
    expect(screen.queryByRole("button", { name: /查看轨迹/ })).not.toBeInTheDocument();
});
it("整局检索分页限制 DOM 数量，改变筛选后回到第一页顶部", async () => {
    const many = Array.from({ length: 41 }, (_, index) => {
        const turn = Math.floor(index / 2) + 1, ordinal = index % 2 + 1;
        return row({ id: `${turn * 100}:${ordinal}`, turn, attempt: turn * 100, ordinal, cursor: index * 100 + 1 });
    });
    setup(<RunTrajectories />, { ...payload, entries: many });
    await screen.findByText("1 / 2");
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(40);
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(1);
    const viewport = document.querySelector<HTMLElement>(".trajectory-results")!;
    viewport.scrollTop = 100;
    fireEvent.change(screen.getByLabelText("搜索整局行动"), { target: { value: "训练场" } });
    expect(screen.getByText("1 / 2")).toBeVisible();
    expect(viewport.scrollTop).toBe(0);
});
it("对象摘要按具体实例关联，配方只统计已确认制作，回滚仍可查看", async () => {
    setup(<><ObjectTrajectories subject={{ kind: "upgrade", id: "training" }} /><ObjectTrajectories subject={{ kind: "recipe", id: "sword" }} /><ObjectTrajectories subject={{ kind: "equipment", id: "eq-2" }} /></>);
    await screen.findByText("第 2 回合解锁");
    expect(screen.getByText(/已确认制作 1 次 · 首次第 3 回合/)).toBeInTheDocument();
    expect(screen.getByText("第 3 回合制作 · 后续回合")).toBeInTheDocument();
    expect(subjectRows(rows, { kind: "equipment", id: "eq-2" })).toHaveLength(1);
    expect(confirmed(rows[1])).toBe(false);
    const recipe = screen.getByText(/已确认制作 1 次 · 首次第 3 回合/).closest("details")!;
    fireEvent.click(recipe.querySelector("summary")!);
    expect(within(recipe).getByText("已回滚，不再生效")).toBeVisible();
    expect(within(recipe).getAllByText("后续回合")).toHaveLength(2);
});
it("从具体装备的角度区分制作、穿戴和被替换卸下，不混淆同名实例", () => {
    const item = { kind: "equipment" as const, id: "eq-2", name: "铁剑" };
    const subject = { kind: "equipment" as const, id: "eq-2" };
    expect(objectActionTitle(row({ subjects: [{ ...item, created: true }], event: { type: "tool_result", name: "craft_equipment", result: { ok: true } } }), subject)).toBe("制作 铁剑");
    expect(objectActionTitle(row({ subjects: [{ ...item, ownership: { before: null, after: { id: "hero", name: "剑士" } } }], event: { type: "tool_result", name: "equip_item", result: { ok: true } } }), subject)).toBe("剑士 穿戴 铁剑");
    expect(objectActionTitle(row({ subjects: [{ ...item, ownership: { before: { id: "hero", name: "剑士" }, after: null } }], event: { type: "tool_result", name: "equip_item", result: { ok: true } } }), subject)).toBe("从 剑士 身上卸下 铁剑");
    expect(objectActionTitle(row({ subjects: [{ ...item, created: true }], event: { type: "tool_result", name: "craft_equipment", result: { ok: true } } }), { kind: "equipment", id: "eq-1" })).toBeUndefined();
});
it("购买已执行但回合未提交时仍显示发生回合，不算已确认成功", async () => {
    setup(<ObjectTrajectories subject={{ kind: "upgrade", id: "training" }} />, { ...payload, entries: [row({ lifecycle: "uncommitted" })] });
    expect(await screen.findByText("第 2 回合购买成功 · 回合未提交")).toBeVisible();
    expect(screen.queryByText("第 2 回合解锁", { exact: true })).not.toBeInTheDocument();
});
it("覆盖不全和加载失败会明确提示，不宣称对象从未获得", async () => {
    setup(<ObjectTrajectories subject={{ kind: "upgrade", id: "training" }} />, { ...payload, entries: [], warning: "只覆盖当前运行阶段" });
    await screen.findByText("尚无已确认的解锁记录");
    const details = screen.getByText("本局轨迹").closest("details")!;
    fireEvent.click(details.querySelector("summary")!);
    expect(screen.getByText("只覆盖当前运行阶段")).toBeVisible();
    expect(screen.getByText("原日志未记录此对象的操作，不能确定获取回合。")).toBeVisible();
});
it("整局结果定位到归档帧，原始事件及跨回合切换不误读当前工作进程日志", async () => {
    const initial: Observation = { turn: 5, max_turns: 8, gold: 300, experience_pool: 0, materials: {}, adventurers: [], party_size_limit: 3 };
    const attempt = (id: number, turn: number) => ({ id, turn, attempt: 1, status: "completed", score: 0, actions: 1, at: "2026-10-19", updated_at: "2026-10-19" });
    const current: WatchPage = { cursor: 30, selected: 10, attempts: [attempt(10, 5)], records: [{ at: "2026-10-19", after: 0, cursor: 30, event: { type: "turn_started", turn: 5, observation: initial } }] };
    const archived: WatchPage = { cursor: 900, selected: 200, origin: "archive", attempts: [attempt(200, 2), attempt(300, 3), attempt(400, 4)], records: [
        { at: "2026-10-19", after: 100, cursor: 200, event: { type: "turn_started", turn: 2, observation: { ...initial, turn: 2, gold: 100 } } },
        { at: "2026-10-19", after: 220, cursor: 250, event: { ...row().event, result: { ...row().event.result, _observation_after: { ...initial, turn: 2, gold: 80 } } } },
    ] };
    const requests: string[] = [];
    let releaseSlow: (() => void) | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
        const path = String(input); requests.push(path);
        if (path.includes("trajectories?attempt=300")) await new Promise<void>(resolve => { releaseSlow = resolve; });
        const data = path.includes("raw_after=") ? { event: { type: "tool_result", name: "purchase_upgrade", arguments: { upgrade_id: "training" } } }
            : path.includes("trajectories?attempt=300") ? { ...archived, selected: 300, records: [{ ...archived.records[0], cursor: 300, event: { type: "turn_started", turn: 3, observation: { ...initial, turn: 3 } } }] }
            : path.includes("trajectories?attempt=400") ? { ...archived, selected: 400, records: [{ ...archived.records[0], cursor: 400, event: { type: "turn_started", turn: 4, observation: { ...initial, turn: 4 } } }] }
            : path.includes("trajectories?attempt=") ? archived : path.endsWith("/trajectories") ? payload : current;
        return new Response(JSON.stringify(data));
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    function Harness() {
        const [position, setPosition] = useState<WatchPosition>({ attempt: null, cursor: null });
        return <QueryClientProvider client={client}><Theme><WatchRun job={testJob("one", { status: "completed", cursor: 30 })} position={position} onPosition={setPosition} initialFocus={{ module: "actions" }} onStop={vi.fn()} onClone={vi.fn()} /></Theme></QueryClientProvider>;
    }
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "整局检索" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看轨迹 回合2 购买训练场" }));
    await screen.findByText("历史局面", { exact: true });
    expect(within(screen.getByLabelText("当前观看位置的资源")).getByText("80", { exact: true })).toBeVisible();
    expect(screen.getByLabelText("选择观战回合")).toHaveValue("200");
    const raw = within(screen.getByRole("region", { name: "所选行动详情" })).getByText("原始事件与调用参数").closest("details")!;
    fireEvent(raw, new Event("toggle"));
    raw.open = true;
    fireEvent(raw, new Event("toggle"));
    await waitFor(() => expect(requests.some(path => path.includes("trajectories?raw_after=220"))).toBe(true));
    expect(requests.some(path => path.includes("/events?after=220"))).toBe(false);
    fireEvent.change(screen.getByLabelText("选择观战回合"), { target: { value: "300" } });
    await waitFor(() => expect(releaseSlow).toBeDefined());
    fireEvent.change(screen.getByLabelText("选择观战回合"), { target: { value: "400" } });
    await waitFor(() => expect(screen.getByLabelText("选择观战回合")).toHaveValue("400"));
    await act(async () => releaseSlow!());
    expect(screen.getByLabelText("选择观战回合")).toHaveValue("400");
    expect(requests.some(path => path.includes("/watch?attempt=300"))).toBe(false);
});
