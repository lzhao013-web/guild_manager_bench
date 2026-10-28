import { useState } from "react";
import type { CSSProperties } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { InspectorResizeHandle } from "./InspectorResizeHandle";

function setup() {
    const changed = vi.fn();
    function Harness() {
        const [width, setWidth] = useState(410);
        const [visible, setVisible] = useState(true);
        return <>
            <button onClick={() => setVisible(false)}>收起面板</button>
            <div className="runs-layout" style={{ "--inspector-width": `${width}px` } as CSSProperties}>
                <aside id="run-inspector">{visible && <InspectorResizeHandle width={width} onChange={next => { changed(next); setWidth(next); }} />}</aside>
            </div>
        </>;
    }
    render(<Harness />);
    const handle = screen.getByRole("separator", { name: "调整详情面板宽度" });
    const layout = handle.closest<HTMLElement>(".runs-layout")!;
    const capture = vi.fn();
    handle.setPointerCapture = capture;
    return { handle, layout, changed, capture };
}

it("拖动左边缘实时预览宽度，松开才提交，不占独立工具行", () => {
    const { handle, layout, changed, capture } = setup();
    expect(handle).toHaveAttribute("aria-controls", "run-inspector");
    expect(handle).toHaveAttribute("aria-orientation", "vertical");
    fireEvent.pointerDown(handle, { button: 0, pointerId: 7, clientX: 800 });
    expect(capture).toHaveBeenCalledWith(7);
    expect(handle).toHaveFocus();
    fireEvent.pointerMove(handle, { pointerId: 7, clientX: 700 });
    expect(layout.style.getPropertyValue("--inspector-width")).toBe("510px");
    expect(layout).toHaveClass("is-resizing");
    expect(handle).toHaveAttribute("aria-valuenow", "510");
    expect(changed).not.toHaveBeenCalled();
    fireEvent.pointerUp(handle, { pointerId: 7, clientX: 700 });
    expect(changed).toHaveBeenCalledExactlyOnceWith(510);
    expect(layout).not.toHaveClass("is-resizing");
    fireEvent.lostPointerCapture(handle, { pointerId: 7 });
    expect(changed).toHaveBeenCalledTimes(1);
});

it.each(["pointerCancel", "lostPointerCapture"] as const)("%s 取消拖动并恢复原宽度", finish => {
    const { handle, layout, changed } = setup();
    fireEvent.pointerDown(handle, { button: 0, pointerId: 7, clientX: 800 });
    fireEvent.pointerMove(handle, { pointerId: 7, clientX: 700 });
    fireEvent[finish](handle, { pointerId: 7 });
    expect(layout.style.getPropertyValue("--inspector-width")).toBe("410px");
    expect(handle).toHaveAttribute("aria-valuenow", "410");
    expect(layout).not.toHaveClass("is-resizing");
    expect(changed).not.toHaveBeenCalled();
});

it("右键不开始拖动，另一指针不能改变或提交当前拖动", () => {
    const { handle, layout, changed, capture } = setup();
    fireEvent.pointerDown(handle, { button: 2, pointerId: 1, clientX: 800 });
    expect(capture).not.toHaveBeenCalled();
    fireEvent.pointerDown(handle, { button: 0, pointerId: 7, clientX: 800 });
    fireEvent.pointerMove(handle, { pointerId: 8, clientX: 600 });
    fireEvent.pointerUp(handle, { pointerId: 8 });
    expect(layout.style.getPropertyValue("--inspector-width")).toBe("410px");
    expect(changed).not.toHaveBeenCalled();
    fireEvent.pointerCancel(handle, { pointerId: 7 });
});

it("宽度限制在340至600像素", () => {
    const { handle, layout, changed } = setup();
    fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientX: 800 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 0 });
    fireEvent.pointerUp(handle, { pointerId: 1 });
    expect(changed).toHaveBeenLastCalledWith(600);
    fireEvent.pointerDown(handle, { button: 0, pointerId: 2, clientX: 600 });
    fireEvent.pointerMove(handle, { pointerId: 2, clientX: 1600 });
    fireEvent.pointerUp(handle, { pointerId: 2 });
    expect(changed).toHaveBeenLastCalledWith(340);
    expect(layout.style.getPropertyValue("--inspector-width")).toBe("340px");
});

it("支持键盘左右调整和Home、End定位边界", () => {
    const { handle, changed } = setup();
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(handle).toHaveAttribute("aria-valuenow", "426");
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(handle).toHaveAttribute("aria-valuenow", "410");
    fireEvent.keyDown(handle, { key: "Home" });
    expect(handle).toHaveAttribute("aria-valuenow", "340");
    fireEvent.keyDown(handle, { key: "End" });
    expect(handle).toHaveAttribute("aria-valuenow", "600");
    fireEvent.keyDown(handle, { key: "Tab" });
    expect(changed).toHaveBeenCalledTimes(4);
});

it("拖动中收起面板不会留下临时宽度或禁止选中文字的状态", () => {
    const { handle, layout, changed } = setup();
    fireEvent.pointerDown(handle, { button: 0, pointerId: 7, clientX: 800 });
    fireEvent.pointerMove(handle, { pointerId: 7, clientX: 700 });
    fireEvent.click(screen.getByRole("button", { name: "收起面板" }));
    expect(screen.queryByRole("separator")).not.toBeInTheDocument();
    expect(layout.style.getPropertyValue("--inspector-width")).toBe("410px");
    expect(layout).not.toHaveClass("is-resizing");
    expect(changed).not.toHaveBeenCalled();
});
