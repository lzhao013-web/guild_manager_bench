import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ElapsedTime } from "./ElapsedTime";

it("只有耗时文本每秒更新，阶段变化重新计时，终态固定且不重渲染父组件", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-29T10:00:10Z"));
    let renders = 0;
    function Parent({ start, end = null, live = true }: { start: string | null; end?: string | null; live?: boolean }) {
        renders++;
        return <span aria-label="阶段耗时"><ElapsedTime start={start} end={end} live={live} /></span>;
    }
    const view = render(<Parent start="2026-10-29T10:00:00Z" />);
    try {
        expect(screen.getByLabelText("阶段耗时")).toHaveTextContent("10 秒");
        act(() => vi.advanceTimersByTime(2000));
        expect(screen.getByLabelText("阶段耗时")).toHaveTextContent("12 秒");
        expect(renders).toBe(1);
        view.rerender(<Parent start="2026-10-29T10:00:11Z" />);
        expect(screen.getByLabelText("阶段耗时")).toHaveTextContent("1 秒");
        act(() => vi.advanceTimersByTime(1000));
        expect(screen.getByLabelText("阶段耗时")).toHaveTextContent("2 秒");
        view.rerender(<Parent start="2026-10-29T10:00:11Z" end="2026-10-29T10:00:13Z" live={false} />);
        act(() => vi.advanceTimersByTime(5000));
        expect(screen.getByLabelText("阶段耗时")).toHaveTextContent("2 秒");
        expect(vi.getTimerCount()).toBe(0);
        view.rerender(<Parent start={null} live={false} />);
        expect(screen.getByLabelText("阶段耗时")).toHaveTextContent("尚未启动");
    } finally {
        view.unmount();
        vi.useRealTimers();
    }
});

it("后台暂停计时更新，返回前台立即追上墙钟，卸载后清理计时器", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-29T10:00:10Z"));
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const view = render(<span aria-label="耗时"><ElapsedTime start="2026-10-29T10:00:00Z" live /></span>);
    try {
        visibility.mockReturnValue("hidden");
        fireEvent(document, new Event("visibilitychange"));
        expect(vi.getTimerCount()).toBe(0);
        act(() => vi.advanceTimersByTime(6000));
        expect(screen.getByLabelText("耗时")).toHaveTextContent("10 秒");
        visibility.mockReturnValue("visible");
        fireEvent(document, new Event("visibilitychange"));
        expect(screen.getByLabelText("耗时")).toHaveTextContent("16 秒");
        act(() => vi.advanceTimersByTime(1000));
        expect(screen.getByLabelText("耗时")).toHaveTextContent("17 秒");
        view.unmount();
        expect(vi.getTimerCount()).toBe(0);
    } finally {
        view.unmount();
        visibility.mockRestore();
        vi.useRealTimers();
    }
});
