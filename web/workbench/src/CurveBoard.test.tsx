import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Theme } from "@radix-ui/themes";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { CurveBoard } from "./CurveBoard";
import { buildRunCatalog } from "./runCatalog";
import { testArchive, testJob } from "./test-jobs";
import type { CurveData, CurvePoint, CurveMilestone } from "./curveModel";

const point = (turn: number, scale = 1): CurvePoint => ({ turn, rank_score: turn === 1 ? 0 : turn * 100 * scale + (turn === 3 && scale === 1 ? 0.1234 : 0), gold_income: turn * 10 * scale, experience_income: turn * 20 * scale, gold: 50 - turn * 2, experience_pool: turn * 3 });
const data = (points: CurvePoint[]): CurveData => ({ run_id: "fixture", status: "completed", max_turns: 8, points, updated_at: null, warning: null,
    conditions: { preset: "full", protocol_hash: "rules", settings_hash: "settings", data_hash: "data", game_seed: 7, scoring_seed: 11 } });
function setup(empty = false, running = false, milestones: CurveMilestone[] = [], records?: Record<string, CurveData>) {
    const requests: string[] = [];
    const catalog = buildRunCatalog([testJob("a", { name: "构造模型 A", archive_id: "A", status: running ? "running" : "completed" }), testJob("b", { name: "构造模型 B", archive_id: "B", status: "completed" })]);
    let finished = false;
    let publish: (value: typeof catalog) => void;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const path = String(input);
        requests.push(`${init?.method || "GET"} ${path}`);
        if (path === "/api/llm/runs") return new Response(JSON.stringify({ runs: [testArchive("cli", { model: "构造 CLI" })] }));
        const key = new URL(path, "http://localhost").searchParams.get("job_id");
        const points = empty ? [] : key === "b" ? [point(1, 2), point(3, 2)] : [point(1), point(2), point(3), ...(finished ? [point(4)] : [])];
        return new Response(JSON.stringify(records?.[key!] || { ...data(points), milestones }));
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    function Harness() {
        const [ids, setIds] = useState(["a", "b"]);
        const [current, setCurrent] = useState(catalog);
        publish = setCurrent;
        return <QueryClientProvider client={client}><Theme><CurveBoard catalog={current} selectedIds={ids} onSelection={setIds} /></Theme></QueryClientProvider>;
    }
    render(<Harness />);
    return { requests, finish: () => {
        finished = true;
        publish(buildRunCatalog(catalog.runs.map(job => job.id === "a" ? { ...job, status: "completed", checkpoint_at: "2026-10-19T12:00:00Z" } : job)));
    } };
}

it("三类曲线统一展示并联动定位，收入与余额可切换，缺回合和差值不补零", async () => {
    const { requests } = setup();
    const values = await screen.findByRole("region", { name: "同回合数值与差值" });
    await waitFor(() => expect(values).toHaveTextContent("600"));
    expect(values).toHaveTextContent("300.1234");
    const workspace = screen.getByRole("region", { name: "统一曲线对比工作区" });
    workspace.focus();
    expect(workspace).toHaveFocus();
    expect(workspace).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("region", { name: "经济曲线" })).toBeVisible();
    expect(screen.getByRole("region", { name: "经验曲线" })).toBeVisible();
    expect(screen.getByRole("region", { name: "战力曲线" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("定位对比回合"), { target: { value: "2" } });
    const rows = within(values).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("200");
    expect(within(rows[2]).getAllByText("未记录")).toHaveLength(3);
    expect(within(rows[2]).getAllByText("差值未知")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "结算后余额" }));
    expect(within(values).getByRole("columnheader", { name: "结算后未分配经验池" })).toBeVisible();
    expect(rows[1]).toHaveTextContent("46");
    fireEvent.keyDown(screen.getByRole("img", { name: /经济，横轴/ }), { key: "ArrowLeft" });
    expect(screen.getByLabelText("定位对比回合")).toHaveValue(1);
    expect(within(rows[1]).getAllByRole("cell")[2]).toHaveTextContent("0");
    await userEvent.click(screen.getByRole("button", { name: "添加曲线 run" }));
    await userEvent.type(screen.getByRole("combobox", { name: /搜索运行名称/ }), "cli");
    await userEvent.click(screen.getByRole("option", { name: /构造 CLI/ }));
    expect(await within(values).findByText("构造 CLI")).toBeVisible();
    expect(requests.some(path => path.includes("run_id=cli"))).toBe(true);
    expect(requests.every(path => path.startsWith("GET ") && !path.includes("/replay") && !path.includes("/rescore"))).toBe(true);
});

