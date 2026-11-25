import type { ActionEntry } from "./actionModel";
import { changeDelta, changeValue } from "./actionChanges";
import type { ActionChange, ActionChanges } from "./actionChanges";

function Change({ change, entry }: { change: ActionChange; entry: ActionEntry }) {
    return <span className="plan-change"><span>{change.label}</span><b className="mono">{changeDelta(change, entry)}</b></span>;
}

export function ActionResourceStrip({ entry, changes }: { entry: ActionEntry; changes: ActionChanges }) {
    return changes.resources.length > 0 && <section className="result-resource-strip" aria-label={changes.preview ? "预计资源变化" : "实际资源变化"}>
        <h4>{changes.preview ? "预计资源" : "资源变化"}</h4>
        <dl>{changes.resources.map((change, index) => {
            const delta = typeof change.before === "number" && typeof change.after === "number" ? change.after - change.before : null;
            return <div key={index}><dt>{change.label}</dt><dd>
                <strong className={`mono ${delta !== null && delta < 0 ? "is-spend" : delta !== null && delta > 0 ? "is-gain" : ""}`}>{changeDelta(change, entry)}</strong>
                {change.before != null && <span className="mono">{changeValue(change.before, entry)} → {changeValue(change.after, entry)}</span>}
            </dd></div>;
        })}</dl>
    </section>;
}

export function ActionChangePanel({ entry, changes, showEmpty = false, showDetails = true }: { entry: ActionEntry; changes: ActionChanges; showEmpty?: boolean; showDetails?: boolean }) {
    const title = changes.preview ? "预计变化" : "实际变化";
    const visibleStats = (stats: ActionChange[]) => stats.filter(change => !["经验", "当前生命", "当前法力"].includes(change.label));
    const level = (name: string) => {
        const before = entry.frame.before?.adventurers.find(member => member.name === name);
        const after = entry.frame.observation?.adventurers.find(member => member.name === name);
        return before && after && before.level === after.level ? String(after.level) : "未记录";
    };
    return <>
        {changes.resources.length > 0 && <section className="plan-resources" aria-label={changes.preview ? "预计资源变化" : "实际资源变化"}>
            <h4>{changes.preview ? "预计资源" : "资源变化"}</h4><div>{changes.resources.map((change, index) => <Change key={index} change={change} entry={entry} />)}</div>
        </section>}
        {changes.members.length > 0 && <section className="plan-members" aria-label={changes.preview ? "预计成员变化" : "实际成员变化"}>
            <h4>{changes.preview ? "预计成员变化" : "成员变化"}</h4>
            <table><thead><tr><th scope="col">成员</th><th scope="col">等级</th><th scope="col">属性变化</th></tr></thead>
                <tbody>{changes.members.map(member => <tr key={member.name}>
                    <th scope="row">{member.name}</th>
                    <td className="mono">{member.level ? `${changeValue(member.level.before, entry)} → ${changeValue(member.level.after, entry)}` : level(member.name)}</td>
                    <td><div className="plan-stat-changes">{(visibleStats(member.stats).length ? visibleStats(member.stats) : member.stats).map((change, index) => <Change key={index} change={change} entry={entry} />)}</div>
                        {member.configuration.map((change, index) => <p className="plan-configuration" key={index}><span>{change.label}</span>{change.before != null && `${changeValue(change.before, entry, change.label)} → `}{changeValue(change.after, entry, change.label)}</p>)}
                    </td>
                </tr>)}</tbody></table>
        </section>}
        {changes.other.length > 0 && <ul className="plan-other-changes">{changes.other.map((change, index) => <li key={index}>{change.label}：{change.before !== undefined && `${changeValue(change.before, entry, change.label)} → `}{changeValue(change.after, entry, change.label)}</li>)}</ul>}
        {showEmpty && !changes.changesRecorded && <p className="plan-missing">{title}未记录</p>}
        {showEmpty && changes.changesRecorded && !changes.resources.length && !changes.members.length && !changes.other.length && <p className="plan-missing">无变化</p>}
        {showDetails && <ActionChangeDetails entry={entry} changes={changes} />}
    </>;
}

export function ActionChangeDetails({ entry, changes, includeOther = false }: { entry: ActionEntry; changes: ActionChanges; includeOther?: boolean }) {
    return (changes.resources.length > 0 || changes.members.length > 0 || includeOther && changes.other.length > 0) && <details className="plan-disclosure" key={`changes:${entry.frame.cursor}`}>
        <summary>完整{changes.preview ? "预计变化" : "实际变化"}（含属性前后值）</summary>
        <ul className="plan-full-changes">
            {changes.resources.map((change, index) => <li key={`resource:${index}`}>{change.label}：{change.before != null && `${changeValue(change.before, entry)} → `}{changeValue(change.after, entry)} <span className="mono">（{changeDelta(change, entry)}）</span></li>)}
            {changes.members.map(member => <li key={member.name}><strong>{member.name}</strong><ul>{[...(member.level ? [member.level] : []), ...member.stats, ...member.configuration].map((change, index) => <li key={index}>{change.label}：{change.before != null && `${changeValue(change.before, entry, change.label)} → `}{changeValue(change.after, entry, change.label)}</li>)}</ul></li>)}
            {includeOther && changes.other.map((change, index) => <li key={`other:${index}`}>{change.label}：{change.before !== undefined && `${changeValue(change.before, entry, change.label)} → `}{changeValue(change.after, entry, change.label)}</li>)}
        </ul>
    </details>;
}
