import type { ReactNode } from "react";
import { ArrowRight } from "@phosphor-icons/react";
import { ActionChangeDetails, ActionChangePanel, ActionResourceStrip } from "./ActionChangePanel";
import { changeValue, describeChanges } from "./actionChanges";
import type { ChangedMember } from "./actionChanges";
import type { ActionEntry } from "./actionModel";
import { ReturnedFields } from "./ActionReturnViews";
import { Portrait } from "./SceneParts";
import { signed } from "./sceneModel";

type MemberSkill = { skill_id?: string; name?: string };

function MemberStatChanges({ member, entry, leveledUp }: { member: ChangedMember; entry: ActionEntry; leveledUp: boolean }) {
    return member.stats.length > 0 && <table className="member-change-table">
        <thead><tr><th scope="col">属性</th><th scope="col">操作前</th><th scope="col">操作后</th><th scope="col">变化</th></tr></thead>
        <tbody>{member.stats.map((change, index) => {
            const experienceRollover = change.label === "经验" && leveledUp;
            const delta = !experienceRollover && typeof change.before === "number" && typeof change.after === "number" ? change.after - change.before : null;
            return <tr key={index}>
                <th scope="row">{change.label === "经验" ? "经验进度" : change.label}</th>
                <td className="mono member-change-before">{changeValue(change.before, entry, change.label)}</td>
                <td className="mono">{changeValue(change.after, entry, change.label)}</td>
                <td><span className={`member-change-delta ${delta === null ? "" : `mono ${delta > 0 ? "is-gain" : delta < 0 ? "is-loss" : ""}`}`}>{experienceRollover ? "跨级结转" : delta === null ? "未记录" : signed(delta)}</span></td>
            </tr>;
        })}</tbody>
    </table>;
}

export function MemberActionResultPanel({ entry, inspection }: { entry: ActionEntry; inspection: ReactNode }) {
    const changes = describeChanges(entry);
    const returnedMembers = entry.returned.tables.filter(table => table.kind === "members").flatMap(table => table.records);
    const afterMembers = entry.frame.observation !== entry.frame.before ? entry.frame.observation?.adventurers : undefined;
    const names = new Set<string>([...changes.members.map(member => member.name), ...returnedMembers.map(member => member.name)].filter(name => typeof name === "string" && name.length > 0));
    if (!names.size && entry.input.state === "recorded") {
        const target = entry.input.value.children.find(field => field.key === "adventurer_id");
        if (target) names.add(target.value.text);
    }
    return <section className="member-action-result action-returns" aria-label="业务返回">
        {!changes.changesRecorded && !entry.returned.tables.length && entry.summary && <p className="action-detail-summary">{entry.summary}</p>}
        <ActionResourceStrip entry={entry} changes={changes} />
        <section className="member-result-changes" aria-label="实际成员变化">
            <h4>成员变化</h4>
            {!names.size && <p className="plan-missing">实际成员变化未记录</p>}
            {[...names].map(name => {
                const changed = changes.members.find(member => member.name === name);
                const before = entry.frame.before?.adventurers.find(member => member.name === name);
                const after = returnedMembers.find(member => member.name === name) ?? afterMembers?.find(member => member.name === name);
                const template = after?.template_id ?? before?.template_id;
                const level = changed?.level?.after ?? after?.level;
                const previousLevel = changed?.level?.before ?? before?.level;
                const leveledUp = typeof previousLevel === "number" && typeof level === "number" && level > previousLevel;
                const beforeSkills = before?.skills as MemberSkill[] | undefined;
                const afterSkills = after?.skills as MemberSkill[] | undefined;
                const gainedSkills = beforeSkills && afterSkills ? afterSkills.filter(skill => !beforeSkills.some(old => old.skill_id === skill.skill_id)) : [];
                const lostSkills = beforeSkills && afterSkills ? beforeSkills.filter(skill => !afterSkills.some(current => current.skill_id === skill.skill_id)) : [];
                return <article className="member-change-card" aria-label={name} key={name}>
                    <header>
                        {template && <Portrait id={template} name={name} />}
                        <strong>{name}</strong>
                        {level != null && <span className="member-change-level mono">Lv.{changed?.level && previousLevel != null && <><span className="member-change-before">{changeValue(previousLevel, entry)}</span> → </>}<b>{changeValue(level, entry)}</b></span>}
                    </header>
                    {changed?.configuration.map((change, index) => <div className="member-config-change" key={index}>
                        <h5>{change.label}</h5><div><span className="member-change-before">{changeValue(change.before, entry, change.label)}</span><ArrowRight size={14} aria-hidden="true" /><strong>{changeValue(change.after, entry, change.label)}</strong></div>
                    </div>)}
                    {changed && <MemberStatChanges member={changed} entry={entry} leveledUp={leveledUp} />}
                    {!changed && <p className="plan-missing">实际成员变化未记录</p>}
                    {gainedSkills.length > 0 && <div className="member-skill-change"><span>获得技能</span><ul>{gainedSkills.map((skill, index) => <li key={skill.skill_id ?? index}>{skill.name || skill.skill_id}</li>)}</ul></div>}
                    {lostSkills.length > 0 && <div className="member-skill-change"><span>移除技能</span><ul>{lostSkills.map((skill, index) => <li key={skill.skill_id ?? index}>{skill.name || skill.skill_id}</li>)}</ul></div>}
                </article>;
            })}
        </section>
        <section className="action-inspection" aria-label="排查详情">
            <ActionChangeDetails entry={entry} changes={changes} />
            {changes.other.length > 0 && <details className="plan-disclosure"><summary>其他实际变化</summary><ActionChangePanel entry={entry} changes={{ ...changes, resources: [], members: [] }} showDetails={false} /></details>}
            {entry.returned.tables.map((table, index) => <ReturnedFields key={`${entry.frame.cursor}:${index}`} value={table.records.length === 1 ? table.records[0] : table.records} label={table.kind === "members" ? "完整角色字段" : `${table.title}完整字段`} />)}
            {Object.keys(entry.returned.fields).length > 0 && <ReturnedFields key={`extra:${entry.frame.cursor}`} value={entry.returned.fields} label="其他返回字段" />}
            {inspection}
        </section>
    </section>;
}