it("三类节点在图下按 run 分轨，缺数值仍可定位，同数值不再合并不同 run", async () => {
    setup(false, false, [
        { id: "exp1", turn: 1, kind: "expansion", label: "解锁扩编 1", detail: "解锁扩编 1" },
        { id: "exp2", turn: 2, kind: "expansion", label: "解锁扩编 2", detail: "解锁扩编 2" },
        { id: "a9", turn: 2, kind: "level_9", label: "剑士达到 Lv9", detail: "剑士：Lv8 → Lv9" },
        { id: "b9", turn: 2, kind: "level_9", label: "神官达到 Lv9", detail: "神官：Lv8 → Lv10" },
        { id: "boss", turn: 3, kind: "boss_victory", label: "战胜首领", detail: "剑士战胜首领座狼" },
        { id: "challenge", turn: 3, kind: "challenge_victory", label: "完成挑战", detail: "神官完成毁灭先知挑战" },
    ]);
    const experience = await screen.findByRole("region", { name: "经验曲线" });
    const mark = within(experience).getByRole("button", { name: /轨道节点.*构造模型 A.*T2/ });
    expect(mark).toHaveAttribute("data-event-count", "2");
    fireEvent.click(mark);
    expect(screen.getByLabelText("定位对比回合")).toHaveValue(2);
    const details = screen.getByRole("region", { name: "所选回合关键节点" });
    expect(details).toHaveTextContent("剑士：Lv8 → Lv9");
    expect(details).toHaveTextContent("神官：Lv8 → Lv10");
    const missing = within(experience).getByRole("button", { name: /轨道节点.*构造模型 B.*T2/ });
    expect(missing).not.toBe(mark);
    fireEvent.focus(missing);
    expect(screen.getByLabelText("定位对比回合")).toHaveValue(2);
    expect(experience.querySelector(".curve-plot button")).toBeNull();
    fireEvent.click(within(experience).getByText(/节点详情/).closest("summary")!);
    expect(within(experience).getByRole("button", { name: /定位节点.*构造模型 B.*T2/ })).toHaveTextContent("数值未记录");
    fireEvent.click(screen.getByRole("button", { name: "结算后余额" }));
    const economy = screen.getByRole("region", { name: "经济曲线" });
    const first = within(economy).getByRole("button", { name: /轨道节点.*构造模型 A.*T1/ });
    const second = within(economy).getByRole("button", { name: /轨道节点.*构造模型 B.*T1/ });
    expect(first).not.toBe(second);
    expect(first).toHaveAttribute("data-event-count", "1");
    expect(second).toHaveAttribute("data-event-count", "1");
    expect(first).toHaveTextContent("扩1");
    fireEvent.change(screen.getByLabelText("曲线起始回合"), { target: { value: "3" } });
    expect(within(economy).queryByRole("button", { name: /轨道节点/ })).not.toBeInTheDocument();
    expect(within(experience).queryByRole("button", { name: /轨道节点/ })).not.toBeInTheDocument();
    const power = screen.getByRole("region", { name: "战力曲线" });
    const victory = within(power).getByRole("button", { name: /轨道节点.*构造模型 A.*T3/ });
    expect(victory).toHaveAttribute("data-event-count", "2");
    expect(victory).toHaveTextContent("首挑");
    expect(power.querySelector(".curve-plot button")).toBeNull();
});

