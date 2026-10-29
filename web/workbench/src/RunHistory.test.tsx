import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Theme } from "@radix-ui/themes";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { RunHistory } from "./RunHistory";
import { buildRunCatalog } from "./runCatalog";
import { testArchive, testJob } from "./test-jobs";
import type { Archive, Job } from "./types";

function setup(jobs: Job[], archives: Archive[]) {
    const callbacks = { onRerun: vi.fn(), onResume: vi.fn(), onOpen: vi.fn(), onClone: vi.fn(), onWatch: vi.fn(), onCompare: vi.fn(), onDeleted: vi.fn() };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, refetchInterval: false } } });
    client.setQueryData(["archives"], { runs: archives });
    function Harness({ items }: { items: Job[] }) {
        return <QueryClientProvider client={client}><Theme>
            <RunHistory jobs={items} runKeys={buildRunCatalog(items).keys} {...callbacks} />
        </Theme></QueryClientProvider>;
    }
    const view = render(<Harness items={jobs} />);
    return { ...callbacks, client, updateJobs: (items: Job[]) => view.rerender(<Harness items={items} />) };
}

it("历史页只保留版本事实标记，不显示比较限制横幅", async () => {
    setup([], [testArchive("mixed-a", { model: "跨版 A", protocol_status: "mixed" }),
        testArchive("mixed-b", { model: "跨版 B", protocol_status: "mixed" }), testArchive("normal", { model: "普通记录" })]);
    expect(screen.getAllByText("跨版本", { exact: true })).toHaveLength(2);
    expect(screen.queryByText(/比较限制|同版本标准评测比较/)).not.toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: "搜索存档" }), "普通记录");
    expect(screen.queryByText(/跨版本运行 · 比较限制/)).not.toBeInTheDocument();
    expect(screen.queryByText("跨版本", { exact: true })).not.toBeInTheDocument();
});

it("操作列表头和各行使用固定列，排序后按钮仍打开各自的记录", async () => {
    const job = testJob("managed-actions", { name: "A 受控记录", status: "completed", archive_id: null });
    const archive = testArchive("cli-actions", { model: "B 命令行存档", status: "completed" });
    const callbacks = setup([job], [archive]);
    expect(screen.getByRole("columnheader", { name: "操作" })).toHaveClass("history-actions-column");
    for (const direction of ["ascending", "descending"]) {
        await userEvent.click(screen.getByRole("button", { name: "Run / 模型" }));
        expect(screen.getByRole("columnheader", { name: "Run / 模型" })).toHaveAttribute("aria-sort", direction);
        const managedRow = screen.getByRole("row", { name: /A 受控记录/ });
        const cliRow = screen.getByRole("row", { name: /B 命令行存档/ });
        const detail = within(managedRow).getByRole("button", { name: "查看详情" });
        const curve = within(cliRow).getByRole("button", { name: "查看曲线" });
        for (const button of [detail, curve]) expect(button.closest("td")).toHaveClass("history-actions-column");
        await userEvent.click(detail);
        expect(callbacks.onOpen).toHaveBeenLastCalledWith(job);
        await userEvent.click(curve);
        expect(callbacks.onCompare).toHaveBeenLastCalledWith(["archive:cli-actions"]);
        await userEvent.click(within(managedRow).getByRole("button", { name: "A 受控记录 的更多操作" }));
        expect(screen.getByRole("menuitem", { name: "查看曲线" })).toBeVisible();
        await userEvent.keyboard("{Escape}");
    }
});

it("已完成存档可从指定回合重跑，但不提供原地续跑", async () => {
    const archive = testArchive("completed-parent", { model: "已完成来源", status: "completed" });
    const callbacks = setup([], [archive]);
    await userEvent.click(screen.getByRole("button", { name: /的更多操作/ }));
    expect(screen.queryByRole("menuitem", { name: "续跑" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: "从指定回合重跑" }));
    expect(callbacks.onRerun).toHaveBeenCalledWith(archive);
});

