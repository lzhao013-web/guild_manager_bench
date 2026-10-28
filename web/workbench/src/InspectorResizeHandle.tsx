import { useEffect, useRef } from "react";
import type { PointerEvent } from "react";

const MIN_WIDTH = 340;
const MAX_WIDTH = 600;
const clamp = (width: number) => Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.round(width)));

type Drag = { pointerId: number; x: number; initial: number; width: number; layout: HTMLElement };

export function InspectorResizeHandle({ width, onChange }: { width: number; onChange: (width: number) => void }) {
    const drag = useRef<Drag | null>(null);
    useEffect(() => () => {
        const current = drag.current;
        if (current) {
            current.layout.style.setProperty("--inspector-width", `${current.initial}px`);
            current.layout.classList.remove("is-resizing");
            drag.current = null;
        }
    }, []);

    function finish(event: PointerEvent<HTMLDivElement>, cancel: boolean) {
        const current = drag.current;
        if (!current || current.pointerId !== event.pointerId) return;
        drag.current = null;
        const next = cancel ? current.initial : current.width;
        current.layout.style.setProperty("--inspector-width", `${next}px`);
        current.layout.classList.remove("is-resizing");
        event.currentTarget.setAttribute("aria-valuenow", String(next));
        if (!cancel) onChange(next);
    }

    return <div className="inspector-resize-handle" role="separator" tabIndex={0}
        aria-label="调整详情面板宽度" aria-orientation="vertical" aria-controls="run-inspector"
        aria-valuemin={MIN_WIDTH} aria-valuemax={MAX_WIDTH} aria-valuenow={width}
        title="拖动调整宽度"
        onPointerDown={event => {
            if (event.button !== 0 || drag.current) return;
            event.preventDefault();
            event.currentTarget.focus({ preventScroll: true });
            const layout = event.currentTarget.closest<HTMLElement>(".runs-layout")!;
            drag.current = { pointerId: event.pointerId, x: event.clientX, initial: width, width, layout };
            event.currentTarget.setPointerCapture(event.pointerId);
            layout.classList.add("is-resizing");
        }}
        onPointerMove={event => {
            const current = drag.current;
            if (!current || current.pointerId !== event.pointerId) return;
            current.width = clamp(current.initial + current.x - event.clientX);
            current.layout.style.setProperty("--inspector-width", `${current.width}px`);
            event.currentTarget.setAttribute("aria-valuenow", String(current.width));
        }}
        onPointerUp={event => finish(event, false)}
        onPointerCancel={event => finish(event, true)}
        onLostPointerCapture={event => finish(event, true)}
        onKeyDown={event => {
            const next = event.key === "ArrowLeft" ? width + 16
                : event.key === "ArrowRight" ? width - 16
                : event.key === "Home" ? MIN_WIDTH
                : event.key === "End" ? MAX_WIDTH : null;
            if (next === null) return;
            event.preventDefault();
            onChange(clamp(next));
        }} />;
}
