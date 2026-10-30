import { useEffect, useState } from "react";
import { fmt, short } from "./model";
import type { ModelEntry } from "./model";
import { milliseconds, modelMetrics } from "./modelMetrics";
import { ModelActivity } from "./motion";
import "./model-stats.css";

/** 仅展示本次调用的已报告用量，不把整局累计值带入历史调用。 */
export function ModelCallStats({ model, live = false, compact = false }: {
    model: ModelEntry; live?: boolean; compact?: boolean;
}) {
    const [now, setNow] = useState(Date.now);
    const pending = !model.response;
    useEffect(() => {
        if (!live || !pending || !model.requestedAt) return;
        setNow(Date.now());
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [live, pending, model.key, model.requestedAt]);
    const metrics = modelMetrics(model, live, now);
    const tokens = (value: number | null) => value === null ? "未报告" : compact ? short(value) : fmt(value);
    const extra = [
        metrics.cached === null ? null : `缓存 ${fmt(metrics.cached)} Token`,
        metrics.reasoning === null ? null : `推理 ${fmt(metrics.reasoning)} Token`,
        metrics.tools === null ? null : `工具 ${metrics.tools} 个`,
    ].filter(Boolean).join(" · ");
    return <span className={`model-call-stats${compact ? " is-compact" : ""}`} role="group" aria-label="本次模型调用概览" title={`本次调用，不是整局累计${extra ? ` · ${extra}` : ""}`}>
        <span className="model-call-state">{live && pending && <ModelActivity state={model.text || model.reasoning ? "receiving" : "waiting"} />}{metrics.status}</span>
        <span title={`本次输入 Token：${metrics.input === null ? "API 未报告" : fmt(metrics.input)}`}>输入 <b>{tokens(metrics.input)}</b><small className="model-input-unit"> Token</small></span>
        <span title={`本次输出 Token：${metrics.output === null ? "API 未报告" : fmt(metrics.output)}`}>输出 <b>{tokens(metrics.output)}</b><small> Token</small></span>
        <span>{metrics.elapsedLabel} <b>{milliseconds(metrics.elapsed)}</b></span>
        {!compact && metrics.cached !== null && <span>缓存 <b>{fmt(metrics.cached)}</b><small> Token</small></span>}
        {!compact && metrics.reasoning !== null && <span>推理 <b>{fmt(metrics.reasoning)}</b><small> Token</small></span>}
        {!compact && metrics.tools !== null && <span>工具 <b>{metrics.tools}</b><small> 个</small></span>}
    </span>;
}