it("历史列表只保留主入口，失败摘要不铺原始错误，更多菜单保留原操作", async () => {
    const failure = 'HTTP 408: {"error":{"message":"stream disconnected before completion"}}';
    const job = testJob("failed", { name: "中断模型", status: "failed", error: failure });
    const archive = testArchive();
    const callbacks = setup([job], [archive]);
    const row = screen.getByRole("row", { name: /中断模型/ });
    expect(within(row).getByRole("button", { name: "查看详情" })).toBeVisible();
    expect(within(row).queryByRole("button", { name: "从指定回合重跑" })).not.toBeInTheDocument();
    const raw = within(row).getByText(failure);
    expect(raw).not.toBeVisible();
    await userEvent.click(within(row).getByText("模型响应中断"));
    expect(raw).toBeVisible();
    await userEvent.click(within(row).getByRole("button", { name: "中断模型 的更多操作" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "查看曲线" }));
    expect(callbacks.onCompare).toHaveBeenCalledWith([job.id]);
    await userEvent.click(within(row).getByRole("button", { name: /的更多操作/ }));
    await userEvent.click(screen.getByRole("menuitem", { name: "续跑" }));
    expect(callbacks.onResume).toHaveBeenCalledWith(archive);
});

it("历史显示模型累计耗时、Token总量和输入输出，缺失值不当作零", () => {
    const managed = testJob("managed", { name: "Managed metrics", status: "completed", model_duration_ms: 90500,
        usage: { input_tokens: 1000, output_tokens: 200, reported: 1, responses: 1 } });
    setup([managed], [testArchive("cli", { model: "CLI metrics", model_duration_ms: 0,
        usage: { input_tokens: 0, output_tokens: 0, reported: 1, responses: 1 } }),
    testArchive("old", { model: "Old metrics" }),
    testArchive("partial", { model: "Partial metrics", usage: { input_tokens: 900, output_tokens: null, reported: 1, responses: 1 } })]);
    const managedRow = screen.getByRole("row", { name: /Managed metrics/ });
    expect(within(managedRow).getByText("1 分 30 秒")).toBeVisible();
    expect(within(managedRow).getByText("1,200")).toBeVisible();
    expect(within(managedRow).getByText("输入 1,000")).toBeVisible();
    expect(within(managedRow).getByText("输出 200")).toBeVisible();
    const cliRow = screen.getByRole("row", { name: /CLI metrics/ });
    expect(within(cliRow).getByText("0 ms")).toBeVisible();
    expect(within(cliRow).getByText("0")).toBeVisible();
    const oldRow = screen.getByRole("row", { name: /Old metrics/ });
    expect(within(oldRow).getAllByText("未记录")).toHaveLength(2);
    expect(within(oldRow).getByText("输入 未记录")).toBeVisible();
    const partialRow = screen.getByRole("row", { name: /Partial metrics/ });
    expect(within(partialRow).getByText("输入 900")).toBeVisible();
    expect(within(partialRow).getByText("输出 未记录")).toBeVisible();
    expect(within(partialRow).getAllByText("未记录")).toHaveLength(2);
});

it("耗时和Token表头按数值双向排序，缺失值始终置后", () => {
    setup([], [
        testArchive("slow", { model: "Slow", model_duration_ms: 100000, usage: { input_tokens: 900, output_tokens: 100, reported: 1, responses: 1 } }),
        testArchive("fast", { model: "Fast", model_duration_ms: 1000, usage: { input_tokens: 1000, output_tokens: 2000, reported: 1, responses: 1 } }),
        testArchive("zero", { model: "Zero", model_duration_ms: 0, usage: { input_tokens: 0, output_tokens: 0, reported: 1, responses: 1 } }),
        testArchive("missing", { model: "Missing" }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: "耗时" }));
    expect(rowNames()).toEqual(["Slow", "Fast", "Zero", "Missing"]);
    fireEvent.click(screen.getByRole("button", { name: "耗时" }));
    expect(rowNames()).toEqual(["Zero", "Fast", "Slow", "Missing"]);
    fireEvent.click(screen.getByRole("button", { name: "Token" }));
    expect(rowNames()).toEqual(["Fast", "Slow", "Zero", "Missing"]);
    fireEvent.click(screen.getByRole("button", { name: "Token" }));
    expect(rowNames()).toEqual(["Zero", "Slow", "Fast", "Missing"]);
});

