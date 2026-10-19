import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import App from "./App";
import type { Job, Snapshot } from "./types";
import { testArchive, testJob } from "./test-jobs";

vi.mock("./CurveBoard", () => ({ CurveBoard: ({ selectedIds, onBack }: { selectedIds: string[]; onBack?: () => void }) => <section aria-label="曲线工作区"><p>{selectedIds.join("/")}</p>{onBack && <button onClick={onBack}>返回原工作区</button>}</section> }));
vi.mock("./WatchRun", () => ({ WatchRun: ({ job }: { job: Job }) => <section aria-label="专注 run"><h2>{job.name}</h2><p>{job.id} / {job.status}</p></section> }));
vi.mock("./RunDetail", () => ({ RunDetail: ({ job, onFocus }: { job: Job; onFocus: () => void }) => <section aria-label="run 详情"><span>{job.name}</span><button onClick={onFocus}>专注查看</button></section> }));

function setup(jobs: Job[], url = "/") {
    history.replaceState(null, "", url);
    let data: Snapshot = { jobs, settings: { max_parallel: 2, per_endpoint: 2 }, presets: ["default"], scheduler_error: null };
    vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
        const path = String(input);
        if (path === "/api/workbench") return new Response(JSON.stringify(data));
        if (path === "/api/llm/runs") return new Response(JSON.stringify({ runs: [testArchive()] }));
        if (path === "/api/workbench/api-profiles") return new Response(JSON.stringify({ profiles: [] }));
        throw new Error(`未预期请求 ${path}`);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false, staleTime: Infinity } } });
    client.setQueryData(["workbench"], data);
    render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
    return { client, update: (next: Job[]) => act(() => { data = { ...data, jobs: next }; client.setQueryData(["workbench"], data); }) };
}

it("多选运行进入统一曲线工作区，续跑使用同一个身份，并能返回运行列表", async () => {
    const old = testJob("original", { status: "interrupted" });
    const latest = testJob("latest", { created_at: "2026-10-19T11:00:00Z" });
    const other = testJob("other", { name: "另一个构造模型", archive_id: "other-archive" });
    setup([old, latest, other]);
    fireEvent.click(screen.getByRole("checkbox", { name: "选择当前筛选的全部运行" }));
    fireEvent.click(screen.getByRole("button", { name: "对比选中曲线" }));
    const board = await screen.findByRole("region", { name: "曲线工作区" });
    expect(board).toHaveTextContent("original/other");
    fireEvent.click(within(board).getByRole("button", { name: "返回原工作区" }));
    expect(screen.getByRole("region", { name: "运行列表" })).toBeVisible();
    expect(screen.getAllByRole("row")).toHaveLength(3);
});

it("曲线书签中的选择优先于浏览器上次保存的选择", async () => {
    localStorage.setItem("gmb.curves.v1", JSON.stringify(["previous"]));
    setup([testJob("requested")], "/?section=curves&curves=requested");
    const board = await screen.findByRole("region", { name: "曲线工作区" });
    await waitFor(() => expect(board).toHaveTextContent("requested"));
    expect(board).not.toHaveTextContent("previous");
});

it("运行中心只显示活动 run，历史归并续跑，不把恢复的旧累计数据相加", async () => {
    const old = testJob("old", { status: "interrupted" });
    const latest = testJob("latest", { status: "completed", created_at: "2026-10-18T11:00:00Z", turn: 35, last_score: 2290.3388 });
    const active = testJob("active", { name: "正在执行的另一局", archive_id: "other" });
    setup([latest, old, active]);
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: old.name })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "历史存档" }));
    const row = await screen.findByRole("row", { name: /构造的同名模型/ });
    expect(row).toHaveTextContent("已完成");
    expect(row).toHaveTextContent("35 / 35");
    expect(row).toHaveTextContent("2,290.3");
    expect(row).not.toHaveTextContent("595.3");
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.queryByText("正在执行的另一局")).not.toBeInTheDocument();
});

