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
const plan = row({ subjects: [{ kind: "upgrade", id: "training", name: "训练场" }, { kind: "equipment", id: "eq-1", name: "青铜剑" }],
    names: { hero: "伊莲", sword: "青铜剑", training: "训练场" }, event: { type: "tool_result", name: "execute_plan", arguments: { plan_id: "p-one" }, result: {
        ok: true, already_executed: false, operation_count: 4, events: [
            { action: { type: "craft", recipe_id: "sword" }, summary: "制作青铜剑", changes: [{ kind: "resource", label: "金币", before: 100, after: 90 }] },
            { action: { type: "purchase_upgrade", upgrade_id: "training" }, summary: "购买训练场", changes: [{ kind: "resource", label: "金币", before: 90, after: 50 }] },
            { action: { type: "allocate_experience", adventurer_id: "hero", amount: 20 }, summary: "分配经验", changes: [{ kind: "resource", label: "经验池", before: 50, after: 30 }] },
            { action: { type: "exchange_materials", material_id: "iron_ore", quantity: 2 }, summary: "兑换铁矿石" },
        ],
    } } });
function setup(children: React.ReactNode, patch = payload, status = 200) {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(patch), { status }));
    const onLocate = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><Theme><TrajectoryProvider job={testJob("one", { status: "completed" })} enabled viewTurn={2} onLocate={onLocate}>{children}</TrajectoryProvider></Theme></QueryClientProvider>);
    return { fetch, onLocate };
}
it("概览始终统计整局，类别与状态入口清除旧条件后定位对应记录", async () => {
    const { onLocate } = setup(<RunTrajectories />);
    const overview = screen.getByRole("region", { name: "整局工具调用概览" });
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
it("整局包括查询、预览、备忘及未知工具，可按工具和执行状态过滤", async () => {
    const names = ["get_crafting", "get_forecast", "preview_monster_refresh", "refresh_monsters", "preview_team_power", "write_memo", "future_custom_tool"];
    const allTools = names.map((name, index) => row({ id: `200:${index + 1}`, ordinal: index + 1, cursor: 250 + index,
        subjects: [], event: { type: index === 6 ? "tool_call" : "tool_result", name, result: index === 6 ? {} : { ok: index !== 5 } } }));
    setup(<RunTrajectories />, { ...payload, entries: allTools });
    await waitFor(() => expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(7));
    expect(screen.getByRole("option", { name: /预览怪物刷新/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("筛选轨迹操作"), { target: { value: "preview_monster_refresh" } });
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: /查看轨迹/ })).toHaveTextContent("只读预览");
    fireEvent.click(screen.getByText(/按工具汇总/).closest("summary")!);
    fireEvent.click(screen.getByRole("button", { name: "筛选工具 future_custom_tool" }));
    expect(screen.getByLabelText("筛选轨迹操作")).toHaveValue("future_custom_tool");
    fireEvent.change(screen.getByLabelText("筛选轨迹状态"), { target: { value: "pending" } });
    expect(screen.getByRole("button", { name: /查看轨迹/ })).toHaveTextContent("执行中");
});

it("方案子操作按类别和具体工具检索，保留步骤及真实定位，调用统计不膨胀", async () => {
    const { fetch, onLocate } = setup(<RunTrajectories />, { ...payload, entries: [plan] });
    await waitFor(() => expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(5));
    const overview = screen.getByRole("region", { name: "整局工具调用概览" });
    expect(within(overview).getByRole("button", { name: /共/ })).toHaveTextContent("共 1 次调用");
    const craft = within(overview).getByRole("button", { name: "查看装备制作记录" });
    expect(within(craft).getByText("1", { exact: true })).toBeVisible();
    expect(craft).toHaveTextContent("已确认 1");
    expect(craft).not.toHaveTextContent("+1步");
    const execute = within(overview).getByRole("button", { name: "查看执行方案记录" });
    expect(execute).not.toHaveTextContent("共4步");
    expect(execute.getAttribute("title")).toContain("包含 4 步子操作");
    expect(within(execute).getByText("1", { exact: true })).toBeVisible();
    expect(execute).toHaveTextContent("已确认 1");
    expect(screen.getByText("按工具汇总 · 1 种 · 1 次调用")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /制作装备.*1条记录/ })).toHaveValue("craft_equipment");
    fireEvent.click(craft);
    const child = screen.getByRole("button", { name: "查看轨迹 回合2 制作青铜剑 / 方案第1/4步" });
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(1);
    expect(child).toHaveTextContent("金币 100 → 90（-10）");
    expect(child).toHaveTextContent("点击查看所属方案");
    expect(onLocate).not.toHaveBeenCalled();
    fireEvent.click(child);
    await waitFor(() => expect(onLocate).toHaveBeenCalledWith(plan.attempt, plan.cursor, "archive"));
    expect(fetch.mock.calls.every(([path]) => String(path).endsWith("/trajectories"))).toBe(true);
    fireEvent.change(screen.getByLabelText("筛选轨迹操作"), { target: { value: "exchange_materials" } });
    expect(screen.getByRole("button", { name: /查看轨迹.*兑换铁矿石/ })).toHaveTextContent("方案第 4 / 4 步");
});
it("类别计数合并方案子操作，悬浮保留调用与确认数来源", async () => {
    const direct = Array.from({ length: 6 }, (_, index) => row({
        id: `direct:${index}`, ordinal: index + 1, cursor: 300 + index,
        lifecycle: index < 4 ? "confirmed" : "uncommitted",
    }));
    const events = Array.from({ length: 8 }, () => ({ action: { type: "purchase_upgrade", upgrade_id: "training" }, summary: "购买训练场" }));
    const batch = { ...plan, id: "plan:1", ordinal: 7, cursor: 350,
        event: { ...plan.event, result: { ...plan.event.result, operation_count: 8, events } } };
    setup(<RunTrajectories />, { ...payload, entries: [...direct, batch] });
    const overview = screen.getByRole("region", { name: "整局工具调用概览" });
    const upgrade = within(overview).getByRole("button", { name: "查看天赋购买记录" });
    await waitFor(() => expect(upgrade).toBeEnabled());
    expect(within(upgrade).getByText("14", { exact: true })).toBeVisible();
    expect(upgrade).toHaveTextContent("已确认 12");
    expect(upgrade).not.toHaveTextContent("+8步");
    expect(upgrade.getAttribute("title")).toContain("直接调用 6 次、方案子操作 8 步");
    expect(upgrade.getAttribute("title")).toContain("直接调用 4 次、方案子操作 8 步");
    const execute = within(overview).getByRole("button", { name: "查看执行方案记录" });
    expect(execute).not.toHaveTextContent("共8步");
    expect(execute.getAttribute("title")).toContain("包含 8 步子操作");
    expect(within(execute).getByText("1", { exact: true })).toBeVisible();
    expect(execute).toHaveTextContent("已确认 1");
});
it("子操作关键词包含本步名称、ID和可读输入，不误命中同方案的其它对象", async () => {
    setup(<RunTrajectories />, { ...payload, entries: [plan] });
    await screen.findByRole("button", { name: /查看轨迹.*方案第3\/4步/ });
    fireEvent.change(screen.getByLabelText("筛选轨迹操作"), { target: { value: "allocate_experience" } });
    fireEvent.change(screen.getByLabelText("搜索整局行动"), { target: { value: "伊莲" } });
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("搜索整局行动"), { target: { value: "hero" } });
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("搜索整局行动"), { target: { value: "青铜剑" } });
    expect(screen.queryByRole("button", { name: /查看轨迹/ })).not.toBeInTheDocument();
});
it("试算、失败与重复提交不展开子操作，已回滚步骤保留历史但不冒充有效成功", async () => {
    const copy = (id: number, patch: Record<string, any>, name = "execute_plan") => ({ ...plan, id: `200:${id}`, ordinal: id,
        event: { ...plan.event, name, result: { ...plan.event.result, ...patch } } });
    setup(<RunTrajectories />, { ...payload, entries: [
        copy(1, { already_executed: true }), copy(2, { ok: false, error: "方案已失效" }), copy(3, { simulation_only: true }, "preview_team_power"),
        { ...copy(4, {}), lifecycle: "rolled_back" },
    ] });
    await waitFor(() => expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(8));
    const overview = screen.getByRole("region", { name: "整局工具调用概览" });
    expect(within(overview).getByRole("button", { name: /共/ })).toHaveTextContent("共 4 次调用");
    fireEvent.change(screen.getByLabelText("筛选轨迹操作"), { target: { value: "category:craft" } });
    expect(screen.getByRole("button", { name: /查看轨迹/ })).toHaveTextContent("已回滚，不再生效");
    fireEvent.change(screen.getByLabelText("筛选轨迹状态"), { target: { value: "confirmed" } });
    expect(screen.queryByRole("button", { name: /查看轨迹/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("筛选轨迹状态"), { target: { value: "rolled_back" } });
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(1);
});

it("概览加载期间显示未知而非假零，失败时不提供可点击的零计数", async () => {
    setup(<RunTrajectories />, payload, 503);
    const overview = screen.getByRole("region", { name: "整局工具调用概览" });
    expect(overview).toHaveAttribute("aria-busy", "true");
    expect(within(overview).queryByText("0", { exact: true })).not.toBeInTheDocument();
    await screen.findByRole("alert");
    expect(within(overview).getByText("轨迹读取失败", { exact: true })).toBeVisible();
    expect(within(overview).queryByText("0", { exact: true })).not.toBeInTheDocument();
    expect(within(overview).getByRole("button", { name: "查看装备制作记录" })).toBeDisabled();
});
it("覆盖不完整的统计明确属于当前阶段，不宣称整局零操作", async () => {
    setup(<RunTrajectories />, { ...payload, origin: "execution", entries: [], warning: "仅覆盖当前运行阶段" });
    const overview = screen.getByRole("region", { name: "整局工具调用概览" });
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
it("大量方案子操作也按40条分页，翻页不改变调用总数", async () => {
    const events = Array.from({ length: 45 }, (_, index) => ({ action: { type: "allocate_experience", adventurer_id: "hero", amount: index + 1 }, summary: `第${index + 1}次培养` }));
    const many = { ...plan, event: { ...plan.event, result: { ...plan.event.result, operation_count: 45, events } } };
    setup(<RunTrajectories />, { ...payload, entries: [many] });
    await screen.findByText("1 / 2");
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(40);
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getAllByRole("button", { name: /查看轨迹/ })).toHaveLength(6);
    expect(screen.getByRole("button", { name: /查看轨迹.*方案第45\/45步/ })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "整局工具调用概览" })).getByRole("button", { name: /共/ })).toHaveTextContent("共 1 次调用");
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
it("点击方案子操作读取整个原子提交后的局面与父调用参数，不伪造中间帧", async () => {
    const initial: Observation = { turn: 5, max_turns: 8, gold: 300, experience_pool: 50, materials: {}, adventurers: [], party_size_limit: 3 };
    const attempt = { id: 200, turn: 2, attempt: 1, status: "completed", score: 0, actions: 1, at: "", updated_at: "" };
    const current: WatchPage = { cursor: 30, selected: 10, attempts: [], records: [
        { at: "", after: 0, cursor: 30, event: { type: "turn_started", turn: 5, observation: initial } },
    ] };
    const archived: WatchPage = { cursor: 900, selected: 200, origin: "archive", attempts: [attempt], records: [
        { at: "", after: 100, cursor: 200, event: { type: "turn_started", turn: 2, observation: { ...initial, turn: 2, gold: 100 } } },
        { at: "", after: 220, cursor: plan.cursor, event: { ...plan.event, result: { ...plan.event.result, _observation_after: { ...initial, turn: 2, gold: 50, experience_pool: 30 } } } },
    ] };
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
        const path = String(input); requests.push(path);
        return new Response(JSON.stringify(path.includes("trajectories?attempt=") ? archived : path.endsWith("/trajectories") ? { ...payload, entries: [plan] } : current));
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    function Harness() {
        const [position, setPosition] = useState<WatchPosition>({ attempt: null, cursor: null });
        return <QueryClientProvider client={client}><Theme><WatchRun runId="plan" job={testJob("plan", { status: "completed", cursor: 30 })} position={position} onPosition={setPosition} initialFocus={{ module: "actions", actionView: "search" }} onStop={vi.fn()} onClone={vi.fn()} /></Theme></QueryClientProvider>;
    }
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: /查看轨迹.*制作青铜剑.*方案第1\/4步/ }));
    await waitFor(() => expect(within(screen.getByLabelText("当前观看位置的资源")).getByText("50", { exact: true })).toBeVisible());
    expect(screen.getByLabelText("选择观战回合")).toHaveValue("200");
    expect(screen.getByRole("region", { name: "所选行动详情" })).toHaveTextContent("p-one");
    expect(requests.some(path => path.includes("trajectories?attempt=200"))).toBe(true);
    expect(requests.some(path => path.includes("/watch?attempt="))).toBe(false);
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
        return <QueryClientProvider client={client}><Theme><WatchRun runId="one" job={testJob("one", { status: "completed", cursor: 30 })} position={position} onPosition={setPosition} initialFocus={{ module: "actions" }} onStop={vi.fn()} onClone={vi.fn()} /></Theme></QueryClientProvider>;
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
