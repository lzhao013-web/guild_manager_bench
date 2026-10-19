import type { ReactNode } from "react";
import {
    CircleNotch,
    CheckCircle,
    WarningCircle,
    PauseCircle,
    Clock,
    StopCircle,
} from "@phosphor-icons/react";
import type { Job } from "./types";
import { STATUS } from "./model";

export function StatusBadge({ status }: { status: Job["status"] }) {
    const Icon =
        status === "completed"
            ? CheckCircle
            : status === "failed"
              ? WarningCircle
              : status === "interrupted"
                ? PauseCircle
                : status === "queued"
                  ? Clock
                  : status === "stopping"
                    ? StopCircle
                    : CircleNotch;
    return (
        <span className={`status status-${status}`}>
            <Icon size={14} weight="bold" />
            {STATUS[status]}
        </span>
    );
}
export function Empty({
    title,
    children,
    action,
}: {
    title: string;
    children?: ReactNode;
    action?: ReactNode;
}) {
    return (
        <div className="empty">
            <div className="empty-mark">
                <CircleNotch size={30} />
            </div>
            <h3>{title}</h3>
            <p>{children}</p>
            {action}
        </div>
    );
}
export function Metric({
    label,
    value,
    note,
}: {
    label: string;
    value: ReactNode;
    note?: string;
}) {
    return (
        <div className="metric">
            <span>{label}</span>
            <strong>{value}</strong>
            {note && <small>{note}</small>}
        </div>
    );
}
export function Trend({
    points,
    large = false,
}: {
    points: Job["scores"];
    large?: boolean;
}) {
    if (!points.length)
        return <span className="muted chart-empty">尚无评分</span>;
    const width = large ? 600 : 100,
        height = large ? 130 : 28;
    const values = points.map((p) => p.value);
    const min = Math.min(...values),
        max = Math.max(...values);
    const turns = points.map((p) => p.turn),
        from = Math.min(...turns),
        to = Math.max(...turns);
    const coords = points
        .map(
            (p) =>
                `${5 + ((p.turn - from) / (to - from || 1)) * (width - 10)},${height - 5 - ((p.value - min) / (max - min || 1)) * (height - 10)}`,
        )
        .join(" ");
    return (
        <svg
            className={large ? "trend trend-large" : "trend"}
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label={`已记录评分趋势，${points.length} 个回合，最新 ${values.at(-1)}`}
        >
            <polyline
                points={coords}
                fill="none"
                stroke="currentColor"
                strokeWidth={large ? 2 : 1.5}
                vectorEffect="non-scaling-stroke"
            />
            {points.map((p, i) => (
                <circle
                    key={p.turn}
                    cx={5 + ((p.turn - from) / (to - from || 1)) * (width - 10)}
                    cy={
                        height -
                        5 -
                        ((p.value - min) / (max - min || 1)) * (height - 10)
                    }
                    r={large ? 3 : 1.5}
                >
                    <title>{`T${p.turn}: ${p.value}`}</title>
                </circle>
            ))}
        </svg>
    );
}
export function JsonDetails({
    title,
    value,
}: {
    title: string;
    value: unknown;
}) {
    return (
        <details className="json-details">
            <summary>{title}</summary>
            <pre>{JSON.stringify(value, null, 2)}</pre>
        </details>
    );
}
