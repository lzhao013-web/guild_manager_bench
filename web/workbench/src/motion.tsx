import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

const VALUE_MOTION_MS = 500;
const NEW_ITEM_MOTION_MS = 220;

function useChangeRevision(value: unknown) {
    const previous = useRef(value);
    const [revision, setRevision] = useState(0);
    useEffect(() => {
        if (Object.is(previous.current, value)) return;
        previous.current = value;
        setRevision(current => current + 1);
    }, [value]);
    return revision;
}

export function ChangeValue({ value, children, className = "", variant = "highlight" }: {
    value: unknown;
    children: ReactNode;
    className?: string;
    variant?: "highlight" | "enter";
}) {
    const revision = useChangeRevision(value);
    const [animating, setAnimating] = useState(false);
    useEffect(() => {
        if (!revision) return;
        setAnimating(true);
        const timer = window.setTimeout(() => setAnimating(false), VALUE_MOTION_MS);
        return () => window.clearTimeout(timer);
    }, [revision]);
    return <span
        className={`change-value motion-${variant} ${animating ? "is-changed" : ""} ${className}`}
        data-change-revision={revision || undefined}>
        {children}
    </span>;
}

export function useNewItemKeys(keys: readonly (string | number)[], enabled: boolean) {
    const known = useRef<Set<string | number> | undefined>(undefined);
    const [recent, setRecent] = useState<ReadonlySet<string | number>>(new Set());
    const signature = JSON.stringify(keys);
    useEffect(() => {
        const current = new Set(keys);
        const previous = known.current;
        known.current = current;
        if (!enabled) {
            setRecent(existing => existing.size ? new Set() : existing);
            return;
        }
        if (!previous) return;
        const added = keys.filter(key => !previous.has(key));
        if (!added.length) return;
        setRecent(new Set(added));
        const timer = window.setTimeout(() => {
            setRecent(existing => {
                const next = new Set(existing);
                for (const key of added) next.delete(key);
                return next;
            });
        }, NEW_ITEM_MOTION_MS);
        return () => window.clearTimeout(timer);
    }, [enabled, signature]);
    return recent;
}

export function animateModuleEnter(panel: HTMLElement, origin: DOMRect | null) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || typeof panel.animate !== "function") return;
    const target = panel.getBoundingClientRect();
    if (!target.width || !target.height) return;
    let transform = "translateY(4px)";
    let opacity = 0;
    if (origin && origin.width && origin.height) {
        const x = origin.left + origin.width / 2 - (target.left + target.width / 2);
        const y = origin.top + origin.height / 2 - (target.top + target.height / 2);
        const scale = Math.max(0.965, Math.min(0.99, origin.width / target.width));
        transform = `translate(${x}px, ${y}px) scale(${scale})`;
        opacity = 0.55;
    }
    return panel.animate([
        { opacity, transform },
        { opacity: 1, transform: "translate(0, 0) scale(1)" },
    ], {
        duration: 200,
        easing: "cubic-bezier(.2, .8, .2, 1)",
    });
}