it("活动 run 结束后离开列表、发出通知，但不关闭正在看的详情", async () => {
    const running = testJob("current");
    const { update } = setup([running]);
    fireEvent.click(screen.getByRole("button", { name: running.name }));
    fireEvent.click(screen.getByRole("button", { name: "专注查看" }));
    expect(await screen.findByRole("region", { name: "专注 run" })).toHaveTextContent("current / running");
    update([{ ...running, status: "completed" }]);
    await waitFor(() => expect(screen.getByRole("region", { name: "专注 run" })).toHaveTextContent("current / completed"));
    expect(await screen.findByLabelText("运行结果通知")).toHaveTextContent("已完成，已移至历史存档");
    fireEvent.click(screen.getByRole("button", { name: "返回运行列表" }));
    expect(screen.getByText("当前没有排队或执行中的 run")).toBeVisible();
    expect(screen.queryByRole("region", { name: "run 详情" })).not.toBeInTheDocument();
});

it("续跑前的旧链接定位到同一个 run 的最新状态，初次打开不弹历史通知", async () => {
    const old = testJob("old", { status: "interrupted" });
    const latest = testJob("new", { status: "completed", created_at: "2026-10-18T11:00:00Z" });
    setup([latest, old], "/?view=focus&run=old");
    expect(await screen.findByRole("region", { name: "专注 run" })).toHaveTextContent("new / completed");
    expect(screen.queryByLabelText("运行结果通知")).not.toBeInTheDocument();
});

it("关注和观战中的旧执行 ID 会归并，同一 run 不会占两个面板", async () => {
    localStorage.setItem("gmb.watch.v1", JSON.stringify(["old", "new"]));
    localStorage.setItem("gmb.pins.v1", JSON.stringify(["new"]));
    const old = testJob("old", { status: "interrupted" });
    const current = testJob("new", { created_at: "2026-10-18T11:00:00Z" });
    setup([current, old], "/?view=watch");
    expect(await screen.findAllByRole("region", { name: "专注 run" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "总览" }));
    expect(screen.getByRole("button", { name: `取消关注 ${current.name}` })).toHaveAttribute("aria-pressed", "true");
});

it("无存档的启动失败仍可查看和重新创建，并会明确提醒失败", async () => {
    const queued = testJob("failed", { status: "queued", archive_id: null, turn: null });
    const { update } = setup([queued]);
    update([{ ...queued, status: "failed", error: "构造启动失败" }]);
    expect(within(await screen.findByLabelText("运行结果通知")).getByRole("alert")).toHaveTextContent("失败");
    fireEvent.click(screen.getByRole("button", { name: "查看历史存档" }));
    expect(await screen.findByText("未生成存档")).toBeVisible();
    expect(screen.getByText("构造启动失败")).toBeVisible();
    expect(screen.getByRole("button", { name: "重新创建" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "查看详情" })).toBeEnabled();
});

it("两次轮询之间已失败的续跑也会提醒，通知不占主画面且可打开最新结果", async () => {
    const first = testJob("first", { status: "interrupted" });
    const { update } = setup([first]);
    expect(screen.queryByLabelText("运行结果通知")).not.toBeInTheDocument();
    update([first, testJob("fast-failure", { status: "failed", created_at: "2026-10-18T11:00:00Z", error: "构造快速失败" })]);
    const notifications = await screen.findByLabelText("运行结果通知");
    expect(within(notifications).getByRole("alert")).toHaveTextContent("1 个失败");
    expect(notifications).not.toHaveAttribute("open");
    fireEvent.click(notifications.querySelector("summary")!);
    fireEvent.click(within(notifications).getByRole("button", { name: "查看结果" }));
    expect(await screen.findByRole("region", { name: "专注 run" })).toHaveTextContent("fast-failure / failed");
    expect(notifications).not.toHaveAttribute("open");
    expect(screen.getByRole("button", { name: "返回历史存档" })).toBeVisible();
});

it("续跑后 run 离开历史，批次仍只计为原实验的一局", async () => {
    const old = testJob("old", { status: "interrupted" });
    const { update } = setup([old]);
    fireEvent.click(screen.getByRole("button", { name: "历史存档" }));
    expect(await screen.findByRole("button", { name: "续跑" })).toBeEnabled();
    update([old, testJob("resumed", { status: "queued", created_at: "2026-10-18T11:00:00Z", batch_id: "resume-batch", batch_name: "不应成为新实验" })]);
    await waitFor(() => expect(screen.queryByRole("button", { name: "续跑" })).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "实验批次" }));
    expect(screen.getByRole("heading", { name: "原始实验" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "不应成为新实验" })).not.toBeInTheDocument();
    expect(screen.getByText(/1 个 run/)).toBeVisible();
});
