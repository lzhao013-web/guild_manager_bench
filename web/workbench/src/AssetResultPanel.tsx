import type { ReactNode } from "react";
import { Sparkle } from "@phosphor-icons/react";
import type { ActionEntry } from "./actionModel";
import type { ReturnTable } from "./actionReturns";
import { returnNames } from "./actionReturns";
import { ActionChangeDetails, ActionChangePanel, ActionResourceStrip } from "./ActionChangePanel";
import { describeChanges } from "./actionChanges";
import { ReturnedFields, Skills, StatValues } from "./ActionReturnViews";
import { EquipmentBadge } from "./EquipmentBadge";
import { EquipmentIcon } from "./EquipmentIcon";
import { BRANCHES, SLOTS } from "./sceneModel";
import { talentBonuses } from "./talentModel";

type Asset = ReturnTable["records"][number];

function EquipmentResult({ item, name, names }: { item: Asset; name: string; names: Map<string, string> }) {
    return <article className="asset-result-card" aria-label={name}>
        <header>
            {item.slot && <EquipmentIcon templateId={item.template_id} slot={item.slot} size={44} />}
            <div><strong>{name}</strong><div className="asset-result-meta"><EquipmentBadge templateId={item.template_id} />{item.slot && <span>{SLOTS[item.slot] || item.slot}</span>}</div></div>
            <span className="asset-result-state">{item.equipped_by === null ? "未穿戴" : item.equipped_by == null ? "穿戴者未记录" : `穿戴者：${names.get(String(item.equipped_by)) || item.equipped_by}`}</span>
        </header>
        {item.description && <p className="return-description">{item.description}</p>}
        {item.stats != null ? <StatValues stats={item.stats} /> : <p className="plan-missing">属性未记录</p>}
        <Skills skills={item.skills} />
        {item.allowed_class_names?.length > 0 && <p className="asset-class-restriction"><span>职业限制</span>{item.allowed_class_names.join("、")}</p>}
    </article>;
}

function UpgradeResult({ item, name }: { item: Asset; name: string }) {
    const bonuses = talentBonuses(item);
    const hasStats = item.stats != null && Object.values(item.stats).some(value => value !== 0);
    return <article className="asset-result-card is-upgrade" aria-label={name}>
        <header>
            <span className="asset-upgrade-icon"><Sparkle size={26} weight="duotone" aria-hidden="true" /></span>
            <div><strong>{name}</strong><div className="asset-result-meta">{item.branch && <span>{BRANCHES[item.branch] || item.branch}</span>}{item.tier != null && <span>第 {item.tier} 层</span>}</div></div>
        </header>
        {item.description && <p className="return-description">{item.description}</p>}
        {hasStats && <StatValues stats={item.stats} modifier />}
        {bonuses.length > 0 && <dl className="return-stat-grid asset-bonus-values">{bonuses.map(bonus => <div key={bonus.key}><dt>{bonus.label}</dt><dd className="mono">{bonus.value}</dd></div>)}</dl>}
        <Skills skills={item.skills} />
        {!item.description && !hasStats && !bonuses.length && !item.skills?.length && <p className="plan-missing">效果明细未记录</p>}
    </article>;
}

export function AssetResultPanel({ entry, data, inspection }: { entry: ActionEntry; data: ReturnTable; inspection: ReactNode }) {
    const changes = describeChanges(entry);
    const equipment = data.kind === "equipment";
    const costs = { ...changes, resources: changes.resources.filter(change => change.kind === "material" || ["金币", "经验池"].includes(change.label)) };
    const names = returnNames(entry.frame);
    return <section className="asset-result action-returns" aria-label="业务返回">
        <ActionResourceStrip entry={entry} changes={costs} />
        <section className="asset-result-collection" aria-label={data.title}>
            <h4>{equipment ? "制作成品" : "本次解锁"}</h4>
            {data.records.map((item, index) => {
                const name = item.name || data.rows[index][0].split("\n")[0];
                return equipment ? <EquipmentResult key={item.instance_id ?? index} item={item} name={name} names={names} />
                    : <UpgradeResult key={item.upgrade_id ?? index} item={item} name={name} />;
            })}
        </section>
        <section className="action-inspection" aria-label="排查详情">
            <ActionChangeDetails entry={entry} changes={changes} />
            {changes.members.length > 0 && <details className="plan-disclosure"><summary>对现有成员的影响（{changes.members.length} 位）</summary><ActionChangePanel entry={entry} changes={{ ...changes, resources: [], other: [] }} showDetails={false} /></details>}
            {changes.other.length > 0 && <details className="plan-disclosure"><summary>其他实际变化</summary><ActionChangePanel entry={entry} changes={{ ...changes, resources: [], members: [] }} showDetails={false} /></details>}
            {entry.returned.tables.map((table, index) => <ReturnedFields key={`${entry.frame.cursor}:${index}`} value={table.records.length === 1 ? table.records[0] : table.records}
                label={table === data ? equipment ? "完整装备字段" : "完整天赋字段" : `${table.title}完整字段`} />)}
            {Object.keys(entry.returned.fields).length > 0 && <ReturnedFields key={`extra:${entry.frame.cursor}`} value={entry.returned.fields} label="其他返回字段" />}
            {inspection}
        </section>
    </section>;
}
