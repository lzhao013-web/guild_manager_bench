import { useId } from "react";
import type { ReactNode } from "react";
import "./memo.css";

export type ModelTab = "output" | "memo";
const tabs = [["output", "输出"], ["memo", "备忘录"]] as const;

export function ModelContentTabs({ value, onChange, output, memo, compact = false, heading, action }: {
    value: ModelTab;
    onChange: (value: ModelTab) => void;
    output: ReactNode;
    memo: ReactNode;
    compact?: boolean;
    heading?: ReactNode;
    action?: ReactNode;
}) {
    const id = useId();
    return <div className={`model-content-tabs ${compact ? "is-compact" : ""}`}>
        <div className="model-content-toolbar">
            {heading && <div className="model-content-heading">{heading}</div>}
            <div role="tablist" aria-label="模型内容" className="model-content-tablist">
                {tabs.map(([key, label], index) => <button key={key} role="tab" id={`${id}-${key}-tab`}
                    aria-selected={value === key} aria-controls={`${id}-${key}-panel`} tabIndex={value === key ? 0 : -1}
                    onClick={() => onChange(key)} onKeyDown={event => {
                        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                        event.preventDefault();
                        const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : (index + 1) % 2;
                        onChange(tabs[next][0]);
                        event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next].focus();
                    }}>{label}</button>)}
            </div>
            {action && <span className="model-content-action">{action}</span>}
        </div>
        <div role="tabpanel" id={`${id}-output-panel`} aria-labelledby={`${id}-output-tab`} hidden={value !== "output"} className="model-content-tabpanel">{output}</div>
        <div role="tabpanel" id={`${id}-memo-panel`} aria-labelledby={`${id}-memo-tab`} hidden={value !== "memo"} className="model-content-tabpanel">{memo}</div>
    </div>;
}
