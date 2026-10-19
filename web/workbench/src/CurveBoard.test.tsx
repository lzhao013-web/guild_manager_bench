import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Theme } from "@radix-ui/themes";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CurveBoard } from "./CurveBoard";
import { buildRunCatalog } from "./runCatalog";
import { testArchive, testJob } from "./test-jobs";
import type { CurveData, CurvePoint } from "./curveModel";

const point = (turn: number, scale = 1): CurvePoint => ({ turn, rank_score: turn === 1 ? 0 : turn * 100 * scale + (turn === 3 && scale === 1 ? 0.1234 : 0), gold_income: turn * 10 * scale, experience_income: turn * 20 * scale, gold: 50 - turn * 2, experience_pool: turn * 3 });
const data = (points: CurvePoint[]): CurveData => ({ run_id: "fixture", status: "completed", max_turns: 8, points, updated_at: null, warning: null,
    conditions: { preset: "full", protocol_hash: "rules", settings_hash: "settings", data_hash: "data", game_seed: 7, scoring_seed: 11 } });
function setup(empty = false, running = false) {
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
        return new Response(JSON.stringify(data(points)));
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
    fireEvent.change(screen.getByLabelText("添加曲线 run"), { target: { value: "archive:cli" } });
    expect(await within(values).findByText("构造 CLI")).toBeVisible();
    expect(requests.some(path => path.includes("run_id=cli"))).toBe(true);
    expect(requests.every(path => path.startsWith("GET ") && !path.includes("/replay") && !path.includes("/rescore"))).toBe(true);
});

it("运行结束和检查点变化后立即追上最后一个回合，不因停止轮询而漏掉终态曲线", async () => {
    const { finish } = setup(false, true);
    expect(await screen.findByRole("heading", { name: "回合 3 对比" })).toBeVisible();
    await act(() => finish());
    expect(await screen.findByRole("heading", { name: "回合 4 对比" })).toBeVisible();
    expect(screen.getByRole("region", { name: "同回合数值与差值" })).toHaveTextContent("400");
});

it("没有结算数据时显示原因，不画三张空图或假曲线", async () => {
    setup(true);
    expect(await screen.findByText("尚无可读取的结算曲线", { exact: true })).toBeVisible();
    expect(screen.queryByRole("region", { name: "经济曲线" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "同回合数值与差值" })).not.toBeInTheDocument();
});
