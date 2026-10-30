import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ModelContentTabs } from "./ModelContentTabs";
import { MemoPanel } from "./MemoPanel";
import type { ModelTab } from "./ModelContentTabs";
import type { MemoSnapshot } from "./watchModel";

afterEach(() => vi.unstubAllGlobals());

it("紧凑卡片只保留可点开的原文预览，页签支持键盘，不再挤入复制操作", () => {
    const text = '<img src=x onerror="alert(1)">\n保留完整备忘，下一回合再换装。'.repeat(20);
    const expand = vi.fn();
    function Harness() {
        const [value, setValue] = useState<ModelTab>("output");
        return <ModelContentTabs value={value} onChange={setValue} compact output={<p>模型输出样例</p>}
            memo={<MemoPanel memo={{ state: "saved", content: text, updated_turn: 4 }} compact onExpand={expand} />} />;
    }
    render(<Harness />);
    const output = screen.getByRole("tab", { name: "输出" });
    output.focus();
    fireEvent.keyDown(output, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "备忘录" })).toHaveFocus();
    expect(screen.getByRole("tabpanel", { name: "备忘录" })).toBeVisible();
    expect(screen.getByText("T4 更新")).toBeVisible();
    expect(document.querySelector(".memo-text")!.textContent).toBe(text);
    expect(document.querySelector(".memo-text img")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制备忘" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开备忘全文" }));
    expect(expand).toHaveBeenCalledOnce();
});

it("全文保留完整原文和复制，不执行模型写入的 HTML", async () => {
    const text = '<img src=x onerror="alert(1)">\n完整原文。'.repeat(20);
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", Object.create(navigator, { clipboard: { value: { writeText } } }));
    render(<MemoPanel memo={{ state: "saved", content: text, updated_turn: 4 }} />);
    expect(screen.getByText("最后修改于 T4")).toBeVisible();
    expect(document.querySelector(".memo-text")!.textContent).toBe(text);
    expect(document.querySelector(".memo-text img")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "复制备忘" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已复制");
    expect(writeText).toHaveBeenCalledWith(text);
});

it("首次未写、主动清空和旧记录缺失使用不同说明", () => {
    const node = (memo: MemoSnapshot) => <MemoPanel memo={memo} />;
    const view = render(node({ state: "empty", updated_turn: null }));
    expect(screen.getByText("模型尚未记录备忘。")).toBeVisible();
    view.rerender(node({ state: "empty", updated_turn: 5 }));
    expect(screen.getByText("模型已清空备忘。")).toBeVisible();
    expect(screen.getByText("最后修改于 T5")).toBeVisible();
    view.rerender(node({ state: "unavailable" }));
    expect(screen.getByText("该位置未记录备忘状态。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "复制备忘" })).not.toBeInTheDocument();
});
