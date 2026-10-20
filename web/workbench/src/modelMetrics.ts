import type { ModelEntry } from "./model";

const number = (value: unknown): number | null =>
    typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;

interface ResponseMetrics {
    usage?: {
        input_tokens?: number; prompt_tokens?: number;
        output_tokens?: number; completion_tokens?: number;
        cached_tokens?: number; cache_read_input_tokens?: number;
        input_tokens_details?: { cached_tokens?: number };
        prompt_tokens_details?: { cached_tokens?: number };
        output_tokens_details?: { reasoning_tokens?: number };
        completion_tokens_details?: { reasoning_tokens?: number };
    };
    timing?: { duration_ms?: number };
    tool_calls?: unknown[];
}

export function modelMetrics(model: ModelEntry, live: boolean, now: number) {
    const response = model.response as ResponseMetrics | undefined;
    const usage = response?.usage;
    let elapsed = number(response?.timing?.duration_ms);
    if (!response && model.requestedAt) {
        const end = live ? now : model.observedAt ? Date.parse(model.observedAt) : NaN;
        elapsed = number(Math.max(0, end - Date.parse(model.requestedAt)));
    }
    return {
        status: response ? "已返回" : live ? model.text || model.reasoning ? "接收中" : "等待响应" : "未记录完整响应",
        input: number(usage?.input_tokens ?? usage?.prompt_tokens),
        output: number(usage?.output_tokens ?? usage?.completion_tokens),
        cached: number(usage?.cached_tokens ?? usage?.input_tokens_details?.cached_tokens ?? usage?.prompt_tokens_details?.cached_tokens ?? usage?.cache_read_input_tokens),
        reasoning: number(usage?.output_tokens_details?.reasoning_tokens ?? usage?.completion_tokens_details?.reasoning_tokens),
        tools: response?.tool_calls?.length ?? null,
        elapsed,
        elapsedLabel: response ? "耗时" : live ? "已等待" : "记录间隔",
    };
}
export function milliseconds(value: number | null): string {
    if (value === null) return "未记录";
    if (value < 1000) return `${Math.round(value)} ms`;
    if (value < 60000) return `${(value / 1000).toFixed(1)} 秒`;
    return `${Math.floor(value / 60000)} 分 ${Math.floor(value % 60000 / 1000)} 秒`;
}
