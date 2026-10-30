import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ModelCallStats } from "./ModelCallStats";
import { modelEntries } from "./model";
import { modelMetrics } from "./modelMetrics";
import type { ModelEntry } from "./model";

afterEach(() => vi.useRealTimers());

it("展示本次调用的报告用量、耗时、缓存、推理和工具数", () => {
    const model: ModelEntry = { key: 1, text: "正文", reasoning: "", response: {
        usage: { input_tokens: 1200, output_tokens: 130, input_tokens_details: { cached_tokens: 800 }, output_tokens_details: { reasoning_tokens: 90 } },
        timing: { duration_ms: 2250 }, tool_calls: [{ name: "get_party" }],
    } };
    render(<ModelCallStats model={model} />);
    const stats = screen.getByRole("group", { name: "本次模型调用概览" });
    expect(stats).toHaveTextContent("输入 1,200 Token");
    expect(stats).toHaveTextContent("输出 130 Token");
    expect(stats).toHaveTextContent("耗时 2.3 秒");
    expect(stats).toHaveTextContent("缓存 800 Token");
    expect(stats).toHaveTextContent("推理 90 Token");
    expect(stats).toHaveTextContent("工具 1 个");
});
it("兼容已支持的 prompt/completion 字段，零值仍是实际报告值", () => {
    const metrics = modelMetrics({ key: 1, text: "", reasoning: "", response: { usage: { prompt_tokens: 0, completion_tokens: 0 }, timing: { duration_ms: 0 } } }, false, 0);
    expect(metrics.input).toBe(0);
    expect(metrics.output).toBe(0);
    expect(metrics.elapsed).toBe(0);
});
it("缺少 usage 或 timing 不显示为零，也不根据正文估算 Token", () => {
    render(<ModelCallStats model={{ key: 1, text: "很长的正文".repeat(100), reasoning: "", response: {} }} />);
    const stats = screen.getByRole("group");
    expect(stats).toHaveTextContent("输入 未报告");
    expect(stats).toHaveTextContent("输出 未报告");
    expect(stats).toHaveTextContent("耗时 未记录");
    expect(stats).not.toHaveTextContent("缓存");
});
it("实时未完成调用更新等待耗时，暂停后固定在所看位置", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-20T12:00:05Z"));
    const model: ModelEntry = { key: 1, text: "", reasoning: "", requestedAt: "2026-10-20T12:00:00Z", observedAt: "2026-10-20T12:00:02Z" };
    const view = render(<ModelCallStats model={model} live />);
    expect(screen.getByRole("group")).toHaveTextContent("已等待 5.0 秒");
    act(() => vi.advanceTimersByTime(2000));
    expect(screen.getByRole("group")).toHaveTextContent("已等待 7.0 秒");
    view.rerender(<ModelCallStats model={model} />);
    expect(screen.getByRole("group")).toHaveTextContent("记录间隔 2.0 秒");
    act(() => vi.advanceTimersByTime(10000));
    expect(screen.getByRole("group")).toHaveTextContent("记录间隔 2.0 秒");
});
it("等待与接收使用不同活动状态，暂停回看和已返回的调用不再播放", () => {
    const model: ModelEntry = { key: 1, text: "", reasoning: "" };
    const view = render(<ModelCallStats model={model} live />);
    expect(screen.getByText("等待响应")).toBeVisible();
    expect(document.querySelector(".model-activity")).toHaveAttribute("data-state", "waiting");
    view.rerender(<ModelCallStats model={{ ...model, reasoning: "正在规划" }} live />);
    expect(screen.getByText("接收中")).toBeVisible();
    expect(document.querySelector(".model-activity")).toHaveAttribute("data-state", "receiving");
    view.rerender(<ModelCallStats model={{ ...model, reasoning: "正在规划" }} />);
    expect(screen.getByText("未记录完整响应")).toBeVisible();
    expect(document.querySelector(".model-activity")).not.toBeInTheDocument();
    view.rerender(<ModelCallStats model={{ ...model, response: {} }} live />);
    expect(screen.getByText("已返回")).toBeVisible();
    expect(document.querySelector(".model-activity")).not.toBeInTheDocument();
});

it("所选历史调用保留自己的统计，不套用后一次调用的用量", () => {
    const events = [
        { type: "model_request", step: 1 },
        { type: "model_response", step: 1, usage: { input_tokens: 12, output_tokens: 3 }, timing: { duration_ms: 100 } },
        { type: "model_request", step: 2 },
        { type: "model_response", step: 2, usage: { input_tokens: 99, output_tokens: 20 }, timing: { duration_ms: 900 } },
    ].map((event, cursor) => ({ event, cursor, at: `2026-10-20T12:00:0${cursor}Z` }));
    const entries = modelEntries(events);
    expect(entries[0].requestedAt).toBe("2026-10-20T12:00:00Z");
    expect(entries[0].observedAt).toBe("2026-10-20T12:00:01Z");
    expect(modelMetrics(entries[0], false, 0)).toMatchObject({ input: 12, output: 3, elapsed: 100 });
    expect(modelMetrics(entries[1], false, 0)).toMatchObject({ input: 99, output: 20, elapsed: 900 });
});
