import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Theme } from "@radix-ui/themes";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { RunHistory } from "./RunHistory";
import { testArchive, testJob } from "./test-jobs";
import type { Archive, Job } from "./types";

function setup(jobs: Job[], archives: Archive[]) {
    const callbacks = { onResume: vi.fn(), onOpen: vi.fn(), onClone: vi.fn(), onWatch: vi.fn(), onCompare: vi.fn() };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, refetchInterval: false } } });
    client.setQueryData(["archives"], { runs: archives });
    function Harness({ items }: { items: Job[] }) {
        const [batchId, setBatchId] = useState("all");
        return <QueryClientProvider client={client}><Theme>
            <RunHistory jobs={items} batchId={batchId} onBatchChange={setBatchId} {...callbacks} />
        </Theme></QueryClientProvider>;
    }
    const view = render(<Harness items={jobs} />);
    return { ...callbacks, client, updateJobs: (items: Job[]) => view.rerender(<Harness items={items} />) };
}

function rowNames() {
    return screen.queryAllByRole("row").slice(1).map(row => within(row).getByRole("strong").textContent);
}

it("preset 下拉来自受控与 CLI 历史，支持自定义和未记录项，并与搜索、状态、批次组合", () => {
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
    expect(screen.getByText("没有匹配的历史 run")).toBeVisible();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索存档" }), { target: { value: "Alpha" } });
    fireEvent.change(screen.getByRole("combobox", { name: "筛选历史批次" }), { target: { value: full.batch_id } });
    expect(rowNames()).toEqual([]);
    fireEvent.change(screen.getByRole("combobox", { name: "筛选历史批次" }), { target: { value: "advanced-batch" } });
    expect(rowNames()).toEqual(["Alpha advanced"]);
    fireEvent.change(screen.getByRole("textbox", { name: "搜索存档" }), { target: { value: "" } });
    fireEvent.change(screen.getByRole("combobox", { name: "筛选历史批次" }), { target: { value: "all" } });
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
    expect(screen.getByText("没有匹配的历史 run")).toBeVisible();
});
