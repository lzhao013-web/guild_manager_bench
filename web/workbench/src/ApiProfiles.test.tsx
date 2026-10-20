import { useState } from "react";
import { Dialog, Theme } from "@radix-ui/themes";
import {
    act,
    fireEvent,
    render,
    screen,
    waitFor,
} from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { NewBatch, defaultDraft } from "./NewBatch";
import { ModelPicker } from "./ModelPicker";
import { ApiProfilesPanel } from "./ApiProfiles";
import { safeTemplate } from "./model";
import type { ApiProfile } from "./types";

const profile: ApiProfile = {
    id: "saved-api",
    name: "本地测试连接",
    provider: "openai",
    base_url: "https://example.test/v1",
    default_model: "history-model",
    known_models: ["history-model"],
    credential_present: true,
    source: "command_history",
    created_at: "2026-10-18T00:00:00Z",
    updated_at: "2026-10-18T00:00:00Z",
};
const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status });

it("选择保存的 API、从模型目录选择，再提交凭证引用而不是密钥", async () => {
    const created = vi.fn();
    const fetch = vi
        .spyOn(globalThis, "fetch")
        .mockImplementation(async (url) =>
            String(url).endsWith("/models")
                ? json({
                      models: [{ id: "api-discovered", name: "API 模型" }],
                      truncated: false,
                  })
                : json({ jobs: [{ id: "new-job" }] }, 201),
        );
    render(
        <Theme>
            <Dialog.Root open>
                <NewBatch
                    presets={["default"]}
                    initial={{
                        ...defaultDraft("default"),
                        name: "保存配置界面测试",
                    }}
                    profiles={[profile]}
                    onCreated={created}
                    onSave={vi.fn()}
                />
            </Dialog.Root>
        </Theme>,
    );
    fireEvent.change(screen.getByRole("combobox", { name: "API 配置" }), {
        target: { value: profile.id },
    });
    expect(
        screen.queryByLabelText("API Key", { exact: true }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("模型名称", { exact: true })).toHaveValue(
        "history-model",
    );
    fireEvent.click(screen.getByRole("button", { name: "获取模型" }));
    expect(await screen.findByText(/API 返回 1 个模型/)).toBeVisible();
    expect(
        document.querySelector('option[value="api-discovered"]'),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("模型名称", { exact: true }), {
        target: { value: "api-discovered" },
    });
    fireEvent.click(screen.getByRole("button", { name: "加入队列" }));
    await waitFor(() => expect(created).toHaveBeenCalledOnce());
    const submitted = fetch.mock.calls.find(
        (call) => call[0] === "/api/workbench/batches",
    )!;
    const body = JSON.parse(submitted[1]!.body as string);
    expect(body.runs[0].profile_id).toBe(profile.id);
    expect(body.runs[0].model).toBe("api-discovered");
    expect(body.runs[0].base_url).toBeNull();
    expect(body.runs[0]).not.toHaveProperty("api_key");
    expect(fetch.mock.calls[0][0]).toBe(
        "/api/workbench/api-profiles/saved-api/models",
    );
});

it("自定义连接保存后立即可选，清除浏览器表单中的密钥", async () => {
    const saved = vi.fn();
    const draft = defaultDraft("default");
    draft.name = "保存连接";
    draft.rows[0].model = "history-model";
    draft.rows[0].base_url = profile.base_url;
    draft.rows[0].api_key = "typed-secret";
    const fetch = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(json(profile, 201));
    render(
        <Theme>
            <Dialog.Root open>
                <NewBatch
                    presets={["default"]}
                    initial={draft}
                    onCreated={vi.fn()}
                    onSave={vi.fn()}
                    onProfileSaved={saved}
                />
            </Dialog.Root>
        </Theme>,
    );
    fireEvent.click(screen.getByRole("button", { name: "保存此 API 配置" }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(profile));
    expect(screen.getByRole("combobox", { name: "API 配置" })).toHaveValue(
        profile.id,
    );
    expect(screen.queryByDisplayValue("typed-secret")).not.toBeInTheDocument();
    expect(JSON.parse(fetch.mock.calls[0][1]!.body as string).api_key).toBe(
        "typed-secret",
    );
});

it("模型目录失败不清除手填模型，也不会伪造目录", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
        json({ detail: "获取模型失败（HTTP 401）" }, 502),
    );
    function Picker() {
        const [value, setValue] = useState("manual-id");
        return (
            <Theme>
                <ModelPicker
                    connection={{
                        provider: "openai",
                        base_url: profile.base_url,
                        api_key: "",
                        profile_id: profile.id,
                    }}
                    value={value}
                    onChange={setValue}
                />
            </Theme>
        );
    }
    render(<Picker />);
    fireEvent.click(screen.getByRole("button", { name: "获取模型" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 401");
    expect(screen.getByLabelText("模型名称")).toHaveValue("manual-id");
    expect(document.querySelectorAll("datalist option")).toHaveLength(0);
});

it("切换配置时丢弃旧的模型列表响应", async () => {
    let resolve!: (response: Response) => void;
    vi.spyOn(globalThis, "fetch").mockImplementation(
        () =>
            new Promise<Response>((r) => {
                resolve = r;
            }),
    );
    const { rerender } = render(
        <Theme>
            <ModelPicker
                connection={{
                    provider: "openai",
                    base_url: profile.base_url,
                    api_key: "",
                    profile_id: "old",
                }}
                value=""
                onChange={vi.fn()}
            />
        </Theme>,
    );
    fireEvent.click(screen.getByRole("button", { name: "获取模型" }));
    rerender(
        <Theme>
            <ModelPicker
                connection={{
                    provider: "google",
                    base_url: "https://another.test/v1beta",
                    api_key: "",
                    profile_id: "new",
                }}
                value=""
                onChange={vi.fn()}
            />
        </Theme>,
    );
    await act(async () =>
        resolve(
            json({
                models: [{ id: "stale-model", name: "不该显示" }],
                truncated: false,
            }),
        ),
    );
    expect(
        document.querySelector('option[value="stale-model"]'),
    ).not.toBeInTheDocument();
});

it("浏览器模板保存配置 ID，但不保存 API Key", () => {
    const draft = defaultDraft("default");
    draft.rows[0] = {
        profile_id: profile.id,
        provider: profile.provider,
        base_url: profile.base_url,
        model: profile.default_model!,
        api_key: "must-not-persist",
    };
    const stored = safeTemplate(draft);
    expect(stored.rows[0].profile_id).toBe(profile.id);
    expect(JSON.stringify(stored)).not.toContain("must-not-persist");
});

it("续跑选择其它 API 时保留原存档模型，而不是悄悄换成配置默认模型", () => {
    const draft = {
        ...defaultDraft("default"),
        resume_run_id: "previous",
        name: "续跑",
    };
    draft.rows[0].model = "original-run-model";
    render(
        <Theme>
            <Dialog.Root open>
                <NewBatch
                    presets={["default"]}
                    initial={draft}
                    profiles={[profile]}
                    onCreated={vi.fn()}
                    onSave={vi.fn()}
                />
            </Dialog.Root>
        </Theme>,
    );
    fireEvent.change(screen.getByRole("combobox", { name: "API 配置" }), {
        target: { value: profile.id },
    });
    expect(screen.getByLabelText("模型名称")).toHaveValue("original-run-model");
});

it("读取中的配置不被误报为删除，也不显示已改用环境密钥", () => {
    const draft = defaultDraft("default");
    draft.rows[0].profile_id = "pending-profile";
    render(
        <Theme>
            <Dialog.Root open>
                <NewBatch
                    presets={["default"]}
                    initial={draft}
                    profilesLoading
                    onCreated={vi.fn()}
                    onSave={vi.fn()}
                />
            </Dialog.Root>
        </Theme>,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
        screen.getByText("配置尚未解析，不会自动改用其它凭证"),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "获取模型" })).toBeDisabled();
});

it("配置列表失败时保留引用并报错，不把连接悄悄改成自定义", () => {
    const draft = defaultDraft("default");
    draft.rows[0].profile_id = profile.id;
    render(
        <Theme>
            <Dialog.Root open>
                <NewBatch
                    presets={["default"]}
                    initial={draft}
                    profilesError="连接失败"
                    onCreated={vi.fn()}
                    onSave={vi.fn()}
                />
            </Dialog.Root>
        </Theme>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("无法读取 API 配置");
    expect(screen.getByRole("combobox", { name: "API 配置" })).toHaveValue(
        profile.id,
    );
    expect(screen.queryByLabelText("API Key")).not.toBeInTheDocument();
});

function renderProfileEditor() {
    const changed = vi.fn();
    render(<Theme><ApiProfilesPanel profiles={[profile]} loading={false} onChanged={changed} onUse={vi.fn()} /></Theme>);
    fireEvent.click(screen.getByRole("button", { name: "编辑 / 模型" }));
    return changed;
}

it("编辑时自动明文读取 Key，不影响未修改连接获取模型，也不持久化密钥", async () => {
    const store = vi.spyOn(Storage.prototype, "setItem");
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ api_key: "saved-visible-key" }));
    renderProfileEditor();
    const input = screen.getByLabelText("API Key", { exact: true });
    await waitFor(() => expect(input).toHaveValue("saved-visible-key"));
    expect(input).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "获取模型" })).toBeEnabled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(fetch.mock.calls[0][0]).toBe("/api/workbench/api-profiles/saved-api/key");
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: "POST", cache: "no-store" });
    expect(store).not.toHaveBeenCalled();
});

