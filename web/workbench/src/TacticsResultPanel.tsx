import type { ReactNode } from "react";
import type { ActionEntry } from "./actionModel";
import type { ReturnTable } from "./actionReturns";
import { returnNames } from "./actionReturns";
import { describeChanges } from "./actionChanges";
import { ActionChangeDetails, ActionChangePanel } from "./ActionChangePanel";
import { ReturnedFields } from "./ActionReturnViews";
import { Portrait } from "./SceneParts";

function skillList(value: unknown): string[] | undefined {
    return Array.isArray(value) ? value as string[] : undefined;
}

function PriorityList({ priority, previous, disabled, names }: { priority: string[] | undefined; previous: string[] | undefined; disabled: string[] | undefined; names: Map<string, string> }) {
    const defaulted = previous && priority && disabled ? previous.filter(id => !priority.includes(id) && !disabled.includes(id)) : [];
    return <section className="tactics-priority" aria-label="执行后的技能顺序">
        <h5>技能优先级 <span>从高到低</span></h5>
        {priority === undefined ? <p className="plan-missing">技能顺序未记录</p> : <>
            {previous === undefined && <p className="tactics-state-note">原顺序未记录</p>}
            {priority.length ? <ol>{priority.map((id, index) => {
                const oldIndex = previous?.indexOf(id);
                const adjusted = oldIndex !== undefined && oldIndex !== index;
                return <li key={`${id}:${index}`} className={adjusted ? "is-adjusted" : ""}>
                    <span className="tactics-priority-index mono" aria-hidden="true">{index + 1}</span>
                    <strong className="tactics-skill-name" title={`技能编号：${id}`}>{names.get(id) || id}</strong>
                    {adjusted && <span className="tactics-rank-change">{oldIndex < 0 ? "新增优先" : `原第 ${oldIndex + 1} 位`}</span>}
                </li>;
            })}</ol> : <p className="tactics-default-order">默认顺序</p>}
            <p className="tactics-state-note">其余未停用技能沿用默认优先级。</p>
            {defaulted.length > 0 && <div className="tactics-defaulted"><span>恢复默认优先级</span><ul>{defaulted.map((id, index) => <li key={`${id}:${index}`} title={`技能编号：${id}`}>{names.get(id) || id}</li>)}</ul></div>}
        </>}
    </section>;
}

function DisabledSkills({ disabled, previous, names }: { disabled: string[] | undefined; previous: string[] | undefined; names: Map<string, string> }) {
    const enabled = previous && disabled ? previous.filter(id => !disabled.includes(id)) : [];
    return <>
        <section className="tactics-disabled" aria-label="执行后的停用技能">
            <h5>停用技能</h5>
            {disabled === undefined ? <p className="plan-missing">停用配置未记录</p> : <>
                {previous === undefined && <p className="tactics-state-note">原停用配置未记录</p>}
                {disabled.length ? <ul>{disabled.map((id, index) => <li key={`${id}:${index}`}>
                    <span title={`技能编号：${id}`}>{names.get(id) || id}</span>
                    {previous && !previous.includes(id) && <span className="tactics-disabled-new">本次停用</span>}
                </li>)}</ul> : <p className="tactics-default-order">无停用技能</p>}
            </>}
        </section>
        {enabled.length > 0 && <section className="tactics-enabled" aria-label="本次重新启用的技能"><h5>本次重新启用</h5><ul>{enabled.map((id, index) => <li key={`${id}:${index}`} title={`技能编号：${id}`}>{names.get(id) || id}</li>)}</ul></section>}
    </>;
}

