import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import App from "./App";
import { testJob } from "./test-jobs";
import type { Job, Snapshot } from "./types";

vi.mock("./CurveBoard", async () => {
    throw new TypeError("Failed to fetch dynamically imported module: /workbench/assets/CurveBoard-old.js");
});
vi.mock("./WatchRun", () => ({ WatchRun: ({ job }: { job: Job }) => {
    if (job.id === "broken") throw new Error("构造的观战渲染错误");
    return <section aria-label="正常观战面板">{job.name}</section>;
} }));
vi.mock("./RunDetail", () => ({ RunDetail: ({ job, onFocus }: { job: Job; onFocus: () => void }) => <section aria-label="运行详情"><span>{job.name}</span><button onClick={onFocus}>专注查看</button></section> }));

function setup(jobs: Job[], url: string) {
    history.replaceState(null, "", url);
    vi.spyOn(console, "error").mockImplementation(() => {});
    const data: Snapshot = { jobs, settings: { max_parallel: 2, per_endpoint: 2 }, presets: ["default"], scheduler_error: null };
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
        const path = String(input);
        requests.push(path);
        if (path === "/api/workbench") return new Response(JSON.stringify(data));
        if (path === "/api/workbench/api-profiles") return new Response(JSON.stringify({ profiles: [] }));
        throw new Error(`未预期请求 ${path}`);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: Infinity } } });
    client.setQueryData(["workbench"], data);
    const view = render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
    return { ...view, requests };
}

it("曲线分包加载失败时保留导航和原 URL，不自动刷新，并可切换到其它工作区", async () => {
    const { requests } = setup([testJob("active")], "/?run=active&section=curves&curves=active");
    const notice = await screen.findByRole("alert", { name: "曲线对比加载失败" });
    expect(notice).toHaveTextContent("请刷新页面重试");
    expect(within(notice).getByRole("button", { name: "刷新页面" })).toBeVisible();
    expect(location.search).toContain("curves=active");
    expect(location.search).toContain("run=active");
    const navigation = screen.getByRole("navigation", { name: "主导航" });
    expect(navigation).toBeVisible();
    fireEvent.click(within(navigation).getByRole("button", { name: /^运行中心/ }));
    expect(screen.getByRole("region", { name: "运行列表" })).toBeVisible();
    fireEvent.click(within(navigation).getByRole("button", { name: "曲线对比" }));
    expect(await screen.findByRole("alert", { name: "曲线对比加载失败" })).toBeVisible();
    expect(requests.every(path => path === "/api/workbench" || path === "/api/workbench/api-profiles")).toBe(true);
    expect(console.error).toHaveBeenCalled();
});

it("多路观战只隔离出错的面板，不卸载其它面板或导航", async () => {
    localStorage.setItem("gmb.watch.v1", JSON.stringify(["broken", "healthy"]));
    setup([testJob("broken", { name: "出错模型", archive_id: "broken-archive" }), testJob("healthy", { name: "正常模型", archive_id: "healthy-archive" })], "/?view=watch");
    expect(await screen.findByRole("alert", { name: "出错模型的观战面板加载失败" })).toBeVisible();
    expect(await screen.findByRole("region", { name: "正常观战面板" })).toHaveTextContent("正常模型");
    expect(screen.getByRole("navigation", { name: "主导航" })).toBeVisible();
});

it("专注视图切换运行后不保留上一局的错误状态", async () => {
    const bad = testJob("broken", { name: "出错模型", archive_id: "broken-archive" });
    const good = testJob("healthy", { name: "正常模型", archive_id: "healthy-archive" });
    setup([bad, good], "/?view=focus&run=broken");
    expect(await screen.findByRole("alert", { name: "观战界面加载失败" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "返回运行列表" }));
    fireEvent.click(screen.getByRole("button", { name: "正常模型" }));
    fireEvent.click(screen.getByRole("button", { name: "专注查看" }));
    expect(await screen.findByRole("region", { name: "正常观战面板" })).toHaveTextContent("正常模型");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
