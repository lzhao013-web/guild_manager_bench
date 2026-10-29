import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ChangeValue, useNewItemKeys } from "./motion";

afterEach(() => vi.useRealTimers());

it("只有真实值变化才启动高亮，文本节点在轮询和更新中保持稳定", () => {
    vi.useFakeTimers();
    const node = (value: number) => <ChangeValue value={value}>{value}</ChangeValue>;
    const view = render(node(10));
    const initial = screen.getByText("10");
    expect(initial).not.toHaveClass("is-changed");
    expect(initial).not.toHaveAttribute("data-change-revision");
    view.rerender(node(10));
    expect(screen.getByText("10")).toBe(initial);
    expect(initial).not.toHaveClass("is-changed");
    view.rerender(node(12));
    const changed = screen.getByText("12");
    expect(changed).toBe(initial);
    expect(changed).toHaveClass("is-changed");
    expect(changed).toHaveAttribute("data-change-revision", "1");
    view.rerender(node(12));
    expect(screen.getByText("12")).toBe(changed);
    act(() => vi.advanceTimersByTime(500));
    expect(changed).not.toHaveClass("is-changed");
});

function NewItems({ keys, enabled }: { keys: number[]; enabled: boolean }) {
    const recent = useNewItemKeys(keys, enabled);
    return <>{keys.map(key => <span key={key} data-item={key} className={recent.has(key) ? "motion-new" : ""}>{key}</span>)}</>;
}

it("新列表项只在实时追加时短暂标记，暂停期间不会积压入场动画", () => {
    vi.useFakeTimers();
    const node = (keys: number[], enabled = true) => <NewItems keys={keys} enabled={enabled} />;
    const view = render(node([1, 2]));
    expect(document.querySelectorAll(".motion-new")).toHaveLength(0);
    view.rerender(node([1, 2]));
    expect(document.querySelectorAll(".motion-new")).toHaveLength(0);
    view.rerender(node([1, 2, 3]));
    expect(screen.getByText("2")).not.toHaveClass("motion-new");
    expect(screen.getByText("3")).toHaveClass("motion-new");
    act(() => vi.advanceTimersByTime(220));
    expect(screen.getByText("3")).not.toHaveClass("motion-new");
    view.rerender(node([1, 2, 3, 4], false));
    expect(screen.getByText("4")).not.toHaveClass("motion-new");
    view.rerender(node([1, 2, 3, 4], true));
    expect(screen.getByText("4")).not.toHaveClass("motion-new");
});
