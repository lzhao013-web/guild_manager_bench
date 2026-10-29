import { Check, Lock, Prohibit } from "@phosphor-icons/react";
import { fmt } from "./model";
import { BRANCHES, MATERIALS } from "./sceneModel";
import { SkillCards, StatTiles } from "./SceneParts";
import { TalentStatus } from "./UpgradeGraph";
import { talentState } from "./talentModel";
import type { Talent } from "./talentModel";
import type { GameEntity } from "./types";

export function TalentDetails({ talent, upgrades, onSelect }: {
    talent: Talent;
    upgrades: Talent[];
    onSelect: (id: string) => void;
}) {
    const missing = talent.missing;
    const costs: [string, number][] = [["金币", talent.gold_cost]];
    if (talent.experience_cost !== undefined) costs.push(["公共经验", talent.experience_cost]);
    for (const [id, value] of Object.entries(talent.material_costs || {})) costs.push([MATERIALS[id] || id, value]);
    const shortfalls: string[] = [];
    if (missing?.gold) shortfalls.push(`缺金币 ${fmt(missing.gold)}`);
    if (missing?.experience || missing?.experience_pool) shortfalls.push(`缺公共经验 ${fmt(missing.experience ?? missing.experience_pool)}`);
    for (const [id, value] of Object.entries(missing?.materials || {})) shortfalls.push(`缺${MATERIALS[id] || id} ${fmt(value)}`);
    const exclusive = talent.exclusive_upgrade_ids || [];
    const requirements = (ids: string[], mutual: boolean) => (
        <div className={`talent-requirements ${mutual ? "exclusive" : ""}`}>
            {ids.map((id) => {
                const target = upgrades.find((upgrade) => upgrade.upgrade_id === id);
                if (!target) return <span key={id}>{id}（该快照未记录）</span>;
                const Icon = mutual ? Prohibit : target.unlocked ? Check : Lock;
                return <button key={id} className={target.unlocked ? "satisfied" : ""} onClick={() => onSelect(id)}>
                    <Icon size={13} /><span>{target.name}</span>
                    <span>{target.unlocked ? "已解锁" : mutual ? "互斥选择" : "未解锁"}</span>
                </button>;
            })}
        </div>
    );
    return (
        <div className="talent-detail">
            <TalentStatus state={talentState(talent)} />
            {talent.branch && <p className="scene-note">{BRANCHES[talent.branch] || talent.branch}{talent.tier !== undefined ? ` / 第 ${talent.tier} 层` : ""}</p>}
            {typeof talent.description === "string" && <p className="entity-description">{talent.description}</p>}
            <h4>完整解锁费用</h4>
            <dl className="talent-costs">{costs.map(([label, value]) => <div key={label}><dt>{label}</dt><dd className="mono">{fmt(value)}</dd></div>)}</dl>
            {!talent.unlocked && shortfalls.length > 0 && <div className="talent-shortfalls" aria-label="资源缺口">{shortfalls.map((text) => <span key={text}>{text}</span>)}</div>}
            <h4>前置节点{talent.required_upgrade_ids.length > 1 ? "（需要全部满足）" : ""}</h4>
            {talent.required_upgrade_ids.length ? requirements(talent.required_upgrade_ids, false) : <p className="scene-note">无前置升级</p>}
            {exclusive.length > 0 && <>
                <h4>互斥分支</h4>
                {requirements(exclusive, true)}
                {!!missing?.exclusive_upgrade_ids?.length && <p className="error-text scene-note">被已解锁节点锁定：{missing.exclusive_upgrade_ids.map((id) => upgrades.find((upgrade) => upgrade.upgrade_id === id)?.name || id).join("、")}</p>}
                <p className="scene-note">红色虚线连接的天赋不能同时解锁。</p>
            </>}
            <h4>解锁效果</h4>
            {typeof talent.party_size_bonus === "number" && talent.party_size_bonus !== 0 && <p>编队容量 +{talent.party_size_bonus}</p>}
            {typeof talent.craft_gold_discount_percent === "number" && talent.craft_gold_discount_percent !== 0 && <p>制作金币减免 {talent.craft_gold_discount_percent}%</p>}
            {typeof talent.hunt_experience_bonus_percent === "number" && talent.hunt_experience_bonus_percent !== 0 && <p>讨伐经验加成 {talent.hunt_experience_bonus_percent}%</p>}
            {typeof talent.hunt_gold_bonus_percent === "number" && talent.hunt_gold_bonus_percent !== 0 && <p>讨伐金币加成 {talent.hunt_gold_bonus_percent}%</p>}
            {typeof talent.elite_boss_gold_bonus_percent === "number" && talent.elite_boss_gold_bonus_percent !== 0 && <p>精英、首领金币额外加成 {talent.elite_boss_gold_bonus_percent}%</p>}
            {typeof talent.monster_refresh_discount_percent === "number" && talent.monster_refresh_discount_percent !== 0 && <p>刷新基础价减免 {talent.monster_refresh_discount_percent}%</p>}
            <StatTiles stats={talent.stats as Record<string, number> | undefined} modifier />
            <h4>技能效果</h4>
            <SkillCards skills={talent.skills as GameEntity[] | undefined} />
        </div>
    );
}
