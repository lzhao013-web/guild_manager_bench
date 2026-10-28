import { useState } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ResizableTable } from "./ResizableTable";
import { enhanceResizableTable } from "../../assets/table-resize.mjs";

const columns = [
    { id: "name", label: "名称", minWidth: 80 },
    { id: "count", label: "次数", minWidth: 64 },
];
afterEach(() => vi.unstubAllGlobals());

function geometry(visible = () => true) {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
        if (!visible()) return new DOMRect();
        if (this.tagName === "COL") {
            const table = this.closest("table")!;
            const index = [...this.parentElement!.children].indexOf(this);
            const natural = table.tHead!.rows[0].cells[index].getAttribute("aria-label")?.includes("次数") ? 100 : 200;
            return new DOMRect(0, 0, this.style.width ? parseFloat(this.style.width) : natural, 32);
        }
        if (this instanceof HTMLTableCellElement) {
            const table = this.closest("table")!;
            const col = table.querySelector("colgroup")!.children[this.cellIndex] as HTMLElement;
            const natural = this.getAttribute("aria-label")?.includes("次数") ? 100 : 200;
            return new DOMRect(0, 0, col.style.width ? parseFloat(col.style.width) : natural, 32);
        }
        return new DOMRect(0, 0, 400, 200);
    });
}
function captures(handles: HTMLElement[]) {
    for (const handle of handles) {
        const captured = new Set<number>();
        handle.setPointerCapture = vi.fn(id => { captured.add(id); });
        handle.hasPointerCapture = vi.fn(id => captured.has(id));
        handle.releasePointerCapture = vi.fn(id => { captured.delete(id); });
    }
}
function setup() {
    const sort = vi.fn(), row = vi.fn();
    function Harness() {
        const [rows, setRows] = useState(1);
        const [reversed, setReversed] = useState(false);
        const order = reversed ? [...columns].reverse() : columns;
        return <><button onClick={() => setRows(rows + 1)}>更新数据</button><button onClick={() => setReversed(!reversed)}>切换列顺序</button>
            <ResizableTable storageKey="test" columns={order} scroll>
                <thead><tr>{order.map(column => <th key={column.id}>{column.id === "name" ? <button onClick={sort}>名称</button> : column.label}</th>)}</tr></thead>
                <tbody><tr onClick={row}>{order.map(column => <td key={column.id}>{column.id === "name" ? "测试记录" : rows}</td>)}</tr></tbody>
            </ResizableTable></>;
    }
    const view = render(<Harness />);
    const handles = screen.getAllByRole("separator") as HTMLElement[];
    captures(handles);
    return { ...view, sort, row, handles, table: screen.getByRole("table") as HTMLTableElement };
}
function drag(handle: HTMLElement, from: number, to: number, pointerId = 1) {
    fireEvent.pointerDown(handle, { button: 0, pointerId, clientX: from });
    fireEvent.pointerMove(handle, { pointerId, clientX: to });
    fireEvent.pointerUp(handle, { pointerId, clientX: to });
}

it("拖动仅改变当前列，松开保存，点击分隔线不触发排序或行点击", () => {
    geometry();
    const { table, handles, sort, row } = setup();
    const save = vi.spyOn(Storage.prototype, "setItem");
    fireEvent.pointerDown(handles[0], { button: 0, pointerId: 7, clientX: 200 });
    fireEvent.pointerMove(handles[0], { pointerId: 7, clientX: 280 });
    expect(table.querySelectorAll("col")[0].style.width).toBe("280px");
    expect(table.querySelectorAll("col")[1].style.width).toBe("100px");
    expect(table.style.width).toBe("380px");
    expect(handles[0]).toHaveAttribute("aria-valuenow", "280");
    expect(save).not.toHaveBeenCalled();
    fireEvent.pointerUp(handles[0], { pointerId: 7 });
    expect(save).toHaveBeenCalledExactlyOnceWith("gmb.column-widths.v1.test", JSON.stringify({ name: 280 }));
    fireEvent.click(handles[0]);
    expect(sort).not.toHaveBeenCalled();
    expect(row).not.toHaveBeenCalled();
    expect(screen.getByRole("columnheader", { name: "名称" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "名称" }));
    expect(sort).toHaveBeenCalledOnce();
});

