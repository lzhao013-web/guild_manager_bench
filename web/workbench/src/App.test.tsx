import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import App from "./App";
import type { Job, Snapshot } from "./types";
import { testArchive, testJob } from "./test-jobs";

vi.mock("./CurveBoard", () => ({ CurveBoard: ({ selectedIds, onBack }: { selectedIds: string[]; onBack?: () => void }) => <section aria-label="曲线工作区"><p>{selectedIds.join("/")}</p>{onBack && <button onClick={onBack}>返回原工作区</button>}</section> }));
vi.mock("./WatchRun", () => ({ WatchRun: ({ job }: { job: Job }) => <section aria-label="专注 run"><h2>{job.name}</h2><p>{job.id} / {job.status}</p></section> }));
vi.mock("./RunDetail", () => ({ RunDetail: ({ job, onFocus }: { job: Job; onFocus: () => void }) => <section aria-label="run 详情"><span>{job.name}</span><button onClick={onFocus}>专注查看</button></section> }));

function setup(jobs: Job[], url = "/") {
    history.replaceState(null, "", url);
    let data: Snapshot = { jobs, settings: { max_parallel: 2, per_endpoint: 2 }, presets: ["default"], scheduler_error: null };
    let archives = [testArchive()];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const path = String(input);
        if (init?.method === "DELETE") {
            const jobId = path.split("/").at(-1)!;
            const runId = data.jobs.find(job => job.id === jobId)!.archive_id;
            const removed = data.jobs.filter(job => job.archive_id === runId).map(job => job.id);
            data = { ...data, jobs: data.jobs.filter(job => !removed.includes(job.id)) };
            archives = archives.filter(archive => archive.run_id !== runId);
            return new Response(JSON.stringify({ deleted: true, run_id: runId, deleted_job_ids: removed }));
        }
        if (path === "/api/workbench") return new Response(JSON.stringify(data));
        if (path === "/api/llm/runs") return new Response(JSON.stringify({ runs: archives }));
        if (path === "/api/workbench/api-profiles") return new Response(JSON.stringify({ profiles: [] }));
        throw new Error(`未预期请求 ${path}`);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false, staleTime: Infinity } } });
    client.setQueryData(["workbench"], data);
    render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
    return { client, update: (next: Job[]) => act(() => { data = { ...data, jobs: next }; client.setQueryData(["workbench"], data); }) };
}

it("运行列表显示累计模型耗时，续跑不重复相加，零值和缺失有区别", () => {
    setup([
        testJob("old", { status: "interrupted", model_duration_ms: 120000 }),
        testJob("new", { name: "续跑耗时样例", created_at: "2026-10-19T11:00:00Z", model_duration_ms: 180000 }),
        testJob("zero", { name: "零耗时样例", archive_id: "zero", model_duration_ms: 0 }),
        testJob("missing", { name: "缺失耗时样例", archive_id: "missing" }),
    ]);
    expect(screen.getByRole("columnheader", { name: "累计耗时" })).toHaveAttribute("title", expect.stringContaining("模型调用累计"));
    expect(screen.getAllByRole("columnheader")[4]).toHaveTextContent("累计耗时");
    const duration = (name: string) => screen.getByRole("button", { name }).closest("tr")!.querySelector(".run-duration");
    expect(duration("续跑耗时样例")).toHaveTextContent("3 分 0 秒");
    expect(duration("零耗时样例")).toHaveTextContent("0 ms");
    expect(duration("缺失耗时样例")).toHaveTextContent("未记录");
});

