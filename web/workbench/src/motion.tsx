import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { fmt } from "./model";
import type { Observation } from "./types";
import "./motion.css";

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

export function ResourceValue({ value, live, scope, label, children }: {
    value: number | null | undefined;
    live: boolean;
    scope: string;
    label: string;
    children: ReactNode;
}) {
    const previous = useRef({ value, live, scope });
    const [change, setChange] = useState<{ value: number; delta: number; scope: string } | null>(null);
    useEffect(() => {
        const before = previous.current;
        previous.current = { value, live, scope };
        if (!live || !before.live || scope !== before.scope || value == null || before.value == null || value === before.value) {
            setChange(null);
            return;
        }
        setChange({ value, delta: value - before.value, scope });
        const timer = window.setTimeout(() => setChange(null), 1600);
        return () => window.clearTimeout(timer);
    }, [value, live, scope]);
    const visible = live && change?.scope === scope && change.value === value ? change : null;
    return <span className="resource-value">
        <ChangeValue value={value}>{children}</ChangeValue>
        {visible && <span key={visible.value} className={`resource-delta ${visible.delta > 0 ? "is-gain" : "is-spend"}`}
            aria-hidden="true" title={`${label}本次更新净变化：${visible.delta > 0 ? "+" : ""}${fmt(visible.delta)}`}>
            {visible.delta > 0 ? "+" : ""}{fmt(visible.delta)}
        </span>}
    </span>;
}

// 对比已读取的对象状态，不把首次加载和退出历史回看当成新事件。
function useChangedKeys(values: Record<string, string>, enabled: boolean) {
    const previous = useRef({ values, enabled });
    const [changed, setChanged] = useState<ReadonlySet<string>>(new Set());
    const signature = JSON.stringify(values);
    useEffect(() => {
        const before = previous.current;
        previous.current = { values, enabled };
        const keys = enabled && before.enabled
            ? Object.keys(values).filter(key => values[key] !== before.values[key]) : [];
        setChanged(new Set(keys));
        if (!keys.length) return;
        const timer = window.setTimeout(() => setChanged(new Set()), 900);
        return () => window.clearTimeout(timer);
    }, [enabled, signature]);
    return enabled ? changed : new Set<string>();
}

export function useSceneChanges(observation: Observation | null | undefined, live: boolean) {
    const values: Record<string, string> = {};
    for (const member of observation?.adventurers || [])
        values[`member:${member.adventurer_id}`] = JSON.stringify([member.level, member.equipment_slots]);
    for (const item of observation?.equipment_inventory || []) {
        if (item.instance_id) values[`equipment:${item.instance_id}`] = JSON.stringify(item.equipped_by);
    }
    for (const upgrade of observation?.global_upgrades || []) {
        if (typeof upgrade.upgrade_id === "string") values[`upgrade:${upgrade.upgrade_id}`] = String(upgrade.unlocked);
    }
    return useChangedKeys(values, live && !!observation);
}

export function ModelActivity({ state }: { state: "waiting" | "receiving" }) {
    return <span className="model-activity" data-state={state} aria-hidden="true"><i /><i /><i /></span>;
}

export function SlidingNav({ activeKey, children }: { activeKey: string; children: ReactNode }) {
    const root = useRef<HTMLElement>(null);
    const marker = useRef<HTMLSpanElement>(null);
    useLayoutEffect(() => {
        const nav = root.current!;
        const indicator = marker.current!;
        function update() {
            const selected = nav.querySelector<HTMLElement>('[aria-current="page"]');
            indicator.hidden = !selected;
            if (!selected) return;
            indicator.style.width = `${selected.offsetWidth}px`;
            indicator.style.height = `${selected.offsetHeight}px`;
            indicator.style.transform = `translate(${selected.offsetLeft}px, ${selected.offsetTop}px)`;
        }
        update();
        const observer = new ResizeObserver(update);
        observer.observe(nav);
        return () => observer.disconnect();
    }, [activeKey]);
    return <nav className="sliding-nav" ref={root} aria-label="主导航">
        <span className="nav-selection" ref={marker} aria-hidden="true" hidden />
        {children}
    </nav>;
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