it("运行结束和检查点变化后立即追上最后一个回合，不因停止轮询而漏掉终态曲线", async () => {
    const { finish } = setup(false, true);
    expect(await screen.findByRole("heading", { name: "回合 3 对比" })).toBeVisible();
    await act(() => finish());
    expect(await screen.findByRole("heading", { name: "回合 4 对比" })).toBeVisible();
    expect(screen.getByRole("region", { name: "同回合数值与差值" })).toHaveTextContent("400");
});

it("去掉比较声明但保留曲线过时提示，共同回合按钮仍定位已记录数值且遵守范围", async () => {
    const warning = "跨版本执行：历史与当前规则不完全一致，不作同版本标准比较。当前回放暂不可读或格式不符，保留上次成功读取的曲线";
    setup(false, false, [], {
        a: { ...data([point(1), point(2), point(4)]), warning },
        b: { ...data([point(1, 2), point(3, 2), { ...point(4, 2), gold_income: null }]), warning },
    });
    const values = await screen.findByRole("region", { name: "同回合数值与差值" });
    await waitFor(() => expect(values).toHaveTextContent("800"));
    const risk = screen.getByLabelText("曲线数据提示");
    expect(risk.querySelector("summary")).toHaveTextContent("2 个 run：曲线更新失败，暂时显示上次的数据");
    expect(screen.queryByText(/同版本标准比较/)).not.toBeInTheDocument();
    expect(risk).not.toHaveAttribute("open");
    fireEvent.click(risk.querySelector("summary")!);
    expect(within(risk).getByText(/构造模型 A/)).toBeVisible();
    expect(within(risk).getByText(/构造模型 B/)).toBeVisible();
    expect(screen.getByText("当前回合 4：1 / 2 路具备三项指标")).toBeVisible();
    const common = screen.getByRole("button", { name: "最近共同已记录回合" });
    fireEvent.click(common);
    expect(screen.getByLabelText("定位对比回合")).toHaveValue(1);
    expect(values).not.toHaveTextContent("差值未知");
    fireEvent.click(screen.getByRole("button", { name: "全程 / 跟随最新" }));
    expect(screen.getByLabelText("定位对比回合")).toHaveValue(4);
    fireEvent.click(screen.getByRole("button", { name: "结算后余额" }));
    fireEvent.click(common);
    expect(screen.getByLabelText("定位对比回合")).toHaveValue(4);
    fireEvent.change(screen.getByLabelText("曲线结束回合"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("曲线起始回合"), { target: { value: "2" } });
    expect(common).toBeDisabled();
    expect(screen.getByText("当前范围内没有共同已记录的三项指标回合")).toBeVisible();
});

it.each(["条件资料缺失", "规则、种子与上限不同"])("%s时正常展示曲线和差值，不显示比较条件提示", async scenario => {
    const first = { ...data([point(1), point(3)]), milestones: [], warning: "跨版本执行：历史与当前规则不完全一致，不作同版本标准比较" };
    const second = { ...data([point(1, 2), point(3, 2)]), milestones: [],
        conditions: scenario === "条件资料缺失" ? {} : { ...first.conditions, protocol_hash: "other", game_seed: 99 },
        max_turns: 35 };
    setup(false, false, [], { a: first, b: second });
    const values = await screen.findByRole("region", { name: "同回合数值与差值" });
    await waitFor(() => expect(values).toHaveTextContent("600"));
    expect(values).toHaveTextContent("差值 +299.8766");
    expect(screen.getByRole("region", { name: "战力曲线" })).toBeVisible();
    expect(screen.queryByText(/部分规则信息|仅供并列查看|同条件/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("曲线数据提示")).not.toBeInTheDocument();
});

it("没有结算数据时显示原因，不画三张空图或假曲线", async () => {
    setup(true);
    expect(await screen.findByText("暂无曲线数据", { exact: true })).toBeVisible();
    expect(screen.queryByRole("region", { name: "经济曲线" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "同回合数值与差值" })).not.toBeInTheDocument();
});
