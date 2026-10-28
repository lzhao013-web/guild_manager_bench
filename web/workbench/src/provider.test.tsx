import { Dialog, Theme } from "@radix-ui/themes";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { defaultDraft, NewBatch } from "./NewBatch";

it.each([true, false])(
    "保留 Anthropic 思考的显式设置：%s",
    async (thinking) => {
        const draft = defaultDraft("default");
        draft.name = "Anthropic 界面配置测试";
        draft.rows[0].provider = "anthropic";
        const created = vi.fn();
        const fetch = vi
            .spyOn(globalThis, "fetch")
            .mockResolvedValue(
                new Response(JSON.stringify({ jobs: [] }), { status: 201 }),
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
        fireEvent.change(
            screen.getByRole("combobox", { name: "Anthropic 思考" }),
            { target: { value: String(thinking) } },
        );
        fireEvent.click(screen.getByRole("button", { name: "加入队列" }));
        await waitFor(() => expect(created).toHaveBeenCalledOnce());
        expect(
            JSON.parse(fetch.mock.calls[0][1]!.body as string).runs[0].thinking,
        ).toBe(thinking);
    },
);

it("续跑更换接口后可以修正推理强度，不会被隐藏字段困住", async () => {
    const draft = {
        ...defaultDraft("default"),
        name: "续跑配置测试",
        resume_run_id: "previous",
        efforts: "max",
    };
    draft.rows[0].provider = "google";
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
    expect(screen.getByRole("button", { name: "加入队列" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("推理强度", { exact: false }), {
        target: { value: "high" },
    });
    expect(screen.getByRole("button", { name: "加入队列" })).toBeEnabled();
    expect(screen.getByText("1 RUNS")).toBeVisible();
});