it("CLI 主入口使用曲线，删除需二次确认，取消不发请求", async () => {
    const archive = testArchive("cli", { status: "completed", model: "待删CLI" });
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (path, init) => new Response(JSON.stringify(
        init?.method === "DELETE" ? { deleted: true, run_id: "cli", deleted_job_ids: [] } : { runs: [] })));
    const callbacks = setup([], [archive]);
    expect(document.querySelector('a[href^="/replay"]')).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "查看曲线" }));
    expect(callbacks.onCompare).toHaveBeenCalledWith(["archive:cli"]);
    await userEvent.click(screen.getByRole("button", { name: /的更多操作/ }));
    await userEvent.click(screen.getByRole("menuitem", { name: "删除存档" }));
    const dialog = screen.getByRole("alertdialog", { name: "删除存档？" });
    expect(dialog).toHaveTextContent("无法恢复");
    expect(fetch).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(fetch).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /的更多操作/ }));
    await userEvent.click(screen.getByRole("menuitem", { name: "删除存档" }));
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(callbacks.onDeleted).toHaveBeenCalledWith({ deleted: true, run_id: "cli", deleted_job_ids: [] }));
    expect(fetch.mock.calls[0]).toEqual(["/api/workbench/archives/cli", expect.objectContaining({ method: "DELETE" })]);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

it("未归档的失败记录可删除，服务端拒绝时保留记录并显示错误", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (_path, init) => new Response(JSON.stringify(
        init?.method === "DELETE" ? { detail: "任务仍被占用" } : { runs: [] }), { status: init?.method === "DELETE" ? 409 : 200 }));
    const callbacks = setup([testJob("failed", { name: "失败记录", archive_id: null, status: "failed" })], []);
    await userEvent.click(screen.getByRole("button", { name: /的更多操作/ }));
    await userEvent.click(screen.getByRole("menuitem", { name: "删除记录" }));
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("任务仍被占用");
    expect(fetch.mock.calls[0][0]).toBe("/api/workbench/jobs/failed");
    expect(callbacks.onDeleted).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认删除" })).toBeEnabled();
});

it("有排队分支引用时禁用源存档删除", async () => {
    const waiting = testJob("queued-child", { status: "queued", archive_id: null,
        config: { ...testJob("config").config, rerun_run_id: "source" } });
    setup([waiting], [testArchive("source", { status: "completed", model: "来源" })]);
    await userEvent.click(screen.getByRole("button", { name: /的更多操作/ }));
    expect(screen.getByRole("menuitem", { name: "删除存档" })).toHaveAttribute("aria-disabled", "true");
});

it("多选受控存档、CLI和失败记录，统一确认且不受四路观战上限限制", async () => {
    const jobs = [testJob("managed-job", { name: "受控存档", status: "completed", archive_id: "managed-archive" }),
        testJob("failed-job", { name: "失败记录", status: "failed", archive_id: null })];
    const archives = [testArchive("managed-archive", { status: "completed" }),
        ...Array.from({ length: 4 }, (_, i) => testArchive(`cli-${i}`, { status: "completed", model: `CLI ${i}` }))];
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (path, init) => {
        if (init?.method !== "DELETE") return new Response(JSON.stringify({ runs: [] }));
        requests.push(String(path));
        const id = String(path).split("/").at(-1)!;
        return new Response(JSON.stringify({ deleted: true, run_id: id === "managed-job" ? "managed-archive" : id === "failed-job" ? null : id,
            deleted_job_ids: String(path).includes("/jobs/") ? [id] : [] }));
    });
    const callbacks = setup(jobs, archives);
    await userEvent.click(screen.getByLabelText("选择当前筛选的全部历史 run"));
    await userEvent.click(screen.getByRole("button", { name: "删除选中" }));
    const dialog = screen.getByRole("alertdialog", { name: "删除选中的 6 项？" });
    expect(within(dialog).getByRole("list", { name: "待删除项" }).children).toHaveLength(6);
    expect(dialog).toHaveTextContent("无法恢复");
    expect(requests).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(requests).toEqual([]);
    expect(screen.getByLabelText("选择当前筛选的全部历史 run")).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "删除选中" }));
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(callbacks.onDeleted).toHaveBeenCalledTimes(6));
    expect(new Set(requests)).toEqual(new Set(["/api/workbench/jobs/managed-job", "/api/workbench/jobs/failed-job",
        ...Array.from({ length: 4 }, (_, i) => `/api/workbench/archives/cli-${i}`)]));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除选中" })).not.toBeInTheDocument();
});

