import { useEffect, useRef, useState } from "react";
import { X } from "@phosphor-icons/react";
import { STATUS } from "./model";
import type { Job } from "./types";

export function RunResultNotice({ name, status, onOpen, onDismiss }: {
    name: string;
    status: Job["status"];
    onOpen: () => void;
    onDismiss: () => void;
}) {
    const [leaving, setLeaving] = useState(false);
    const timer = useRef<number | null>(null);
    useEffect(() => () => {
        if (timer.current !== null) window.clearTimeout(timer.current);
    }, []);
    function dismiss() {
        if (leaving) return;
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            onDismiss();
            return;
        }
        setLeaving(true);
        timer.current = window.setTimeout(onDismiss, 140);
    }
    return <div className={`run-result-notice ${status === "failed" ? "is-failed" : ""} ${leaving ? "is-leaving" : ""}`}>
        <span><strong>{name}</strong> {STATUS[status]}，已移至历史存档。</span>
        <button className="text-button" onClick={onOpen}>查看结果</button>
        <button className="notice-dismiss" aria-label={`关闭 ${name} 的结果通知`} disabled={leaving} onClick={dismiss}><X size={16} /></button>
    </div>;
}
