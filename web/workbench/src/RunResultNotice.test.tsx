import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RunResultNotice } from "./RunResultNotice";

afterEach(() => vi.useRealTimers());

it("结果通知关闭时先播放短暂退出，再移除对应通知", () => {
    vi.useFakeTimers();
    const onDismiss = vi.fn();
    render(<RunResultNotice name="构造完成运行" status="completed" onOpen={() => {}} onDismiss={onDismiss} />);
    const notice = document.querySelector(".run-result-notice")!;
    expect(notice).not.toHaveClass("is-leaving");
    fireEvent.click(screen.getByRole("button", { name: "关闭 构造完成运行 的结果通知" }));
    expect(notice).toHaveClass("is-leaving");
    expect(onDismiss).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(140));
    expect(onDismiss).toHaveBeenCalledTimes(1);
});

it("减少动态时立即关闭通知，不保留退出等待", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
    const onDismiss = vi.fn();
    render(<RunResultNotice name="构造失败运行" status="failed" onOpen={() => {}} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: "关闭 构造失败运行 的结果通知" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
});