it("批量删除只处理当前筛选中选中的项，不删除隐藏的选择", async () => {
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (path, init) => {
        if (init?.method !== "DELETE") return new Response(JSON.stringify({ runs: [] }));
        requests.push(String(path));
        return new Response(JSON.stringify({ deleted: true, run_id: "visible", deleted_job_ids: [] }));
    });
    setup([testJob("hidden", { name: "隐藏记录", status: "failed", archive_id: null })],
        [testArchive("visible", { model: "可见CLI", preset: "advanced", status: "completed" })]);
    await userEvent.click(screen.getByLabelText("选择当前筛选的全部历史 run"));
    fireEvent.change(screen.getByLabelText("筛选历史 preset"), { target: { value: "advanced" } });
    await userEvent.click(screen.getByRole("button", { name: "删除选中" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("可见CLI");
    expect(screen.getByRole("alertdialog")).not.toHaveTextContent("隐藏记录");
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(requests).toEqual(["/api/workbench/archives/visible"]);
    fireEvent.change(screen.getByLabelText("筛选历史 preset"), { target: { value: "all" } });
    expect(screen.getByLabelText("选择历史 隐藏记录")).toBeChecked();
});

it("部分失败保留失败选择和原因，重试不重复删除成功项", async () => {
    let remaining = ["a", "b", "c"].map(id => testArchive(id, { model: `${id.toUpperCase()} CLI`, status: "completed" }));
    let failB = true;
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (path, init) => {
        if (init?.method !== "DELETE") return new Response(JSON.stringify({ runs: remaining }));
        const id = String(path).split("/").at(-1)!;
        requests.push(id);
        if (id === "b" && failB) return new Response(JSON.stringify({ detail: "存档仍被占用" }), { status: 409 });
        remaining = remaining.filter(row => row.run_id !== id);
        return new Response(JSON.stringify({ deleted: true, run_id: id, deleted_job_ids: [] }));
    });
    const callbacks = setup([], remaining);
    await userEvent.click(screen.getByLabelText("选择当前筛选的全部历史 run"));
    await userEvent.click(screen.getByRole("button", { name: "删除选中" }));
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("已删除 2 项，1 项请求失败");
    expect(alert).toHaveTextContent("B CLI：存档仍被占用");
    expect(screen.getByRole("alertdialog", { name: "删除剩余 1 项？" })).toBeVisible();
    expect(callbacks.onDeleted).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.queryByLabelText("选择历史 A CLI")).not.toBeInTheDocument());
    expect(screen.getByLabelText("选择历史 B CLI")).toBeChecked();
    failB = false;
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(requests.filter(id => id === "a")).toHaveLength(1);
    expect(requests.filter(id => id === "c")).toHaveLength(1);
    expect(requests.filter(id => id === "b")).toHaveLength(2);
    expect(callbacks.onDeleted).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("status")).toHaveTextContent("已删除 3 项");
});

it("删除逐项执行并显示进度，执行期间取消和Escape不会关闭确认框", async () => {
    const pending: { resolve: (response: Response) => void; runId: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (path, init) => init?.method === "DELETE"
        ? new Promise<Response>(resolve => pending.push({ resolve, runId: String(path).split("/").at(-1)! })) : new Response(JSON.stringify({ runs: [] })));
    const callbacks = setup([], [testArchive("a", { model: "A", status: "completed" }), testArchive("b", { model: "B", status: "completed" })]);
    await userEvent.click(screen.getByLabelText("选择当前筛选的全部历史 run"));
    await userEvent.click(screen.getByRole("button", { name: "删除选中" }));
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByRole("button", { name: "取消" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "确认删除" })).toBeDisabled();
    expect(within(dialog).getByRole("status")).toHaveTextContent("已处理 0 / 2 项");
    await userEvent.keyboard("{Escape}");
    expect(dialog).toBeVisible();
    expect(pending).toHaveLength(1);
    await act(async () => pending[0].resolve(new Response(JSON.stringify({ deleted: true, run_id: pending[0].runId, deleted_job_ids: [] }))));
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(within(dialog).getByRole("status")).toHaveTextContent("已处理 1 / 2 项");
    await act(async () => pending[1].resolve(new Response(JSON.stringify({ deleted: true, run_id: pending[1].runId, deleted_job_ids: [] }))));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(callbacks.onDeleted).toHaveBeenCalledTimes(2);
});

