import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { curveSegments } from "./curveModel";
import type { CurveData, CurveMetric } from "./curveModel";
import { short } from "./model";

export interface CurveSeries { key: string; name: string; color: string; data: CurveData }
export function CurvePlot({ title, note, metric, series, from, to, turn, onTurn }: {
    title: string; note: string; metric: CurveMetric; series: CurveSeries[];
    from: number; to: number; turn: number; onTurn: (turn: number) => void;
}) {
    const root = useRef<HTMLDivElement>(null);
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
    const plots = useMemo(() => series.map(run => ({ ...run, segments: curveSegments(run.data.points, metric, from, to) })), [series, metric, from, to]);
    const values = plots.flatMap(run => run.segments.flatMap(segment => segment.map(point => point[metric]!)));
    const min = Math.min(0, ...values), max = Math.max(1, ...values);
    const left = 53, right = width - 14, top = 19, bottom = height - 32;
    const x = (value: number) => from === to ? (left + right) / 2 : left + (value - from) / (to - from) * (right - left);
    const y = (value: number) => bottom - (value - min) / (max - min) * (bottom - top);
    const tickCount = width < 380 ? 3 : 5;
    const ticks = [...new Set(Array.from({ length: tickCount }, (_, i) => Math.round(from + (to - from) * i / (tickCount - 1))))];
    return <section className="curve-chart" aria-label={`${title}曲线`}>
        <header><h2>{title}</h2><span>{note}</span></header>
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
                    {run.segments.flat().map(point => <circle key={point.turn} cx={x(point.turn)} cy={y(point[metric]!)} r={point.turn === turn ? 4 : 2.5} fill="currentColor">
                        <title>{run.name} / 回合 {point.turn}：{point[metric]}</title>
                    </circle>)}
                </g>)}
                {!values.length && <text className="curve-axis curve-empty-text" x={(left + right) / 2} y={(top + bottom) / 2} textAnchor="middle">该范围尚无已记录数据</text>}
            </svg>
        </div>
    </section>;
}
