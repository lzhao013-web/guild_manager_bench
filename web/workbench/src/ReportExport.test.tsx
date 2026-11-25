import { Theme } from "@radix-ui/themes";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReportExportButton } from "./ReportExport";

afterEach(() => vi.unstubAllGlobals());

function setup() {
    const create = vi.fn((_blob: Blob) => "blob:report-download");
    const revoke = vi.fn();
    vi.stubGlobal("URL", Object.assign(class extends URL {}, { createObjectURL: create, revokeObjectURL: revoke }));
    const downloads: { href: string; name: string }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
        downloads.push({ href: this.href, name: this.download });
    });
    render(<Theme><ReportExportButton runId="局 & #1" /></Theme>);
    return { create, downloads };
}

it("按需生成单文件战报，生成中防重复提交，不读取原始回放", async () => {
    let resolve!: (response: Response) => void;
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(done => { resolve = done; }));
    const { create, downloads } = setup();
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "导出战报" }));
    expect(screen.getByRole("button")).toBeDisabled();
    fireEvent.click(screen.getByRole("button"));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe("/api/llm/runs/%E5%B1%80%20%26%20%231/report");
    const html = '<!doctype html><html lang="zh-CN"><body>离线战报</body></html>';
    await act(async () => resolve(new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
    await waitFor(() => expect(downloads).toEqual([{ href: "blob:report-download", name: "guild-report.html" }]));
    expect(await create.mock.calls[0][0].text()).toBe(html);
    expect(screen.getByRole("status")).toHaveTextContent("浏览器下载");
    expect(screen.getByRole("button", { name: "导出战报" })).toBeEnabled();
    expect(document.querySelector("a[download]")).toBeNull();
});

it("读取失败不下载错误页面，并允许重试", async () => {
    vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "存档暂不可读，请稍后重试" }), { status: 503 }))
        .mockResolvedValueOnce(new Response("<!doctype html><p>战报</p>", { headers: { "Content-Type": "text/html" } }));
    const { create } = setup();
    fireEvent.click(screen.getByRole("button", { name: "导出战报" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("存档暂不可读");
    expect(create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "导出战报" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
