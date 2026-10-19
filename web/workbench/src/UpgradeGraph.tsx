import { useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent } from "react";
import {
    ArrowsInSimple, Check, Coins, Crosshair, GitBranch, Lock,
    MagnifyingGlass, Minus, Plus, Prohibit, TreeStructure,
} from "@phosphor-icons/react";
import { layoutTalents, TALENT_STATES, talentRoute, talentState } from "./talentModel";
import type { Talent, TalentState } from "./talentModel";
import type { GameEntity } from "./types";
import "./talent.css";

const STATE_ICONS = {
    unlocked: Check, available: Plus, prerequisite: GitBranch,
    resources: Coins, exclusive: Prohibit, locked: Lock,
};
export function TalentStatus({ state }: { state: TalentState }) {
    const Icon = STATE_ICONS[state];
    return <span className={`talent-status ${state}`}><Icon size={12} />{TALENT_STATES[state]}</span>;
}

export function UpgradeGraph({ upgrades, selectedId, onSelect, active = true }: {
    upgrades: GameEntity[];
    selectedId: string | null;
    onSelect: (upgrade: GameEntity) => void;
    active?: boolean;
}) {
    const talents = upgrades as Talent[];
    const layout = useMemo(() => layoutTalents(talents), [upgrades]);
    const byId = useMemo(() => new Map(layout.nodes.map((node) => [node.talent.upgrade_id, node])), [layout]);
    const [hovered, setHovered] = useState<string | null>(null);
    // 已点选的路线优先，鼠标离开或焦点转到详情后不会丢失。
    const focus = selectedId || hovered;
    const route = useMemo(() => talentRoute(talents, focus), [upgrades, focus]);
    const [query, setQuery] = useState("");
    const matches = query.trim() ? talents.filter((talent) =>
        `${talent.name} ${talent.upgrade_id}`.toLowerCase().includes(query.trim().toLowerCase())) : [];
    const [zoom, setZoom] = useState(1);
    const viewport = useRef<HTMLDivElement>(null);
    const surface = useRef<HTMLDivElement>(null);
    const initialFit = useRef(false);
    const lastSelected = useRef<string | null>(null);
    const scroll = useRef({ x: 0, y: 0 });
    const pendingCenter = useRef<{ x: number; y: number; zoom: number } | null>(null);
    const drag = useRef<{ id: number; x: number; y: number; left: number; top: number } | null>(null);
    const marker = useId().replace(/:/g, "");

    function centerViewport(center: { x: number; y: number }, scale: number) {
        const element = viewport.current!;
        element.scrollLeft = surface.current!.offsetLeft + center.x * scale - element.clientWidth / 2;
        element.scrollTop = center.y * scale - element.clientHeight / 2;
        scroll.current = { x: element.scrollLeft, y: element.scrollTop };
    }
    function changeScale(scale: number, center: { x: number; y: number }) {
        if (scale === zoom) {
            pendingCenter.current = null;
            centerViewport(center, scale);
        } else {
            pendingCenter.current = { ...center, zoom: scale };
            setZoom(scale);
        }
    }
    function locate(id: string) {
        const node = byId.get(id)!;
        const element = viewport.current!;
        element.scrollLeft = surface.current!.offsetLeft + (node.x + node.width / 2) * zoom - element.clientWidth / 2;
        element.scrollTop = (node.y + node.height / 2) * zoom - element.clientHeight / 2;
        scroll.current = { x: element.scrollLeft, y: element.scrollTop };
    }
    function setScale(next: number) {
        const element = viewport.current!;
        const scale = Math.max(0.25, Math.min(1.5, Math.round(next * 100) / 100));
        changeScale(scale, {
            x: (element.scrollLeft + element.clientWidth / 2 - surface.current!.offsetLeft) / zoom,
            y: (element.scrollTop + element.clientHeight / 2) / zoom,
        });
    }
    function fit() {
        const element = viewport.current!;
        const scale = Math.max(0.25, Math.min(1, (element.clientWidth - 16) / layout.width, (element.clientHeight - 16) / layout.height));
        changeScale(scale, { x: layout.width / 2, y: layout.height / 2 });
    }
    useLayoutEffect(() => {
        const element = viewport.current;
        if (!element) return;
        function measure() {
            if (!active || initialFit.current || !element!.clientWidth || !element!.clientHeight) return;
            initialFit.current = true;
            // 首次显示保持可读字号。更小的全图缩放由用户主动选择。
            const scale = Math.max(0.8, Math.min(1, (element!.clientWidth - 16) / layout.width, (element!.clientHeight - 16) / layout.height));
            const center = selectedId && byId.has(selectedId)
                ? { x: byId.get(selectedId)!.x + byId.get(selectedId)!.width / 2, y: byId.get(selectedId)!.y + byId.get(selectedId)!.height / 2 }
                : { x: layout.width / 2, y: layout.height / 2 };
            changeScale(scale, center);
        }
        measure();
        const observer = new ResizeObserver(measure);
        observer.observe(element);
        return () => observer.disconnect();
    }, [active, layout.width, layout.height]);
    useLayoutEffect(() => {
        const element = viewport.current;
        if (!element || !active) return;
        if (pendingCenter.current?.zoom === zoom) {
            const center = pendingCenter.current;
            centerViewport(center, zoom);
            pendingCenter.current = null;
        }
    }, [zoom, active]);
    useLayoutEffect(() => {
        const element = viewport.current;
        if (!element || !active) return;
        element.scrollLeft = scroll.current.x;
        element.scrollTop = scroll.current.y;
        const node = selectedId ? byId.get(selectedId) : undefined;
        const changed = lastSelected.current !== selectedId;
        lastSelected.current = selectedId;
        if (changed && node) {
            const x = surface.current!.offsetLeft + node.x * zoom, y = node.y * zoom;
            if (x < element.scrollLeft || x + node.width * zoom > element.scrollLeft + element.clientWidth ||
                y < element.scrollTop || y + node.height * zoom > element.scrollTop + element.clientHeight) locate(selectedId!);
        }
    }, [active, selectedId]);
    function startDrag(event: PointerEvent<HTMLDivElement>) {
        if (event.button !== 0 || event.pointerType === "touch" || (event.target as HTMLElement).closest("button")) return;
        const element = event.currentTarget;
        drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, left: element.scrollLeft, top: element.scrollTop };
        element.setPointerCapture(event.pointerId);
        element.classList.add("is-dragging");
    }
    function finishDrag(event: PointerEvent<HTMLDivElement>) {
        if (!drag.current) return;
        event.currentTarget.classList.remove("is-dragging");
        drag.current = null;
    }
    function searchNext() {
        if (!matches.length) return;
        const index = matches.findIndex((talent) => talent.upgrade_id === selectedId);
        const next = matches[(index + 1) % matches.length];
        onSelect(next);
        locate(next.upgrade_id);
    }
    if (!talents.length) return <p className="scene-zone-empty">此时没有升级记录</p>;
    return (
        <div className="talent-explorer">
            <div className="talent-toolbar">
                <form className="talent-search" onSubmit={(event) => { event.preventDefault(); searchNext(); }}>
                    <MagnifyingGlass size={15} />
                    <input aria-label="搜索天赋" placeholder="搜索天赋名称" value={query} onChange={(event) => setQuery(event.target.value)} />
                    <span role="status">{query.trim() ? `${matches.length} 个匹配` : `${talents.length} 个节点`}</span>
                    <button type="submit" disabled={!matches.length} title="回车逐个定位匹配节点">定位</button>
                </form>
                <div className="talent-zoom">
                    <button aria-label="缩小天赋图" disabled={zoom <= 0.25} onClick={() => setScale(zoom - 0.1)}><Minus size={14} /></button>
                    <button className="talent-zoom-value mono" aria-label="天赋图原始大小" onClick={() => setScale(1)}>{Math.round(zoom * 100)}%</button>
                    <button aria-label="放大天赋图" disabled={zoom >= 1.5} onClick={() => setScale(zoom + 0.1)}><Plus size={14} /></button>
                    <button disabled={!selectedId} onClick={() => locate(selectedId!)}><Crosshair size={14} />定位选中</button>
                    <button onClick={fit}><ArrowsInSimple size={14} />适应全图</button>
                </div>
            </div>
            <div className="talent-legend" aria-label="天赋图例">
                <span><i className="talent-line-sample" />前置</span>
                <span><i className="talent-line-sample exclusive" />互斥</span>
                <TalentStatus state="unlocked" /><TalentStatus state="available" />
                <TalentStatus state="resources" /><TalentStatus state="prerequisite" /><TalentStatus state="exclusive" />
                <span className="talent-hint">{focus ? "突出当前节点的全部前置路线" : "点选看路线，拖动空白处平移"}</span>
            </div>
            <div className="talent-scrollport" ref={viewport} role="region" tabIndex={0} aria-label="天赋路线画布"
                onScroll={(event) => { if (active) scroll.current = { x: event.currentTarget.scrollLeft, y: event.currentTarget.scrollTop }; }}
                onPointerDown={startDrag}
                onPointerMove={(event) => {
                    if (!drag.current || drag.current.id !== event.pointerId) return;
                    event.currentTarget.scrollLeft = drag.current.left - event.clientX + drag.current.x;
                    event.currentTarget.scrollTop = drag.current.top - event.clientY + drag.current.y;
                }}
                onPointerUp={finishDrag} onPointerCancel={finishDrag} onLostPointerCapture={finishDrag}>
                <div className="talent-surface" ref={surface} style={{ width: layout.width * zoom, height: layout.height * zoom }}>
                    <div className={`scene-upgrade-tree ${focus ? "has-focus" : ""}`} style={{ width: layout.width, height: layout.height, transform: `scale(${zoom})` }}>
                        {layout.lanes.map((lane) => <div key={lane.id} className="talent-lane" style={{ left: lane.x - 6, width: lane.width + 12, height: layout.height - 16 }}>
                            <h4><TreeStructure size={13} />{lane.label}</h4>
                        </div>)}
                        {layout.rows.map((row) => <div key={row.level} className="talent-level" style={{ top: row.y, height: row.height }}>
                            <span>{layout.branched ? `第 ${row.level} 层` : `前置 ${row.level - 1}`}</span>
                        </div>)}
                        <svg className="upgrade-connections" aria-hidden="true">
                            <defs><marker id={marker} viewBox="0 0 6 6" refX="5" refY="3" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><polygon points="0,0 6,3 0,6" /></marker></defs>
                            {layout.edges.map((edge) => {
                                const highlighted = edge.kind === "dependency" ? route.edges.has(`${edge.from}>${edge.to}`) : edge.from === focus || edge.to === focus;
                                return <path key={`${edge.kind}:${edge.from}:${edge.to}`} d={edge.path}
                                    data-from={edge.from} data-to={edge.to}
                                    className={`talent-edge ${edge.kind} ${highlighted ? "highlighted" : ""}`}
                                    markerEnd={edge.kind === "dependency" ? `url(#${marker})` : undefined} />;
                            })}
                        </svg>
                        {layout.nodes.map((node) => {
                            const talent = node.talent, id = talent.upgrade_id, state = talentState(talent);
                            const names = talent.required_upgrade_ids.map((parent) => byId.get(parent)?.talent.name || parent);
                            const description = `${TALENT_STATES[state]}。${names.length ? `前置需要全部满足：${names.join("、")}` : "无前置升级"}`;
                            const related = id === focus || route.ancestors.has(id) || route.exclusive.has(id);
                            return <button key={id} data-upgrade-id={id}
                                className={`scene-upgrade-node ${state} ${id === selectedId ? "is-selected" : ""} ${route.ancestors.has(id) ? "is-route" : ""} ${route.exclusive.has(id) ? "is-exclusive" : ""} ${focus && !related ? "is-muted" : ""} ${matches.some((match) => match.upgrade_id === id) ? "is-match" : ""}`}
                                style={{ left: node.x, top: node.y, width: node.width, height: node.height }}
                                aria-label={`查看升级 ${talent.name}`} aria-pressed={id === selectedId} title={description}
                                onClick={() => onSelect(talent)}
                                onMouseEnter={() => setHovered(id)} onMouseLeave={() => setHovered(null)}
                                onFocus={() => setHovered(id)} onBlur={() => setHovered(null)}>
                                <strong>{talent.name}</strong><TalentStatus state={state} />
                            </button>;
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
}
