import { useMemo } from "react";
import { useTrajectories } from "./TrajectoryContext";
import { summarizeTrajectories } from "./trajectoryModel";
import type { OperationCategory, TrajectoryState } from "./trajectoryModel";

const STATES = [
    { id: "confirmed", label: "已确认成功" },
    { id: "uncommitted", label: "回合未提交" },
    { id: "failed", label: "操作失败" },
    { id: "rolled_back", label: "已回滚" },
] as const;

export function TrajectoryOverview({ type, state, onCategory, onState, onAll }: {
    type: string;
    state: TrajectoryState;
    onCategory: (category: OperationCategory) => void;
    onState: (state: TrajectoryState) => void;
    onAll: () => void;
}) {
    const history = useTrajectories()!;
    const entries = history.data?.entries;
    const summary = useMemo(() => summarizeTrajectories(entries || []), [entries]);
    const loaded = history.data !== undefined;
    const count = (value: number) => loaded ? value : "…";
    return <section className="trajectory-overview" aria-label="整局经营操作概览" aria-busy={!loaded && history.pending}>
        <header>
            <div className="trajectory-overview-heading"><h3>{history.data?.origin === "execution" ? "当前阶段操作概览" : "整局经营概览"}</h3>
                <button className="trajectory-total" disabled={!loaded} onClick={onAll} title="查看全部记录，并清除当前筛选">共 <b className="mono">{count(summary.total)}</b> 次尝试</button>
                <span className={`trajectory-coverage ${history.data?.origin === "execution" ? "is-partial" : ""}`}>
                    {history.error ? loaded ? "读取失败，保留上次结果" : "轨迹读取失败" : !loaded ? "正在读取…" : history.data!.origin === "execution" ? "仅当前运行阶段" : "整局已记录操作"}
                </span>
            </div>
            <div className="trajectory-state-totals" role="group" aria-label="按整局操作状态筛选">
                {STATES.map(item => <button key={item.id} className={`is-${item.id}`} aria-pressed={state === item.id}
                    aria-label={`查看${item.label}记录`} disabled={!loaded} onClick={() => onState(item.id)}
                    title={`查看已记录的${item.label}记录，并清除其它筛选。执行结果与回合状态可能重叠，不能将这些计数相加。`}>
                    {item.label} <b className="mono">{count(summary.states[item.id])}</b>
                </button>)}
            </div>
        </header>
        <div className="trajectory-category-totals" role="group" aria-label="按经营操作类别筛选">
            {summary.categories.map(category => <button key={category.id} className={loaded && category.attempts === 0 ? "is-zero" : ""}
                aria-label={`查看${category.label}记录`} aria-pressed={type === `category:${category.id}`} disabled={!loaded}
                title={`查看已记录的${category.label}记录，并清除其它筛选。尝试包含失败、未提交和回滚，已确认只计成功且未被回滚的已提交操作。`}
                onClick={() => onCategory(category.id)}>
                <strong>{category.label}</strong>
                <span><b className="mono">{count(category.attempts)}</b><small>次尝试</small><em>已确认 {count(category.confirmed)}</em></span>
            </button>)}
        </div>
        <p>概览不随搜索变化 · 不含查询与试算 · 状态计数可能重叠</p>
    </section>;
}