it("单击不固化默认布局，取消或丢失捕获不保存临时宽度", () => {
    geometry();
    const { table, handles } = setup();
    fireEvent.pointerDown(handles[0], { button: 0, pointerId: 1, clientX: 200 });
    fireEvent.pointerUp(handles[0], { pointerId: 1 });
    expect(table).not.toHaveAttribute("data-column-sized");
    for (const finish of ["pointerCancel", "lostPointerCapture"] as const) {
        fireEvent.pointerDown(handles[0], { button: 0, pointerId: 2, clientX: 200 });
        fireEvent.pointerMove(handles[0], { pointerId: 2, clientX: 350 });
        fireEvent[finish](handles[0], { pointerId: 2 });
        expect(table.style.width).toBe("");
        expect(table).not.toHaveClass("is-column-resizing");
        expect(localStorage.getItem("gmb.column-widths.v1.test")).toBeNull();
    }
});

it("实时数据刷新不打断拖拽，另一个指针不能提交当前操作", () => {
    geometry();
    const { handles } = setup();
    fireEvent.pointerDown(handles[0], { button: 0, pointerId: 7, clientX: 200 });
    fireEvent.pointerMove(handles[0], { pointerId: 7, clientX: 260 });
    fireEvent.click(screen.getByRole("button", { name: "更新数据" }));
    fireEvent.pointerMove(handles[0], { pointerId: 8, clientX: 400 });
    fireEvent.pointerUp(handles[0], { pointerId: 8 });
    expect(localStorage.getItem("gmb.column-widths.v1.test")).toBeNull();
    fireEvent.pointerMove(handles[0], { pointerId: 7, clientX: 300 });
    fireEvent.pointerUp(handles[0], { pointerId: 7 });
    expect(JSON.parse(localStorage.getItem("gmb.column-widths.v1.test")!)).toEqual({ name: 300 });
});

it("列宽按列ID恢复，重排后不会串列，双击只恢复当前列", () => {
    geometry();
    localStorage.setItem("gmb.column-widths.v1.test", JSON.stringify({ name: 280, count: 120 }));
    const { table } = setup();
    fireEvent.click(screen.getByRole("button", { name: "切换列顺序" }));
    expect([...table.querySelectorAll("col")].map(col => col.style.width)).toEqual(["120px", "280px"]);
    fireEvent.doubleClick(screen.getByRole("separator", { name: "调整名称列宽" }));
    expect(JSON.parse(localStorage.getItem("gmb.column-widths.v1.test")!)).toEqual({ count: 120 });
    expect([...table.querySelectorAll("col")].map(col => col.style.width)).toEqual(["120px", "200px"]);
});

it("拖动中列结构改变时取消临时值，不把旧列宽写给新位置", () => {
    geometry();
    const { handles, table } = setup();
    fireEvent.pointerDown(handles[0], { button: 0, pointerId: 7, clientX: 200 });
    fireEvent.pointerMove(handles[0], { pointerId: 7, clientX: 300 });
    fireEvent.click(screen.getByRole("button", { name: "切换列顺序" }));
    expect(table).not.toHaveClass("is-column-resizing");
    expect(localStorage.getItem("gmb.column-widths.v1.test")).toBeNull();
    fireEvent.pointerUp(handles[0], { pointerId: 7 });
    expect(localStorage.getItem("gmb.column-widths.v1.test")).toBeNull();
    expect(screen.getByRole("separator", { name: "调整次数列宽" })).toHaveAttribute("aria-valuenow", "100");
});

it("写入当前列时保留暂未展示的字段偏好", () => {
    geometry();
    localStorage.setItem("gmb.column-widths.v1.test", JSON.stringify({ name: 280, unseen: 450 }));
    const { handles } = setup();
    drag(handles[1], 100, 140);
    expect(JSON.parse(localStorage.getItem("gmb.column-widths.v1.test")!)).toEqual({ name: 280, count: 140, unseen: 450 });
});

