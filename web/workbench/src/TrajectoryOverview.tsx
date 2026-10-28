import { useMemo } from "react";
import { useTrajectories } from "./TrajectoryContext";
import { summarizeTrajectories } from "./trajectoryModel";
import type { OperationCategory, TrajectoryState } from "./trajectoryModel";

const STATES = [
    { id: "succeeded", label: "执行成功" },
    { id: "pending", label: "执行中" },
    { id: "recorded", label: "结果未明" },
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
    const totalPlanSteps = summary.categories.reduce((total, category) => total + category.planSteps, 0);
    const loaded = history.data !== undefined;
    const count = (value: number) => loaded ? value : "…";
    return <section className="trajectory-overview" aria-label="整局工具调用概览" aria-busy={!loaded && history.pending}>
        <header>
            <div className="trajectory-overview-heading"><h3>{history.data?.origin === "execution" ? "当前阶段工具调用概览" : "整局工具调用概览"}</h3>
                <button className="trajectory-total" disabled={!loaded} onClick={onAll} title="查看全部记录，并清除当前筛选">共 <b className="mono">{count(summary.total)}</b> 次调用</button>
                <span className={`trajectory-coverage ${history.data?.origin === "execution" ? "is-partial" : ""}`}>
                    {history.error ? loaded ? "读取失败，保留上次结果" : "轨迹读取失败" : !loaded ? "正在读取…" : history.data!.origin === "execution" ? "仅当前运行阶段" : "整局已记录调用"}
                </span>
            </div>
            <div className="trajectory-state-totals" role="group" aria-label="按整局调用状态筛选">
                {STATES.map(item => <button key={item.id} className={`is-${item.id}`} aria-pressed={state === item.id}
                    aria-label={`查看${item.label}记录`} disabled={!loaded} onClick={() => onState(item.id)}
                    title={`查看已记录的${item.label}记录，并清除其它筛选。执行结果与回合状态可能重叠，不能将这些计数相加。`}>
                    {item.label} <b className="mono">{count(summary.states[item.id])}</b>
                </button>)}
            </div>
        </header>
        <div className="trajectory-category-totals" role="group" aria-label="按工具类别筛选">
            {summary.categories.map(category => {
                const isPlan = category.id === "plan";
                const operations = category.attempts + (isPlan ? 0 : category.planSteps);
                const confirmedCount = category.confirmed + (isPlan ? 0 : category.planConfirmed);
                const title = isPlan
                    ? `共 ${category.attempts} 次执行方案调用，包含 ${totalPlanSteps} 步子操作。已确认 ${category.confirmed} 次方案调用。子操作已计入各自类别，本卡片不重复累加。点击查看对应记录并清除其它筛选。`
                    : `共 ${operations} 次${category.label}操作，其中直接调用 ${category.attempts} 次、方案子操作 ${category.planSteps} 步。已确认 ${confirmedCount} 次，其中直接调用 ${category.confirmed} 次、方案子操作 ${category.planConfirmed} 步。点击查看对应记录并清除其它筛选。`;
                return <button key={category.id} className={loaded && operations === 0 ? "is-zero" : ""}
                    aria-label={`查看${category.label}记录`} aria-pressed={type === `category:${category.id}`} disabled={!loaded}
                    title={title} onClick={() => onCategory(category.id)}>
                    <strong>{category.label}</strong>
                    <span><b className="mono">{count(operations)}</b><small>{isPlan ? "次方案" : "次操作"}</small><em>已确认 {count(confirmedCount)}</em></span>
                </button>;
            })}
        </div>
        <p>类别计数含方案子操作，悬浮可查看来源 · 顶部总次数、状态计数和工具汇总仍按原始调用统计 · 不代表实际收益 · 状态可能重叠</p>
    </section>;
}
