import { useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

/** 卡片内容区有独立尺寸，条目数量不会反过来撑高卡片。 */
export function OverviewItems<T>({ items, children, className, rowHeight, minItemWidth, gap = 6 }: {
    items: readonly T[];
    children: (item: T) => ReactNode;
    className: string;
    rowHeight: number;
    minItemWidth?: number;
    gap?: number;
}) {
    const root = useRef<HTMLDivElement>(null);
    const [layout, setLayout] = useState({ columns: 1, count: items.length });
    useLayoutEffect(() => {
        const node = root.current!;
        function measure() {
            const { width, height } = node.getBoundingClientRect();
            // 已展开其它模块时，总览仍挂载但不可见，等再次显示后测量。
            if (!width) return;
            const columnLimit = minItemWidth ? Math.max(1, Math.floor((width + gap) / (minItemWidth + gap))) : 1;
            const columns = items.length ? Math.ceil(items.length / Math.ceil(items.length / columnLimit)) : 1;
            const count = Math.min(items.length, columns * Math.max(0, Math.floor((height + gap) / (rowHeight + gap))));
            setLayout(previous => previous.columns === columns && previous.count === count ? previous : { columns, count });
        }
        measure();
        const observer = new ResizeObserver(measure);
        observer.observe(node);
        return () => observer.disconnect();
    }, [items.length, rowHeight, minItemWidth, gap]);
    const naturalRows = Math.ceil(Math.min(items.length, minItemWidth ? 12 : 6) / layout.columns);
    return <div ref={root} className={`overview-items ${className}`} style={{
        gridTemplateColumns: `repeat(${layout.columns}, minmax(0, 1fr))`,
        gridAutoRows: `${rowHeight}px`, gap,
        "--overview-natural-height": `${naturalRows * rowHeight + Math.max(0, naturalRows - 1) * gap}px`,
    } as CSSProperties}>
        {items.slice(0, layout.count).map(children)}
    </div>;
}