it("直接修改 Key 并保存，不需要额外更换凭证步骤", async () => {
    const fetch = vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(json({ api_key: "old-visible-key" }))
        .mockResolvedValueOnce(json(profile));
    const changed = renderProfileEditor();
    const input = screen.getByLabelText("API Key", { exact: true });
    await waitFor(() => expect(input).toHaveValue("old-visible-key"));
    fireEvent.change(input, { target: { value: "new-visible-key" } });
    expect(screen.getByRole("button", { name: "获取模型" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "保存 API 配置" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(fetch.mock.calls[1][1]?.method).toBe("PUT");
    expect(JSON.parse(fetch.mock.calls[1][1]?.body as string)).toMatchObject({ api_key: "new-visible-key", clear_api_key: false });
    expect(screen.queryByDisplayValue("new-visible-key")).not.toBeInTheDocument();
});

it("清空输入再保存直接移除已保存 Key，不把留空当保留", async () => {
    const fetch = vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(json({ api_key: "key-to-clear" }))
        .mockResolvedValueOnce(json({ ...profile, credential_present: false }));
    const changed = renderProfileEditor();
    const input = screen.getByLabelText("API Key", { exact: true });
    await waitFor(() => expect(input).toHaveValue("key-to-clear"));
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 API 配置" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(JSON.parse(fetch.mock.calls[1][1]?.body as string)).toMatchObject({ api_key: null, clear_api_key: true });
});

it("读取 Key 期间不能提交，避免加载中的空值清除旧 Key", async () => {
    let resolve!: (response: Response) => void;
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>(r => { resolve = r; }));
    renderProfileEditor();
    expect(screen.getByLabelText("API Key", { exact: true })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存 API 配置" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("API 配置名称"), { target: { value: "只改名字" } });
    fireEvent.submit(screen.getByLabelText("API 配置名称").closest("form")!);
    expect(fetch).toHaveBeenCalledOnce();
    await act(async () => resolve(json({ api_key: "loaded-key" })));
    expect(screen.getByLabelText("API Key", { exact: true })).toHaveValue("loaded-key");
    expect(screen.getByRole("button", { name: "保存 API 配置" })).toBeEnabled();
});

it("旧 Key 读取失败时允许直接输入替换，但不能误保存空值", async () => {
    const fetch = vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(json({ detail: "系统凭证缺失" }, 503))
        .mockResolvedValueOnce(json(profile));
    const changed = renderProfileEditor();
    expect(await screen.findByRole("alert")).toHaveTextContent("系统凭证缺失");
    expect(screen.getByRole("button", { name: "保存 API 配置" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("API Key", { exact: true }), { target: { value: "repaired-key" } });
    expect(screen.getByRole("button", { name: "保存 API 配置" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "保存 API 配置" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(JSON.parse(fetch.mock.calls[1][1]?.body as string).api_key).toBe("repaired-key");
});

it("关闭编辑后迟到的 Key 响应不能覆盖新窗口", async () => {
    let resolve!: (response: Response) => void;
    vi.spyOn(globalThis, "fetch")
        .mockImplementationOnce(() => new Promise<Response>(r => { resolve = r; }))
        .mockResolvedValueOnce(json({ api_key: "current-key" }));
    renderProfileEditor();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    fireEvent.click(screen.getByRole("button", { name: "编辑 / 模型" }));
    await waitFor(() => expect(screen.getByLabelText("API Key", { exact: true })).toHaveValue("current-key"));
    await act(async () => resolve(json({ api_key: "stale-key" })));
    expect(screen.getByLabelText("API Key", { exact: true })).toHaveValue("current-key");
});

it("未单独保存 Key 的配置保持空白，不把环境 Key 回填", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ api_key: null }));
    renderProfileEditor();
    await waitFor(() => expect(screen.getByRole("button", { name: "保存 API 配置" })).toBeEnabled());
    expect(screen.getByLabelText("API Key", { exact: true })).toHaveValue("");
    expect(screen.getByRole("button", { name: "获取模型" })).toBeEnabled();
});