it("批量选择含占用项时禁用删除，取消勾选后可继续", async () => {
    const queued = testJob("queued-child", { status: "queued", archive_id: null,
        config: { ...testJob("config").config, rerun_run_id: "source" } });
    setup([queued], [testArchive("source", { model: "被占用来源", status: "completed" }),
        testArchive("free", { model: "空闲存档", status: "completed" })]);
    await userEvent.click(screen.getByLabelText("选择当前筛选的全部历史 run"));
    expect(screen.getByRole("button", { name: "删除选中" })).toBeDisabled();
    expect(screen.getByText("1 项占用中")).toBeVisible();
    await userEvent.click(screen.getByLabelText("选择历史 被占用来源"));
    expect(screen.getByRole("button", { name: "删除选中" })).toBeEnabled();
});

it("确认期间新增的排队引用会阻止删除，不发送请求", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const { updateJobs } = setup([], [testArchive("source", { model: "来源", status: "completed" }),
        testArchive("other", { model: "另一存档", status: "completed" })]);
    await userEvent.click(screen.getByLabelText("选择当前筛选的全部历史 run"));
    await userEvent.click(screen.getByRole("button", { name: "删除选中" }));
    updateJobs([testJob("queued-child", { status: "queued", archive_id: null,
        config: { ...testJob("config").config, rerun_run_id: "source" } })]);
    expect(screen.getByRole("button", { name: "确认删除" })).toBeDisabled();
    expect(screen.getByText("部分存档仍在使用中，请先停止对应任务。")).toBeVisible();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(0);
});

function rowNames() {
    return screen.queryAllByRole("row").slice(1).map(row => within(row).getByRole("strong").textContent);
}