export function TacticsResultPanel({ entry, inspection }: { entry: ActionEntry; inspection: ReactNode }) {
    const changes = describeChanges(entry);
    const returnedMembers: ReturnTable["records"] = [
        ...(entry.event.result?._action_output?.adventurers ?? []),
        ...(entry.event.result?.adventurers ?? []),
    ];
    const snapshotMembers = entry.frame.observation !== entry.frame.before ? entry.frame.observation?.adventurers : undefined;
    const configured = changes.members.filter(member => member.configuration.some(change => ["技能顺序", "停用技能"].includes(change.label)));
    const subjects = new Set<string>([...returnedMembers.map(member => member.name), ...configured.map(member => member.name)].filter(name => typeof name === "string" && name.length > 0));
    if (!subjects.size && entry.input.state === "recorded") {
        const target = entry.input.value.children.find(field => field.key === "adventurer_id");
        if (target) subjects.add(target.value.text);
    }
    const names = returnNames(entry.frame);
    const actualMembers = [...subjects].map(name => returnedMembers.find(member => member.name === name) ?? snapshotMembers?.find(member => member.name === name)).filter(member => member !== undefined);
    return <section className="tactics-action-result action-returns" aria-label="业务返回">
        {!changes.changesRecorded && !entry.returned.tables.length && entry.summary && <p className="action-detail-summary">{entry.summary}</p>}
        <section className="tactics-result-config" aria-label="实际战术配置">
            <h4>战术配置</h4>
            {!subjects.size && <p className="plan-missing">实际战术配置未记录</p>}
            {[...subjects].map(name => {
                const returned = returnedMembers.find(member => member.name === name);
                const snapshot = snapshotMembers?.find(member => member.name === name);
                const before = entry.frame.before?.adventurers.find(member => member.name === name);
                const changed = configured.find(member => member.name === name);
                const orderChange = changed?.configuration.find(change => change.label === "技能顺序");
                const disabledChange = changed?.configuration.find(change => change.label === "停用技能");
                const priority = skillList(returned?.tactics?.skill_priority) ?? skillList(orderChange?.after) ?? skillList(snapshot?.tactics?.skill_priority);
                const disabled = skillList(returned?.tactics?.disabled_skills) ?? skillList(disabledChange?.after) ?? skillList(snapshot?.tactics?.disabled_skills);
                const previousPriority = skillList(orderChange?.before) ?? skillList(before?.tactics?.skill_priority);
                const previousDisabled = skillList(disabledChange?.before) ?? skillList(before?.tactics?.disabled_skills);
                const unchanged = priority !== undefined && previousPriority !== undefined && disabled !== undefined && previousDisabled !== undefined
                    && priority.length === previousPriority.length && priority.every((id, index) => id === previousPriority[index])
                    && disabled.length === previousDisabled.length && disabled.every(id => previousDisabled.includes(id));
                const template = returned?.template_id ?? snapshot?.template_id ?? before?.template_id;
                const level = returned?.level ?? snapshot?.level;
                return <article className="member-change-card tactics-member-card" aria-label={name} key={name}>
                    <header>{template && <Portrait id={template} name={name} />}<strong>{name}</strong>
                        {level != null && <span className="member-change-level mono">Lv.{level}</span>}
                    </header>
                    {unchanged && <p className="tactics-unchanged">配置未变化</p>}
                    <PriorityList priority={priority} previous={previousPriority} disabled={disabled} names={names} />
                    <DisabledSkills disabled={disabled} previous={previousDisabled} names={names} />
                </article>;
            })}
        </section>
        <section className="action-inspection" aria-label="排查详情">
            <ActionChangeDetails entry={entry} changes={changes} />
            {changes.other.length > 0 && <details className="plan-disclosure"><summary>其他实际变化</summary><ActionChangePanel entry={entry} changes={{ ...changes, resources: [], members: [] }} showDetails={false} /></details>}
            {actualMembers.length > 0 && <ReturnedFields key={`members:${entry.frame.cursor}`} value={actualMembers.length === 1 ? actualMembers[0] : actualMembers} label="完整角色字段" />}
            {entry.returned.tables.map((table, index) => <ReturnedFields key={`${entry.frame.cursor}:${index}`} value={table.records} label={table.kind === "tactics" ? "完整战术字段" : `${table.title}完整字段`} />)}
            {Object.keys(entry.returned.fields).length > 0 && <ReturnedFields key={`extra:${entry.frame.cursor}`} value={entry.returned.fields} label="其他返回字段" />}
            {inspection}
        </section>
    </section>;
}
