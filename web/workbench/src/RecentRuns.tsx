import { ArrowRight } from "@phosphor-icons/react";
import { fmt } from "./model";
import { RunId } from "./RunId";
import { StatusBadge } from "./ui";
import type { Job } from "./types";
import "./recent-runs.css";

export function RecentRuns({ jobs, runKeys, onOpen, onHistory }: {
    jobs: Job[];
    runKeys: Map<string, string>;
    onOpen: (job: Job) => void;
    onHistory: () => void;
}) {
    return <section className="recent-runs" aria-label="最近结束的运行">
        <header><h2>最近结束</h2><button className="text-button" onClick={onHistory}>查看历史存档<ArrowRight size={14} /></button></header>
        <ul>{jobs.map(job => {
            const key = runKeys.get(job.id)!;
            const recordedAt = job.ended_at || job.updated_at;
            return <li className="recent-run" key={key}>
                <div className="recent-run-title">
                    <button className="run-name" aria-label={`打开运行 ${job.name} ${key}`} onClick={() => onOpen(job)}>{job.name}</button>
                    <div><RunId id={key} /><span>{job.config.preset}</span>
                        {job.data.protocol_status === "mixed" && <span className="recent-run-warning" title="续跑时使用了不同版本的规则">跨版本</span>}</div>
                </div>
                <StatusBadge status={job.status} />
                <div className="recent-run-score"><span>Rank Score</span><strong className="mono">{fmt(job.last_score)}</strong></div>
                <time dateTime={recordedAt} title={`${job.ended_at ? "结束时间" : "最后更新时间"}：${recordedAt}`}>
                    {new Date(recordedAt).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })}
                </time>
                <button className="text-button recent-run-open" aria-label={`查看结果 ${job.name} ${key}`} onClick={() => onOpen(job)}>查看结果<ArrowRight size={14} /></button>
            </li>;
        })}</ul>
    </section>;
}
