import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ChangeValue, ResourceValue, useNewItemKeys } from "./motion";

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

it("资源只显示连续实时快照的净变化，暂停、恢复和更换回合尝试不伪造收益", () => {
    vi.useFakeTimers();
    const node = (value: number | null, live = true, scope = "run:attempt-1") =>
        <ResourceValue value={value} live={live} scope={scope} label="金币">{value ?? "未知"}</ResourceValue>;
    const view = render(node(100));
    expect(screen.queryByText("+100")).not.toBeInTheDocument();
    view.rerender(node(60));
    expect(screen.getByText("-40")).toBeVisible();
    view.rerender(node(60));
    act(() => vi.advanceTimersByTime(1600));
    expect(screen.queryByText("-40")).not.toBeInTheDocument();
    view.rerender(node(0));
    expect(screen.getByText("-60")).toBeVisible();
    view.rerender(node(20));
    expect(screen.getByText("+20")).toBeVisible();
    view.rerender(node(100, false));
    expect(document.querySelector(".resource-delta")).not.toBeInTheDocument();
    view.rerender(node(250));
    expect(document.querySelector(".resource-delta")).not.toBeInTheDocument();
    view.rerender(node(300, true, "run:attempt-2"));
    expect(document.querySelector(".resource-delta")).not.toBeInTheDocument();
    view.rerender(node(null, true, "run:attempt-2"));
    view.rerender(node(400, true, "run:attempt-2"));
    expect(document.querySelector(".resource-delta")).not.toBeInTheDocument();
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
