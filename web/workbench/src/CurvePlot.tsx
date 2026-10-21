import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Popover } from "@radix-ui/themes";
import { curveMilestoneGroups, curveSegments } from "./curveModel";
import type { CurveData, CurveMetric, CurveMilestone } from "./curveModel";
import { short } from "./model";

export interface CurveSeries { key: string; number: number; name: string; color: string; data: CurveData }
function eventLabel(event: CurveMilestone): string {
    return event.kind === "expansion" ? event.label.replace("解锁扩编 ", "扩")
        : event.kind === "level_9" ? "9" : event.kind === "boss_victory" ? "首" : "挑";
}
export function CurvePlot({ title, note, metric, series, from, to, turn, onTurn }: {
    title: string; note: string; metric: CurveMetric; series: CurveSeries[];
    from: number; to: number; turn: number; onTurn: (turn: number) => void;
}) {
    const root = useRef<HTMLDivElement>(null);
    const tracksRoot = useRef<HTMLDivElement>(null);
    const nodeAnchor = useRef<HTMLButtonElement>(null);
    const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const detailId = useId();
    const [inspected, setInspected] = useState<{ run: string; turn: number; pinned: boolean } | null>(null);
    const signature = JSON.stringify(series.map(run => run.key));
    const cancelClose = () => { if (closeTimer.current !== null) clearTimeout(closeTimer.current); };
    const closeDetail = () => { cancelClose(); setInspected(null); };
    const showDetail = (node: HTMLButtonElement, run: string, number: number, pinned: boolean) => {
        cancelClose();
        nodeAnchor.current = node;
        setInspected({ run, turn: number, pinned });
        onTurn(number);
    };
    const scheduleClose = () => {
        cancelClose();
        closeTimer.current = setTimeout(() => setInspected(current => current?.pinned ? current : null), 120);
    };
    useEffect(() => {
        setInspected(null);
        return () => { if (closeTimer.current !== null) clearTimeout(closeTimer.current); };
    }, [metric, from, to, signature]);
    const [{ width, height }, setSize] = useState({ width: 400, height: 190 });
    useLayoutEffect(() => {
        const node = root.current!;
        const measure = () => {
            const bounds = node.getBoundingClientRect();
            const next = { width: Math.max(160, bounds.width), height: Math.max(140, bounds.height) };
            setSize(previous => previous.width === next.width && previous.height === next.height ? previous : next);
        };
        measure();
        const observer = new ResizeObserver(measure);
        observer.observe(node);
        return () => observer.disconnect();
    }, []);
    const plots = useMemo(() => series.map(run => ({ ...run, segments: curveSegments(run.data.points, metric, from, to),
        milestones: curveMilestoneGroups(run.data, metric, from, to) })), [series, metric, from, to]);
    const values = plots.flatMap(run => run.segments.flatMap(segment => segment.map(point => point[metric]!)));
    const min = Math.min(0, ...values), max = Math.max(1, ...values);
    const left = 53, right = width - 14, top = 19, bottom = height - 32;
    const x = (value: number) => from === to ? (left + right) / 2 : left + (value - from) / (to - from) * (right - left);
    const y = (value: number) => bottom - (value - min) / (max - min) * (bottom - top);
    const tickCount = width < 380 ? 3 : 5;
    const ticks = [...new Set(Array.from({ length: tickCount }, (_, i) => Math.round(from + (to - from) * i / (tickCount - 1))))];
    const tracks = plots.map(run => ({ ...run, nodes: run.milestones.map((group, index, all) => {
        const clearance = Math.min(index ? x(group.turn) - x(all[index - 1].turn) : Infinity,
            index + 1 < all.length ? x(all[index + 1].turn) - x(group.turn) : Infinity);
        return { ...group, labels: [...new Set(group.events.map(eventLabel))], compact: clearance < 28, hitWidth: Math.min(28, clearance),
            value: run.data.points.find(point => point.turn === group.turn)?.[metric],
            description: `${run.number}号 ${run.name} / T${group.turn}：${group.events.map(event => event.detail).join("；")}` };
    }) }));
    const groups = tracks.flatMap(run => run.nodes.map(group => ({ ...group, run })));
    const detail = groups.find(group => group.run.key === inspected?.run && group.turn === inspected.turn);
    const detailWidth = Math.min(340, width - 16);
    const anchorLeft = detail ? x(detail.turn) - detail.hitWidth / 2 : 0;
    const detailOffset = Math.max(8, Math.min(anchorLeft, width - detailWidth - 8)) - anchorLeft;
    const legend = metric === "rank_score" ? "首=首领 / 挑=挑战" : metric.startsWith("gold") ? "扩1 / 扩2" : "9=达到 Lv9";
    return <section className="curve-chart" aria-label={`${title}曲线`}>
        <header><h2>{title}</h2><span>{note}</span><small className="curve-marker-legend">{legend}</small></header>
        <div ref={root} className="curve-plot">
            <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}，横轴为真实回合，方向键移动共同定位回合`}
                tabIndex={0} onKeyDown={event => {
                    if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); onTurn(Math.max(from, Math.min(to, turn + (event.key === "ArrowLeft" ? -1 : 1)))); }
                }} onPointerMove={event => {
                    const bounds = event.currentTarget.getBoundingClientRect();
                    const px = (event.clientX - bounds.left) / bounds.width * width;
                    onTurn(Math.max(from, Math.min(to, Math.round(from + (px - left) / (right - left) * (to - from)))));
                }}>
                {[min, (min + max) / 2, max].map(value => <g key={value}>
                    <line className="curve-grid-line" x1={left} x2={right} y1={y(value)} y2={y(value)} />
                    <text className="curve-axis" x={left - 8} y={y(value) + 4} textAnchor="end">{short(value)}</text>
                </g>)}
                {ticks.map(value => <text className="curve-axis" key={value} x={x(value)} y={bottom + 20} textAnchor="middle">{value}</text>)}
                <line className="curve-cursor" x1={x(turn)} x2={x(turn)} y1={top} y2={bottom} />
                {plots.map(run => <g key={run.key} style={{ color: run.color }}>
                    {run.segments.map((segment, index) => <polyline key={index} points={segment.map(point => `${x(point.turn)},${y(point[metric]!)}`).join(" ")}
                        fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />)}
                    {run.segments.flatMap(segment => segment.filter(point => point.turn === turn || segment.length === 1)).map(point => <circle key={point.turn}
                        cx={x(point.turn)} cy={y(point[metric]!)} r={point.turn === turn ? 3 : 2} data-selected={point.turn === turn}
                        fill={point.turn === turn ? "var(--gray-2)" : "currentColor"} stroke="currentColor" strokeWidth={point.turn === turn ? 1.5 : 0}>
                        <title>{run.name} / 回合 {point.turn}：{point[metric]}</title>
                    </circle>)}
                </g>)}
                {!values.length && <text className="curve-axis curve-empty-text" x={(left + right) / 2} y={(top + bottom) / 2} textAnchor="middle">该范围尚无已记录数据</text>}
            </svg>
        </div>
        <Popover.Root open={!!detail} onOpenChange={open => { if (!open) closeDetail(); }}>
        <Popover.Anchor virtualRef={nodeAnchor} />
        {groups.length ? <div ref={tracksRoot} className="curve-event-tracks" role="group" aria-label={`${title}事件轨道`}>
            {tracks.map(run => <div className="curve-event-row" key={run.key} role="group" aria-label={`${run.number}号 ${run.name}事件`} style={{ color: run.color }}>
                <span className="curve-event-run" title={`${run.number}号 ${run.name}`}>{run.number}</span>
                <span className="curve-event-baseline" style={{ left, right: width - right }} aria-hidden="true" />
                <span className="curve-event-cursor" style={{ left: x(turn) }} aria-hidden="true" />
                {!run.nodes.length && <span className="curve-event-empty" style={{ marginLeft: left }}>无已记录节点</span>}
                {run.nodes.map(group => <button type="button" key={group.turn} className="curve-event-node"
                    style={{ left: x(group.turn), width: group.hitWidth }} aria-label={`轨道节点 ${title} ${group.description}`}
                    aria-pressed={group.turn === turn} data-event-count={group.events.length} data-compact={group.compact}
                    aria-haspopup="dialog" aria-expanded={detail?.run.key === run.key && detail.turn === group.turn}
                    aria-controls={detail?.run.key === run.key && detail.turn === group.turn ? detailId : undefined}
                    onPointerEnter={event => showDetail(event.currentTarget, run.key, group.turn, false)} onPointerLeave={scheduleClose}
                    onFocus={event => showDetail(event.currentTarget, run.key, group.turn, true)}
                    onClick={event => showDetail(event.currentTarget, run.key, group.turn, true)}>
                    {group.compact ? <span className="curve-event-ticks" aria-hidden="true">{group.labels.map(label => <i className={`curve-event-tick ${label === "挑" ? "is-challenge" : ""}`} key={label} />)}</span>
                        : <span aria-hidden="true">{group.labels.join("")}</span>}
                </button>)}
            </div>)}
        </div> : <p className="curve-events-empty">当前范围无已记录节点</p>}
        {detail && <Popover.Content id={detailId} aria-label={`${title}节点详情`} className="curve-event-popover" size="1"
            side="bottom" align="start" sideOffset={3} alignOffset={detailOffset} avoidCollisions={false} style={{ width: detailWidth }}
            onOpenAutoFocus={event => event.preventDefault()} onCloseAutoFocus={event => event.preventDefault()}
            onInteractOutside={event => {
                if (event.target instanceof Element && event.target.closest(".curve-event-node") && tracksRoot.current?.contains(event.target)) event.preventDefault();
            }}
            onPointerEnter={cancelClose} onPointerLeave={scheduleClose}>
            <div className="curve-event-popover-heading"><strong style={{ color: detail.run.color }}>{detail.run.number}号 {detail.run.name} / T{detail.turn}</strong>
                <Popover.Close><button type="button" className="curve-event-popover-close" aria-label="关闭节点详情">关闭</button></Popover.Close></div>
            <ul>{detail.events.map(event => <li key={event.id}>{event.detail}</li>)}</ul>
            {detail.value == null && <p>该回合曲线数值未记录。</p>}
        </Popover.Content>}
        </Popover.Root>
        {!!groups.length && <details className="curve-milestone-list">
            <summary>节点详情 · {groups.reduce((total, group) => total + group.events.length, 0)} 件 · {new Set(groups.map(group => group.turn)).size} 回合</summary>
            <ul>{groups.map(group => <li key={`${group.run.key}:${group.turn}`}>
                <button type="button" style={{ color: group.run.color }} aria-label={`定位节点 ${group.description}`}
                    onClick={() => onTurn(group.turn)}>{group.description}{group.value == null && "（曲线数值未记录）"}</button>
            </li>)}</ul>
        </details>}
    </section>;
}
