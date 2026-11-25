import type { ReactNode } from "react";
import type { ActionEntry } from "./actionModel";
import type { ReturnTable } from "./actionReturns";
import { ActionChangeDetails, ActionChangePanel, ActionResourceStrip } from "./ActionChangePanel";
import { changeValue, describeChanges } from "./actionChanges";
import { ReturnCollection, ReturnedFields, Skills } from "./ActionReturnViews";
import { fmt } from "./model";
import { Portrait } from "./SceneParts";
import { STAT_NAMES } from "./sceneModel";

function MemberStats({ member }: { member: ReturnTable["records"][number] }) {
    return <dl className="recruit-member-stats">
        {(["hp", "mp"] as const).map(key => {
            const current = member.resources?.[key === "hp" ? "current_hp" : "current_mp"];
            const max = member.effective_stats?.[key];
            if (current == null && max == null) return null;
            const value = current == null ? fmt(max) : current === max ? fmt(current) : `${fmt(current)} / ${max == null ? "未记录" : fmt(max)}`;
            return <div key={key}><dt>{current == null ? STAT_NAMES[key] : key === "hp" ? "生命" : "法力"}</dt><dd className="mono">{value}</dd></div>;
        })}
        {member.effective_stats && Object.entries(member.effective_stats as Record<string, number>).filter(([key, value]) => !["hp", "mp"].includes(key) && value !== 0).map(([key, value]) =>
            <div key={key}><dt>{STAT_NAMES[key] || key}</dt><dd className="mono">{fmt(value)}</dd></div>)}
    </dl>;
}

export function RecruitmentResultPanel({ entry, data, inspection }: { entry: ActionEntry; data: ReturnTable; inspection: ReactNode }) {
    const changes = describeChanges(entry);
    const other = changes.other.filter(change => !(change.label === "新冒险者" && change.before == null && data.records.some(member => member.name === changeValue(change.after, entry, change.label))));
    return <section className="recruitment-result action-returns" aria-label="业务返回">
        <ActionResourceStrip entry={entry} changes={changes} />
        <section className="recruit-result-collection" aria-label={data.title}>
            <h4>招募角色</h4>
            {data.records.map((member, index) => <article className="recruit-member" aria-label={member.name} key={member.adventurer_id ?? index}>
                <header>
                    {member.template_id && <Portrait id={member.template_id} name={member.name} />}
                    <strong>{member.name}</strong>
                    {member.level != null && <span className="recruit-member-level mono">Lv.{member.level}</span>}
                </header>
                <MemberStats member={member} />
                <Skills skills={member.skills} />
            </article>)}
        </section>
        {(changes.members.length > 0 || other.length > 0) && <ActionChangePanel entry={entry} changes={{ ...changes, resources: [], other }} showDetails={false} />}
        {entry.returned.tables.filter(table => table !== data).map((table, index) => <ReturnCollection key={index} data={table} entry={entry} />)}
        <section className="action-inspection" aria-label="排查详情">
            <ActionChangeDetails entry={entry} changes={changes} />
            <ReturnedFields key={`members:${entry.frame.cursor}`} value={data.records.length === 1 ? data.records[0] : data.records} label="完整角色字段" />
            {Object.keys(entry.returned.fields).length > 0 && <ReturnedFields key={`extra:${entry.frame.cursor}`} value={entry.returned.fields} label="其他返回字段" />}
            {inspection}
        </section>
    </section>;
}
