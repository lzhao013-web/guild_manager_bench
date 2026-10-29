import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OverviewItems } from "./OverviewItems";

afterEach(() => vi.unstubAllGlobals());

it("根据内容区宽高增减完整条目，隐藏期间不清空，条目仍能正常打开", () => {
    let width = 450, height = 180;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(() => new DOMRect(0, 0, width, height));
    let resize: ResizeObserverCallback;
    vi.stubGlobal("ResizeObserver", class {
        constructor(callback: ResizeObserverCallback) { resize = callback; }
        observe() {}
        disconnect() {}
    });
    const onOpen = vi.fn();
    const items = Array.from({ length: 20 }, (_, i) => i);
    const content = (values: number[]) => <OverviewItems className="test-items" items={values} rowHeight={28} minItemWidth={180}>
        {id => <button key={id} onClick={() => onOpen(id)}>项目 {id}</button>}
    </OverviewItems>;
    const view = render(content(items));
    expect(screen.getAllByRole("button")).toHaveLength(10);
    height = 248;
    act(() => resize([], {} as ResizeObserver));
    expect(screen.getAllByRole("button")).toHaveLength(14);
    width = 210;
    act(() => resize([], {} as ResizeObserver));
    expect(screen.getAllByRole("button")).toHaveLength(7);
    width = height = 0;
    act(() => resize([], {} as ResizeObserver));
    expect(screen.getAllByRole("button")).toHaveLength(7);
    width = 450; height = 180;
    act(() => resize([], {} as ResizeObserver));
    expect(screen.getAllByRole("button")).toHaveLength(10);
    screen.getByRole("button", { name: "项目 9" }).click();
    expect(onOpen).toHaveBeenCalledWith(9);
    view.rerender(content(items.slice(0, 3)));
    expect(screen.getAllByRole("button")).toHaveLength(3);
    height = 12;
    act(() => resize([], {} as ResizeObserver));
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    width = 1500; height = 62;
    view.rerender(content(items.slice(0, 10)));
    expect(screen.getAllByRole("button")).toHaveLength(10);
    expect(document.querySelector(".overview-items")).toHaveStyle({ gridTemplateColumns: "repeat(5, minmax(0, 1fr))" });
});