it("preset 下拉来自受控与 CLI 历史，支持自定义和未记录项，并与搜索、状态组合", () => {
    const full = testJob("full", { name: "Alpha full", status: "completed", archive_id: null, config: { ...testJob("config").config, preset: "full" } });
    const advanced = testJob("advanced", { name: "Alpha advanced", status: "failed", archive_id: null, batch_id: "advanced-batch", batch_name: "高级批次", config: { ...full.config, preset: "advanced" } });
    setup([full, advanced], [testArchive("cli", { model: "Beta CLI", preset: "advanced", status: "completed" }),
        testArchive("custom", { model: "Custom CLI", preset: "custom-preset" }), testArchive("unknown", { model: "Old CLI", preset: "" })]);
    const preset = screen.getByRole("combobox", { name: "筛选历史 preset" });
    expect(within(preset).getByRole("option", { name: "custom-preset" })).toBeInTheDocument();
    fireEvent.change(preset, { target: { value: "advanced" } });
    expect(rowNames().sort()).toEqual(["Alpha advanced", "Beta CLI"]);
    expect(screen.getByRole("status")).toHaveTextContent("显示 2 / 5 个历史 run");
    fireEvent.change(screen.getByRole("combobox", { name: "筛选历史状态" }), { target: { value: "failed" } });
    expect(rowNames()).toEqual(["Alpha advanced"]);
    fireEvent.change(screen.getByRole("textbox", { name: "搜索存档" }), { target: { value: "Beta" } });
    expect(screen.getByText("没有匹配的历史运行")).toBeVisible();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索存档" }), { target: { value: "Alpha" } });
    expect(rowNames()).toEqual(["Alpha advanced"]);
    expect(screen.queryByRole("combobox", { name: "筛选历史批次" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索存档" }), { target: { value: "" } });
    fireEvent.change(screen.getByRole("combobox", { name: "筛选历史状态" }), { target: { value: "all" } });
    fireEvent.change(preset, { target: { value: "" } });
    expect(rowNames()).toEqual(["Old CLI"]);
});

it("历史默认按创建时间降序，评分双向排序保留零分并把缺失评分放最后", () => {
    const older = testJob("older", { name: "Older zero", archive_id: null, status: "completed", last_score: 0 });
    const newer = testArchive("newer", { model: "Newer high", created_at: "2026-10-19T10:00:00Z", rank_score: 100 });
    const unknown = testJob("unknown", { name: "Unknown score", archive_id: null, status: "failed", created_at: "2026-10-20T10:00:00Z", last_score: null });
    setup([older, unknown], [newer]);
    expect(rowNames()).toEqual(["Unknown score", "Newer high", "Older zero"]);
    expect(screen.queryByRole("combobox", { name: "历史排序" })).not.toBeInTheDocument();
    const created = screen.getByRole("columnheader", { name: "创建时间" });
    expect(created).toHaveAttribute("aria-sort", "descending");
    const score = screen.getByRole("button", { name: "Rank Score" });
    fireEvent.click(score);
    expect(rowNames()).toEqual(["Newer high", "Older zero", "Unknown score"]);
    expect(screen.getByRole("columnheader", { name: "Rank Score" })).toHaveAttribute("aria-sort", "descending");
    expect(created).not.toHaveAttribute("aria-sort");
    fireEvent.click(score);
    expect(rowNames()).toEqual(["Older zero", "Newer high", "Unknown score"]);
    expect(screen.getByRole("columnheader", { name: "Rank Score" })).toHaveAttribute("aria-sort", "ascending");
    fireEvent.click(screen.getByRole("button", { name: "创建时间" }));
    expect(created).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(screen.getByRole("button", { name: "创建时间" }));
    expect(rowNames()).toEqual(["Older zero", "Newer high", "Unknown score"]);
    expect(created).toHaveAttribute("aria-sort", "ascending");
});

it("排序不会改变选择身份，筛选后的对比使用当前可见顺序和正确的 run ID", () => {
    const job = testJob("managed", { name: "Managed", status: "completed", archive_id: null, last_score: 100 });
    const { onCompare } = setup([job], [testArchive("cli", { model: "CLI", rank_score: 0 })]);
    fireEvent.click(screen.getByRole("checkbox", { name: "选择历史 Managed" }));
    fireEvent.click(screen.getByRole("button", { name: "Rank Score" }));
    fireEvent.click(screen.getByRole("button", { name: "Rank Score" }));
    expect(screen.getByRole("checkbox", { name: "选择历史 Managed" })).toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: "选择历史 CLI" }));
    fireEvent.click(screen.getByRole("button", { name: "对比选中曲线" }));
    expect(onCompare).toHaveBeenLastCalledWith(["archive:cli", "managed"]);
});

it("表头可用 Enter 和空格排序，切换字段使用该字段的默认方向", async () => {
    const user = userEvent.setup();
    setup([], [testArchive("a", { model: "Alpha", turns: 0 }), testArchive("b", { model: "Beta", turns: 9 })]);
    const progress = screen.getByRole("button", { name: "进度" });
    progress.focus();
    await user.keyboard("{Enter}");
    expect(rowNames()).toEqual(["Beta", "Alpha"]);
    expect(screen.getByRole("columnheader", { name: "进度" })).toHaveAttribute("aria-sort", "descending");
    await user.keyboard(" ");
    expect(rowNames()).toEqual(["Alpha", "Beta"]);
    await user.click(screen.getByRole("button", { name: "Run / 模型" }));
    expect(screen.getByRole("columnheader", { name: "Run / 模型" })).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getByRole("columnheader", { name: "进度" })).not.toHaveAttribute("aria-sort");
});

it("续跑把最后一个匹配 run 移出历史后，preset 控件仍保留用户选中的值", () => {
    const done = testJob("done", { status: "completed", archive_id: null, config: { ...testJob("config").config, preset: "full" } });
    const { updateJobs, client } = setup([done], []);
    fireEvent.change(screen.getByRole("combobox", { name: "筛选历史 preset" }), { target: { value: "full" } });
    updateJobs([{ ...done, status: "queued" }]);
    act(() => client.setQueryData(["archives"], { runs: [] }));
    expect(screen.getByRole("combobox", { name: "筛选历史 preset" })).toHaveValue("full");
    expect(screen.getByRole("status")).toHaveTextContent("显示 0 / 0 个历史 run");
    expect(screen.getByText("没有匹配的历史运行")).toBeVisible();
});
