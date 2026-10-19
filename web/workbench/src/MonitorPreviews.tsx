import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowsOutSimple, Backpack, ListBullets, Storefront, TreeStructure } from "@phosphor-icons/react";
import { businessPreviews, previewProgress } from "./businessModel";
import { fmt } from "./model";
import { Portrait, SlotIcon } from "./SceneParts";
import { actionSummary } from "./actionModel";
import type { ActionEntry } from "./actionModel";
import type { WatchFocus } from "./sceneNavigation";
import type { Observation } from "./types";
import type { WatchFrame } from "./watchModel";

interface PreviewLayout { tiles: boolean; columns: number; rows: number; details: boolean; inlineProgress: boolean; roomy: boolean }
function layoutFor(width: number, height: number, cards: number): PreviewLayout {
    const columns = Math.min(2, cards);
    const tileHeight = (height - 6 * (Math.ceil(cards / columns) - 1)) / Math.ceil(cards / columns);
    const tiles = width / columns >= 140 && tileHeight >= 48;
    if (!tiles && cards > 1 && height >= 74 && width / Math.min(3, cards - 1) >= 140)
        return { tiles: true, columns: Math.min(3, cards - 1), rows: 1, details: false, inlineProgress: true, roomy: false };
    const details = tileHeight >= 105;
    const roomy = tileHeight >= 240 && width / columns >= 320;
    return { tiles, columns, details, roomy, inlineProgress: false, rows: Math.min(8, Math.max(1, Math.floor((tileHeight - (roomy ? 36 : 28)) / (roomy ? 48 : details ? 36 : 20)))) };
}

export function MonitorPreviews({ observation, frame, frames, onFrame, onFocus }: {
    observation: Observation | null;
    frame: WatchFrame | undefined;
    frames: WatchFrame[];
    onFrame: (frame: WatchFrame) => void;
    onFocus?: (target?: WatchFocus) => void;
}) {
    const { groups, opportunities } = useMemo(() => businessPreviews(observation), [observation]);
    const progress = useMemo(() => previewProgress(frames, frame), [frames, frame]);
    const root = useRef<HTMLDivElement>(null);
    const [layout, setLayout] = useState<PreviewLayout>(() => layoutFor(0, 0, groups.length + 1));
    useLayoutEffect(() => {
        const node = root.current!;
        function measure() {
            const box = node.getBoundingClientRect();
            const next = layoutFor(box.width, box.height, groups.length + 1);
            setLayout(previous => Object.keys(next).every(key => next[key as keyof PreviewLayout] === previous[key as keyof PreviewLayout]) ? previous : next);
        }
        measure();
        const observer = new ResizeObserver(measure);
        observer.observe(node);
        return () => observer.disconnect();
    }, [groups.length]);
    const action = (item: ActionEntry, current = false) => <button key={item.frame.cursor} className={`${current ? "monitor-current" : ""} ${item.status === "failed" ? "failed" : ""}`}
        aria-label={`定位行动 ${item.title}`} title={`${item.title} ${actionSummary(item)}`} onClick={() => onFrame(item.frame)}>
        <span className="monitor-row-label">{item.rolledBack ? "已回滚" : item.status === "failed" ? "失败" : item.status === "pending" ? "执行中" : item.kind === "system" ? "提示" : item.kind === "query" ? "查询" : "行动"}</span><strong>{item.title}</strong><span className="monitor-action-summary">{actionSummary(item)}</span>
    </button>;
    const previewActions = progress.actions.length ? progress.actions : progress.query ? [progress.query] : [];
    const queryNote = layout.tiles && !layout.inlineProgress && layout.rows > 1 && (!progress.actions.length || progress.queryOnly);
    const actionLimit = layout.tiles && !layout.inlineProgress ? Math.max(1, layout.rows - (queryNote ? 1 : 0)) : 1;
    const actions = <section className="monitor-actions" aria-label="行动摘要">
        {previewActions.slice(0, actionLimit).map((item, index) => action(item, index === 0))}
        {queryNote && <p className="business-empty">{progress.queries ? `目前只进行了 ${progress.queries} 次查询，尚无经营动作` : "该位置尚无经营动作记录"}</p>}
        <button className="monitor-section-link monitor-action-link" aria-label="查看完整行动" disabled={!onFocus} onClick={() => onFocus?.({ module: "actions" })}><ArrowsOutSimple size={12} /></button>
    </section>;
    return <div ref={root} className="monitor-previews" data-mode={layout.tiles ? "tiles" : "summary"} aria-label="经营预览">
        {!layout.tiles ? <div className="monitor-preview-summary">
            <div className="monitor-opportunities" aria-label="当前观看位置的经营机会">
                {opportunities.map(item => <button key={item.label} title={`${item.label}：${item.count === null ? "该位置未记录" : `${item.count} 项，点击查看详情`}`}
                    aria-label={`查看${item.label}概况`} disabled={item.count === null || !onFocus} onClick={() => onFocus?.(item.target)}>
                    {item.label}<b>{item.count ?? "未记录"}</b>
                </button>)}
            </div>
            {actions}
        </div> : <div className={`monitor-preview-grid ${layout.details ? "with-details" : ""} ${layout.inlineProgress ? "inline-progress" : ""} ${layout.roomy ? "is-roomy" : ""}`} data-cards={groups.length + 1} style={{ gridTemplateColumns: `repeat(${layout.columns}, minmax(0, 1fr))` }}>
            {groups.map(group => <section className={`monitor-preview-card business-${group.id}`} key={group.id} aria-label={`${group.title}预览`}>
                <button className="business-heading" disabled={!onFocus} onClick={() => onFocus?.(group.target)}>
                    {group.id === "recruitment" ? <Storefront size={13} /> : group.id === "upgrades" ? <TreeStructure size={13} /> : <Backpack size={13} />}
                    <h3>{group.title}</h3><small title={group.note}>{group.note}</small><ArrowsOutSimple size={11} />
                </button>
                <div className="business-rows">
                    {group.rows.slice(0, layout.rows).map(row => <button className={`business-row ${row.available ? "is-available" : ""}`} key={row.id}
                        aria-label={`查看${row.target.selection?.kind === "recipe" ? "制作配方" : row.target.selection?.kind === "candidate" ? "招募候选" : row.target.selection?.kind === "upgrade" ? "升级" : "装备"} ${row.name}`}
                        title={`${row.name} · ${row.value}\n${row.detail}`} disabled={!onFocus} onClick={() => onFocus?.(row.target)}>
                        {row.portrait ? <Portrait id={row.portrait} name={row.name} /> : row.slot ? <SlotIcon slot={row.slot} size={16} /> : <TreeStructure size={14} />}
                        <span><span className="business-row-main"><strong>{row.name}</strong><b>{row.value}</b></span>
                            {layout.details && row.detail && <small>{row.detail}</small>}</span>
                    </button>)}
                </div>
            </section>)}
            <section className={`monitor-preview-card business-progress ${layout.rows === 1 ? "is-tight" : ""}`} aria-label="经营进展预览">
                <div className="business-heading"><ListBullets size={13} /><h3>经营进展</h3><small>{progress.queryOnly ? "仅查询 " : ""}{progress.queries} 次查询{frame?.score != null ? ` · 已记评分 ${fmt(frame.score)}` : ""}</small></div>
                {actions}
            </section>
        </div>}
    </div>;
}
