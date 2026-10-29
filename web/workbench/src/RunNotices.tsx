import { useEffect, useId, useRef, useState } from "react";
import { Popover } from "@radix-ui/themes";
import { Info, Warning, X } from "@phosphor-icons/react";
import type { Job } from "./types";
import "./run-notices.css";

type NoticeKind = "rerun" | "mixed";

export function RunNotices({ config, data }: Pick<Job, "config" | "data">) {
    const [inspected, setInspected] = useState<{ kind: NoticeKind; pinned: boolean } | null>(null);
    const anchor = useRef<HTMLButtonElement>(null);
    const root = useRef<HTMLDivElement>(null);
    const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const detailId = useId();
    const cancelClose = () => {
        if (closeTimer.current !== null) clearTimeout(closeTimer.current);
        closeTimer.current = null;
    };
    const close = () => { cancelClose(); setInspected(null); };
    const show = (node: HTMLButtonElement, kind: NoticeKind, pinned: boolean) => {
        cancelClose();
        anchor.current = node;
        setInspected(current => ({ kind, pinned: pinned || (current?.kind === kind && current.pinned) }));
    };
    const scheduleClose = () => {
        cancelClose();
        closeTimer.current = setTimeout(() => {
            closeTimer.current = null;
            setInspected(current => current?.pinned ? current : null);
        }, 120);
    };
    useEffect(() => () => {
        if (closeTimer.current !== null) clearTimeout(closeTimer.current);
    }, []);
    if (!config.rerun_run_id && data.protocol_status !== "mixed") return null;
    const notices: { kind: NoticeKind; label: string; accessibleLabel: string }[] = [];
    if (config.rerun_run_id) notices.push({ kind: "rerun", label: `T${config.rerun_from_turn} 分支重跑`, accessibleLabel: `T${config.rerun_from_turn} 分支重跑，查看来源` });
    if (data.protocol_status === "mixed") notices.push({ kind: "mixed", label: "跨版本", accessibleLabel: "跨版本执行，查看说明" });
    const detail = notices.find(notice => notice.kind === inspected?.kind);
    return <Popover.Root open={!!detail} onOpenChange={open => { if (!open) close(); }}>
        <Popover.Anchor virtualRef={anchor} />
        <div className="run-notices" ref={root} role="group" aria-label="重跑与版本信息">
            {notices.map(notice => <button type="button" key={notice.kind}
                className={`run-notice ${notice.kind === "mixed" ? "is-warning" : ""}`}
                aria-label={notice.accessibleLabel} aria-haspopup="dialog" aria-expanded={detail?.kind === notice.kind}
                aria-controls={detail?.kind === notice.kind ? detailId : undefined}
                onPointerEnter={event => { if (event.pointerType !== "touch") show(event.currentTarget, notice.kind, false); }}
                onPointerLeave={scheduleClose} onFocus={event => show(event.currentTarget, notice.kind, true)}
                onClick={event => show(event.currentTarget, notice.kind, true)}>
                {notice.label}{notice.kind === "mixed" ? <Warning size={13} aria-hidden="true" /> : <Info size={13} aria-hidden="true" />}
            </button>)}
        </div>
        {detail && <Popover.Content id={detailId} aria-label={`${detail.label}详情`} className="run-notice-popover" size="1"
            side="bottom" align="start" sideOffset={5} collisionPadding={12}
            onOpenAutoFocus={event => event.preventDefault()} onCloseAutoFocus={event => event.preventDefault()}
            onInteractOutside={event => {
                if (event.target instanceof Element && root.current?.contains(event.target)) event.preventDefault();
            }}
            onPointerEnter={cancelClose} onPointerLeave={scheduleClose}>
            <header><strong>{detail.label}</strong><Popover.Close><button type="button" aria-label="关闭运行信息"><X size={14} /></button></Popover.Close></header>
            <p>{detail.kind === "rerun"
                ? `从 T${config.rerun_from_turn} 开局分支重跑，来源：${config.rerun_run_id}。原记录保持不变。`
                : "这次运行续跑时使用了不同版本的游戏规则。"}</p>
        </Popover.Content>}
    </Popover.Root>;
}