it("键盘调整遵守最小宽度，支持Home和End，取消挂载会结束拖拽", () => {
    geometry();
    const { handles, table, unmount } = setup();
    fireEvent.keyDown(handles[0], { key: "ArrowRight" });
    expect(handles[0]).toHaveAttribute("aria-valuenow", "216");
    fireEvent.keyDown(handles[0], { key: "Home" });
    expect(handles[0]).toHaveAttribute("aria-valuenow", "80");
    fireEvent.keyDown(handles[0], { key: "ArrowLeft" });
    expect(handles[0]).toHaveAttribute("aria-valuenow", "80");
    fireEvent.keyDown(handles[0], { key: "End" });
    expect(handles[0]).toHaveAttribute("aria-valuenow", "1600");
    fireEvent.pointerDown(handles[0], { button: 0, pointerId: 3, clientX: 800 });
    fireEvent.pointerMove(handles[0], { pointerId: 3, clientX: 700 });
    unmount();
    expect(table).not.toHaveClass("is-column-resizing");
    expect(JSON.parse(localStorage.getItem("gmb.column-widths.v1.test")!)).toEqual({ name: 1600 });
});

it.each(["invalid JSON", "null", "[]", '{"name":"invalid","count":-1}'])("损坏缓存 %s 不阻止列表显示", raw => {
    geometry();
    localStorage.setItem("gmb.column-widths.v1.test", raw);
    const { table } = setup();
    expect(table).not.toHaveAttribute("data-column-sized");
    expect(screen.getByRole("separator", { name: "调整名称列宽" })).toHaveAttribute("aria-valuenow", "200");
});

it("折叠中的表格等实际可见后恢复列宽", () => {
    let visible = false;
    geometry(() => visible);
    const callbacks: (() => void)[] = [];
    vi.stubGlobal("ResizeObserver", class {
        constructor(callback: () => void) { callbacks.push(callback); }
        observe() {}
        unobserve() {}
        disconnect() {}
    });
    localStorage.setItem("gmb.column-widths.v1.test", JSON.stringify({ name: 280 }));
    const { table } = setup();
    expect(table).not.toHaveAttribute("data-column-sized");
    visible = true;
    act(() => callbacks.forEach(callback => callback()));
    expect(table.querySelector("col")!.style.width).toBe("280px");
});

it.each([true, false])("静态页面复用同一逻辑，已有colgroup为 %s，清理后可重新挂载", existing => {
    geometry();
    const host = document.createElement("div");
    const table = document.createElement("table");
    table.id = "legacy-test";
    table.innerHTML = `${existing ? '<colgroup><col class="original"><col></colgroup>' : ''}<thead><tr><th>名称</th><th>次数</th></tr></thead><tbody><tr><td>示例</td><td>1</td></tr></tbody>`;
    host.append(table);
    document.body.append(host);
    const destroy = enhanceResizableTable(table, { key: "legacy", columns });
    const handles = [...table.querySelectorAll<HTMLElement>(".column-resize-handle")];
    captures(handles);
    drag(handles[0], 200, 280);
    destroy();
    expect(table.querySelector(".column-resize-handle")).toBeNull();
    expect(table.querySelectorAll("colgroup")).toHaveLength(existing ? 1 : 0);
    if (existing) expect(table.querySelector("col")).toHaveClass("original");
    const destroyAgain = enhanceResizableTable(table, { key: "legacy", columns });
    expect(table.querySelector("col")!.style.width).toBe("280px");
    destroyAgain();
    host.remove();
});

it("不同表格独立记忆，嵌套表格的拖动不会调整外层", () => {
    geometry();
    render(<ResizableTable storageKey="outer" columns={columns} scroll><thead><tr><th>名称</th><th>次数</th></tr></thead>
        <tbody><tr><td colSpan={2}><ResizableTable storageKey="inner" columns={columns} scroll><thead><tr><th>名称</th><th>次数</th></tr></thead>
            <tbody><tr><td>内层</td><td>1</td></tr></tbody></ResizableTable></td></tr></tbody></ResizableTable>);
    const inner = screen.getAllByRole("table")[1];
    const handle = within(inner).getByRole("separator", { name: "调整名称列宽" });
    captures([handle]);
    drag(handle, 200, 280);
    expect(localStorage.getItem("gmb.column-widths.v1.outer")).toBeNull();
    expect(JSON.parse(localStorage.getItem("gmb.column-widths.v1.inner")!)).toEqual({ name: 280 });
});
