import { useState } from "react";
import { Copy } from "@phosphor-icons/react";
import type { MemoSnapshot } from "./watchModel";
import "./memo.css";

type MemoPanelProps = {
    memo: MemoSnapshot;
    onActions?: () => void;
    reverted?: boolean;
} & ({ compact: true; onExpand: () => void } | { compact?: false; onExpand?: never });

export function MemoPanel({ memo, compact = false, onExpand, onActions, reverted = false }: MemoPanelProps) {
    const [copying, setCopying] = useState(false);
    const [feedback, setFeedback] = useState<{ content: string; text: string } | null>(null);
    const content = memo.state === "saved" ? memo.content : null;
    const meta = (memo.state === "saved" || memo.state === "empty") && memo.updated_turn !== null
        ? <p className="memo-meta" title={`最后修改于 T${memo.updated_turn}`}>
            {compact ? `T${memo.updated_turn} 更新` : `最后修改于 T${memo.updated_turn}`}
        </p> : null;
    const emptyNote = memo.state === "empty"
        ? memo.updated_turn === null ? "模型尚未记录备忘。" : "模型已清空备忘。"
        : memo.state === "legacy" ? "此记录使用旧版逐条备忘，请在行动时间线查看原文。" : "该位置未记录备忘状态。";
    const warning = reverted && <p className="memo-note">此尝试未保留，显示回合开始时的备忘。</p>;
    const legacyAction = memo.state === "legacy" && onActions && <div className="memo-actions"><button onClick={onActions}>查看行动记录</button></div>;
    async function copy() {
        if (content === null) return;
        setCopying(true);
        try {
            await navigator.clipboard.writeText(content);
            setFeedback({ content, text: "已复制" });
        } catch {
            setFeedback({ content, text: "复制失败，请手动选择正文复制" });
        } finally {
            setCopying(false);
        }
    }
    if (compact) return <section className="memo-panel is-compact" aria-label="模型备忘">
        {warning}
        {memo.state === "saved" ? <button className="memo-preview" aria-label="展开备忘全文" title="点击查看完整备忘" onClick={onExpand}>
            <span className="memo-text">{memo.content}</span>
        </button> : <p className="memo-note">{emptyNote}</p>}
        {meta}
        {legacyAction}
    </section>;
    return <section className="memo-panel memo-reader" aria-label="模型备忘">
        {warning}
        <div className="memo-heading">
            {meta}
            {memo.state === "saved" && <div className="memo-actions">
                <button disabled={copying} onClick={() => void copy()}><Copy size={14} />{copying ? "复制中" : "复制备忘"}</button>
                {feedback?.content === content && <span role="status">{feedback.text}</span>}
            </div>}
        </div>
        {memo.state === "saved" ? <pre className="memo-text">{memo.content}</pre> : <p className="memo-note">{emptyNote}</p>}
        {legacyAction}
    </section>;
}