it("详情面板用边缘分隔条调整宽度，沿用宽度记忆，不再显示滑块", async () => {
    localStorage.setItem("gmb.inspector-width.v1", "520");
    setup([testJob("resize")], "/?run=resize");
    const handle = screen.getByRole("separator", { name: "调整详情面板宽度" });
    expect(screen.queryByText("面板宽度")).not.toBeInTheDocument();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(handle).toHaveAttribute("aria-valuenow", "520");
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(document.querySelector(".runs-layout")).toHaveStyle("--inspector-width: 536px");
    expect(localStorage.getItem("gmb.inspector-width.v1")).toBe("536");
    fireEvent.click(screen.getByRole("button", { name: "专注查看" }));
    await screen.findByRole("region", { name: "专注 run" });
    fireEvent.click(screen.getByRole("button", { name: "返回运行列表" }));
    expect(screen.getByRole("separator", { name: "调整详情面板宽度" })).toHaveAttribute("aria-valuenow", "536");
});

it("运行统计并入标题，仍可筛选队列与存档异常、进入并发设置", () => {
    setup([
        testJob("running", { name: "执行样例", archive_id: "running" }),
        testJob("queued", { name: "排队样例", archive_id: "queued", status: "queued" }),
        testJob("attention", { name: "异常样例", archive_id: "attention", checkpoint_error: "写入失败" }),
    ]);
    const summary = screen.getByRole("group", { name: "运行统计" });
    expect(summary.closest(".workspace-heading")).toContainElement(screen.getByRole("heading", { name: "运行中心" }));
    expect(within(summary).getByRole("button", { name: "执行中 2" })).toBeVisible();
    const queued = within(summary).getByRole("button", { name: "等待队列 1" });
    fireEvent.click(queued);
    expect(queued).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("筛选运行状态")).toHaveValue("queued");
    expect(screen.getByRole("button", { name: "排队样例" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "执行样例" })).not.toBeInTheDocument();
    fireEvent.click(within(summary).getByRole("button", { name: "存档异常 1" }));
    expect(screen.getByLabelText("筛选运行状态")).toHaveValue("attention");
    expect(screen.getByRole("button", { name: "异常样例" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "排队样例" })).not.toBeInTheDocument();
    fireEvent.click(within(summary).getByRole("button", { name: "调整并发设置" }));
    expect(screen.getByRole("heading", { name: "调度设置" })).toBeVisible();
    expect(screen.queryByRole("group", { name: "运行统计" })).not.toBeInTheDocument();
});

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

it("移除模板、批次和旧版入口，保留排行榜，字体授权收进设置", async () => {
    localStorage.setItem("gmb.batch.v1", JSON.stringify("removed-batch"));
    setup([testJob("visible")]);
    expect(screen.getByRole("button", { name: testJob("visible").name })).toBeVisible();
    expect(screen.queryByRole("button", { name: "模型模板" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "实验批次" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "筛选批次" })).not.toBeInTheDocument();
    expect(screen.queryByText("任务由本机服务独立执行")).not.toBeInTheDocument();
    expect(screen.queryByText("评测规则与运行管理分离")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "字体：MiSans" })).not.toBeInTheDocument();
    expect(document.querySelector('a[href^="/legacy"], a[href^="/replay"]')).toBeNull();
    expect(screen.getByRole("link", { name: /排行榜/ })).toHaveAttribute("href", "/leaderboard/");
    fireEvent.click(screen.getByRole("button", { name: "调度设置" }));
    await userEvent.click(screen.getByText("字体授权"));
    expect(screen.getByText(/本软件使用小米 MiSans/)).toBeVisible();
    expect(screen.getByRole("link", { name: "版权与许可证" })).toHaveAttribute("href", "/workbench/fonts/NOTICE.txt");
});

