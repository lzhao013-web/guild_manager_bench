import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Dialog, Theme } from "@radix-ui/themes";
import { expect, it, vi } from "vitest";
import { defaultDraft, NewBatch } from "./NewBatch";

it.each([undefined, 180, 900])("默认超时600秒，明确配置的 %s 不会被覆盖", timeout => {
    render(<Theme><Dialog.Root open>
        <NewBatch presets={["default"]} initial={timeout === undefined ? undefined : { ...defaultDraft("default"), timeout }} onCreated={vi.fn()} />
    </Dialog.Root></Theme>);
    expect(screen.getByLabelText("请求超时（秒）")).toHaveValue(timeout ?? 600);
});

it("启动前显示完整任务数，不提供模板或批次字段，提交发送真实参数", async () => {
    const draft = {
        ...defaultDraft("default"),
        name: "构造的界面测试",
        efforts: "medium,high",
        seeds: "0,42",
    };
    draft.rows[0].model = "test-model";
    draft.rows[0].api_key = "never-persist-this";
    const created = vi.fn();
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify({ jobs: [{ id: "new-job" }] }), {
            status: 201,
        }),
    );
    render(
        <Theme>
            <Dialog.Root open>
                <NewBatch
                    presets={["default"]}
                    initial={draft}
                    onCreated={created}
                />
            </Dialog.Root>
        </Theme>,
    );
    expect(screen.getByText("4 RUNS")).toBeVisible();
    expect(screen.queryByRole("button", { name: "保存为模板" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("批次名称")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "加入队列" }));
    await waitFor(() => expect(created).toHaveBeenCalledOnce());
    const body = JSON.parse(fetch.mock.calls[0][1]!.body as string);
    expect(body.runs).toHaveLength(4);
    expect(body.runs[0].game_seed).toBe(0);
    expect(body.runs[0].api_key).toBe("never-persist-this");
    expect(body.runs.every((run: { timeout: number }) => run.timeout === 600)).toBe(true);
});

it("提交失败后保留配置并明确展示错误", async () => {
    const draft = { ...defaultDraft("default"), seeds: "42" };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify({ detail: "找不到模型配置" }), {
            status: 422,
        }),
    );
    render(
        <Theme>
            <Dialog.Root open>
                <NewBatch
                    presets={["default"]}
                    initial={draft}
                    onCreated={vi.fn()}
                />
            </Dialog.Root>
        </Theme>,
    );
    fireEvent.click(screen.getByRole("button", { name: "加入队列" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
        "找不到模型配置",
    );
    expect(screen.getByDisplayValue("42")).toBeVisible();
    expect(screen.getByRole("button", { name: "加入队列" })).toBeEnabled();
});
