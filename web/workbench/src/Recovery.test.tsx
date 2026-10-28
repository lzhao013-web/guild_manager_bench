import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Dialog, Theme } from "@radix-ui/themes";
import { expect, it, vi } from "vitest";
import { NewBatch, defaultDraft } from "./NewBatch";
import { expandDraft } from "./model";
import { buildRunCatalog } from "./runCatalog";
import { testJob } from "./test-jobs";

const token = "a".repeat(64);
const changed = (confirmation_token = token) => ({ index: 0, run_id: "original", from_turn: 3, max_rerun_turn: 8, confirmation_token,
    differences: [{ field: "protocol_hash", archived: "old-version", current: "new-version" }] });
const draft = () => ({ ...defaultDraft("default"), name: "分支测试", rerun_run_id: "original", rerun_from_turn: 3, rerun_max_turn: 8 });
function form() {
    const created = vi.fn();
    render(<Theme><Dialog.Root open><NewBatch presets={["default"]} initial={draft()} onCreated={created} /></Dialog.Root></Theme>);
    return created;
}

it("版本差异先展示确认，取消不会创建任务，再确认才发送精确版本令牌", async () => {
    const requests: { path: string; body: any }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (path, init) => {
        requests.push({ path: String(path), body: JSON.parse(String(init?.body)) });
        return new Response(JSON.stringify(String(path).endsWith("/check") ? { recoveries: [changed()] } : { jobs: [{ id: "child" }] }));
    });
    const created = form();
    expect(screen.getByLabelText("重跑起始回合")).toHaveValue(3);
    fireEvent.click(screen.getByRole("button", { name: "加入队列" }));
    const warning = await screen.findByRole("alertdialog", { name: "存档版本与当前版本不一致" });
    expect(warning).toHaveTextContent("old-version");
    expect(warning).toHaveTextContent("new-version");
    expect(warning).toHaveTextContent("从 T3 分支");
    expect(created).not.toHaveBeenCalled();
    expect(requests.map(request => request.path)).toEqual(["/api/workbench/batches/check"]);
    fireEvent.click(within(warning).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(requests).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "加入队列" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认风险，强行执行" }));
    await waitFor(() => expect(created).toHaveBeenCalledOnce());
    const sent = requests.at(-1)!;
    expect(sent.path).toBe("/api/workbench/batches");
    expect(sent.body.runs[0]).toMatchObject({ resume_run_id: null, rerun_run_id: "original", rerun_from_turn: 3, version_confirmation: token });
});

it("检查后版本再次变化会再次要求确认，不能静默沿用旧确认", async () => {
    let attempts = 0;
    const tokens: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (path, init) => {
        if (String(path).endsWith("/check")) return new Response(JSON.stringify({ recoveries: [changed()] }));
        const body = JSON.parse(String(init?.body));
        tokens.push(body.runs[0].version_confirmation);
        attempts++;
        return attempts === 1
            ? new Response(JSON.stringify({ detail: { code: "version_confirmation_required", message: "版本再次变化", recoveries: [changed("b".repeat(64))] } }), { status: 409 })
            : new Response(JSON.stringify({ jobs: [{ id: "child" }] }));
    });
    const created = form();
    fireEvent.click(screen.getByRole("button", { name: "加入队列" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认风险，强行执行" }));
    await waitFor(() => expect(tokens).toHaveLength(1));
    expect(created).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button", { name: "确认风险，强行执行" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "确认风险，强行执行" }));
    await waitFor(() => expect(created).toHaveBeenCalledOnce());
    expect(tokens).toEqual([token, "b".repeat(64)]);
});

it("分支不能批量展开，分支和原 run 保持独立身份", () => {
    expect(() => expandDraft({ ...draft(), repeats: 2 })).toThrow("一个任务");
    expect(() => expandDraft({ ...draft(), rerun_from_turn: 9 })).toThrow("可恢复范围");
    const parent = testJob("parent", { archive_id: "original" });
    const branch = testJob("branch", { archive_id: "child", config: { ...parent.config, resume_run_id: null, rerun_run_id: "original", rerun_from_turn: 3 } });
    expect(buildRunCatalog([parent, branch]).runs).toHaveLength(2);
});