it("批量删除逐项清理关注、观战、曲线和列表缓存，不影响未选中的运行", async () => {
    localStorage.setItem("gmb.pins.v1", JSON.stringify(["a", "b", "other"]));
    localStorage.setItem("gmb.watch.v1", JSON.stringify(["a", "b", "other"]));
    localStorage.setItem("gmb.curves.v1", JSON.stringify(["a", "archive:archive-a", "b", "archive:archive-b", "other"]));
    const { client } = setup([
        testJob("a", { name: "批删 A", status: "completed", archive_id: "archive-a" }),
        testJob("b", { name: "批删 B", status: "completed", archive_id: "archive-b" }),
        testJob("other", { name: "保留运行", archive_id: "other-archive" }),
    ], "/?section=archives");
    await userEvent.click(await screen.findByLabelText("选择历史 批删 A"));
    await userEvent.click(screen.getByLabelText("选择历史 批删 B"));
    await userEvent.click(screen.getByRole("button", { name: "删除选中" }));
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.queryByText("批删 A")).not.toBeInTheDocument();
    expect(screen.queryByText("批删 B")).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("gmb.pins.v1")!)).toEqual(["other"]);
    expect(JSON.parse(localStorage.getItem("gmb.watch.v1")!)).toEqual(["other"]);
    expect(JSON.parse(localStorage.getItem("gmb.curves.v1")!)).toEqual(["other"]);
    expect(client.getQueryData<Snapshot>(["workbench"])!.jobs.map(job => job.id)).toEqual(["other"]);
});

it("删除存档后清理续跑身份、关注、观战、曲线和列表缓存", async () => {
    localStorage.setItem("gmb.pins.v1", JSON.stringify(["old", "new"]));
    localStorage.setItem("gmb.watch.v1", JSON.stringify(["old", "new"]));
    localStorage.setItem("gmb.curves.v1", JSON.stringify(["old", "archive:same-archive", "other"]));
    const { client } = setup([
        testJob("old", { status: "interrupted" }),
        testJob("new", { status: "completed", created_at: "2026-10-18T11:00:00Z" }),
    ], "/?section=archives&run=old");
    await userEvent.click(await screen.findByRole("button", { name: /的更多操作/ }));
    await userEvent.click(screen.getByRole("menuitem", { name: "删除存档" }));
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(await screen.findByText("没有匹配的历史 run")).toBeVisible();
    expect(JSON.parse(localStorage.getItem("gmb.pins.v1")!)).toEqual([]);
    expect(JSON.parse(localStorage.getItem("gmb.watch.v1")!)).toEqual([]);
    expect(JSON.parse(localStorage.getItem("gmb.curves.v1")!)).toEqual(["other"]);
    expect(client.getQueryData<Snapshot>(["workbench"])!.jobs).toEqual([]);
    expect(location.search).not.toContain("run=");
});

it("固定外观不提供密度切换，也不受此前浏览器密度选择影响", () => {
    localStorage.setItem("gmb.density.v1", JSON.stringify("compact"));
    setup([]);
    expect(screen.queryByRole("combobox", { name: "显示密度" })).not.toBeInTheDocument();
    expect(document.querySelector(".radix-themes")).not.toHaveAttribute("data-density");
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
    fireEvent.click(screen.getByText("运行失败，查看原始错误"));
    expect(screen.getByText("构造启动失败")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /的更多操作/ }));
    expect(screen.getByRole("menuitem", { name: "重新创建" })).toBeEnabled();
    await userEvent.keyboard("{Escape}");
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

it("续跑后 run 离开历史，运行中心显示最新任务，不恢复批次入口", async () => {
    const old = testJob("old", { status: "interrupted" });
    const { update } = setup([old]);
    fireEvent.click(screen.getByRole("button", { name: "历史存档" }));
    await userEvent.click(await screen.findByRole("button", { name: /的更多操作/ }));
    expect(screen.getByRole("menuitem", { name: "续跑" })).toBeEnabled();
    await userEvent.keyboard("{Escape}");
    update([old, testJob("resumed", { status: "queued", created_at: "2026-10-18T11:00:00Z", batch_id: "resume-batch", batch_name: "不应成为新实验" })]);
    await waitFor(() => expect(screen.queryByRole("button", { name: /的更多操作/ })).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /^运行中心/ }));
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "实验批次" })).not.toBeInTheDocument();
});
