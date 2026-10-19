import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Dialog, Theme } from "@radix-ui/themes";
import { expect, it, vi } from "vitest";
import { defaultDraft, NewBatch } from "./NewBatch";

it("启动前显示完整任务数，保存模板不含密钥，提交发送真实参数", async () => {
    const draft = {
        ...defaultDraft("default"),
        name: "构造的界面测试",
        efforts: "medium,high",
        seeds: "0,42",
    };
    draft.rows[0].model = "test-model";
    draft.rows[0].api_key = "never-persist-this";
    const created = vi.fn(),
        saved = vi.fn();
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
                    onSave={saved}
                />
            </Dialog.Root>
        </Theme>,
    );
    expect(screen.getByText("4 RUNS")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "保存为模板" }));
    expect(JSON.stringify(saved.mock.calls)).not.toContain(
        "never-persist-this",
    );
    fireEvent.click(screen.getByRole("button", { name: "加入队列" }));
    await waitFor(() => expect(created).toHaveBeenCalledOnce());
    const body = JSON.parse(fetch.mock.calls[0][1]!.body as string);
    expect(body.runs).toHaveLength(4);
    expect(body.runs[0].game_seed).toBe(0);
    expect(body.runs[0].api_key).toBe("never-persist-this");
});

it("提交失败后保留配置并明确展示错误", async () => {
    const draft = { ...defaultDraft("default"), name: "失败测试" };
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
                    onSave={vi.fn()}
                />
            </Dialog.Root>
        </Theme>,
    );
    fireEvent.click(screen.getByRole("button", { name: "加入队列" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
        "找不到模型配置",
    );
    expect(screen.getByDisplayValue("失败测试")).toBeVisible();
    expect(screen.getByRole("button", { name: "加入队列" })).toBeEnabled();
});
