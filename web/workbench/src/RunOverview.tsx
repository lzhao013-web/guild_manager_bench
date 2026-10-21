import type { ReactNode } from "react";
import {
    ArrowsOutSimple, Backpack, ChatText, Check, Coins, ListBullets, Lock,
    Skull, Storefront, TreeStructure, UsersThree,
} from "@phosphor-icons/react";
import { fmt, modelEntries, time } from "./model";
import { Portrait, Vital } from "./SceneParts";
import { EquipmentIcon } from "./EquipmentIcon";
import { MonsterBadges } from "./MonsterBadges";
import { AdventurerStatus, adventurerFrame, adventurerStateText, isLowHealth, monsterFrame } from "./UnitFrame";
import { ModelCallStats } from "./ModelCallStats";
import { monsterClassification } from "./monsterModel";
import type { Selection } from "./sceneModel";
import type { RunModule } from "./sceneNavigation";
import type { Observation } from "./types";
import { actionSummary, recentActions } from "./actionModel";
import { prioritizeUpgrades } from "./previewOrder";
import type { WatchFrame, WatchRecord } from "./watchModel";

const lowHealth = isLowHealth;

export function RunOverview({ observation, frames, records, onOpen, onFrame, live = false }: {
    observation: Observation | null;
    frames: WatchFrame[];
    records: WatchRecord[];
    onOpen: (module: RunModule, selection?: Selection) => void;
    onFrame: (frame: WatchFrame) => void;
    live?: boolean;
}) {
    const models = modelEntries(records);
    const model = models.at(-1);
    const { actions, queries, queryOnly } = recentActions(frames);
    const current = actions[0] || queries.at(-1);
    const previewActions = actions.slice(1, 3);
    const upgrades = observation?.global_upgrades && prioritizeUpgrades(observation.global_upgrades);
    const available = upgrades?.filter((upgrade) => upgrade.can_purchase && !upgrade.unlocked);
    const previewUpgrades = available?.length
        ? available
        : upgrades?.filter((upgrade) => upgrade.unlocked).slice(-2);
    const inventory = observation?.equipment_inventory;
    const stored = inventory?.filter((item) => item.equipped_by === null);
    const previewInventory = inventory && [...inventory].reverse().sort((a, b) =>
        Number(a.equipped_by !== null) - Number(b.equipped_by !== null)).slice(0, 2);
    const candidates = observation?.recruit_candidates;
    const previewCandidates = candidates && [...candidates].sort((a, b) =>
        Number(!!b.can_recruit) - Number(!!a.can_recruit)).slice(0, 2);
    const members = observation && [...observation.adventurers].sort((a, b) =>
        Number(lowHealth(b)) - Number(lowHealth(a))).slice(0, 6);
    const lowCount = observation?.adventurers.filter(lowHealth).length;
    const openAction = (frame: WatchFrame) => { onFrame(frame); onOpen("actions"); };
    return (
        <div className="run-overview" role="region" aria-label="单局总览">
            <div className="overview-grid">
                <OverviewModule id="party" title="编队" icon={<UsersThree size={16} />}
                    note={observation ? `${observation.adventurers.length} / ${observation.party_size_limit}${lowCount ? `，${lowCount} 位生命偏低` : ""}${observation.adventurers.length > 6 ? "，摘要显示 6 位" : ""}` : "未记录"} onOpen={onOpen}>
                    {members?.length ? (
                        <div className="overview-party-grid">
                            {members.map((member) => (
                                <button key={member.adventurer_id} className={`actor-frame ${lowHealth(member) ? "is-low-health" : ""}`}
                                    {...adventurerFrame(member)}
                                    aria-label={`查看成员 ${member.name}`} aria-description={adventurerStateText(member)}
                                    onClick={() => onOpen("party", { kind: "member", id: member.adventurer_id })}>
                                    <Portrait id={member.template_id} name={member.name} />
                                    <div className="overview-member-info">
                                        <div className="overview-member-name">
                                            <AdventurerStatus member={member} compact />
                                            <strong title={member.name}>{member.name}</strong>
                                            <span className="overview-member-level mono">Lv.{member.level}</span>
                                        </div>
                                        <Vital label="生命" value={member.resources.current_hp} max={member.effective_stats.hp} />
                                        {member.effective_stats.mp > 0 && <Vital label="法力" value={member.resources.current_mp} max={member.effective_stats.mp} mana />}
                                    </div>
                                </button>
                            ))}
                        </div>
                    ) : <p className="overview-empty">{observation ? "此时尚未招募冒险者" : "该位置没有编队快照"}</p>}
                </OverviewModule>
                <OverviewModule id="actions" title="最近行动" icon={<ListBullets size={16} />}
                    note={queryOnly ? `无经营动作 · ${queries.length} 次查询` : `${actions.length} 条操作与提示 · ${queries.length} 次查询`} onOpen={onOpen}>
                    {current && (
                        <button className={`overview-current-action ${current.status === "failed" ? "failed" : ""}`} onClick={() => openAction(current.frame)}>
                            <span className="overview-current-label">{current.kind === "query" ? "目前只进行了查询，尚无经营动作" : current.kind === "system" ? "运行提示" : current.rolledBack ? "当时的操作，已回滚" : current.status === "pending" ? "正在执行" : "最近操作"} <time className="mono">{time(current.frame.record.at)}</time></span>
                            <strong>{current.title}</strong>
                            {actionSummary(current) && <span className="overview-current-summary">{actionSummary(current)}</span>}
                        </button>
                    )}
                    <div className="overview-action-rows">
                        {previewActions.map((entry) => (
                            <button key={entry.frame.cursor} className={entry.status === "failed" ? "failed" : ""} onClick={() => openAction(entry.frame)}>
                                <time className="mono">{time(entry.frame.record.at)}</time>
                                <strong>{entry.title}</strong>
                                <span>{entry.rolledBack ? "已回滚 · " : ""}{actionSummary(entry)}</span>
                            </button>
                        ))}
                    </div>
                    {!current && <p className="overview-empty">此位置尚无经营行动</p>}
                </OverviewModule>
                <OverviewModule id="monsters" title="本回合怪物" icon={<Skull size={16} />}
                    note={observation?.monsters ? `${observation.monsters.length} 个${observation.monsters.length > 6 ? "，摘要显示 6 个" : ""}` : "未记录"} onOpen={onOpen}>
                    {observation?.monsters ? (
                        <div className="overview-enemies">
                            {observation.monsters.slice(0, 6).map((monster) => (
                                <button key={monster.monster_id as string} className="actor-frame" {...monsterFrame(monster)} aria-label={`查看怪物 ${monster.name}`} aria-description={monsterClassification(monster)}
                                    onClick={() => onOpen("monsters", { kind: "monster", id: monster.monster_id as string })}>
                                    <Portrait monster id={monster.archetype_id as string} name={monster.name!} />
                                    <span><strong title={monster.name}>{monster.name}</strong>
                                        <MonsterBadges monster={monster} compact /></span>
                                </button>
                            ))}
                            {!observation.monsters.length && <p className="overview-empty">此时没有怪物记录</p>}
                        </div>
                    ) : <p className="overview-empty">该快照未记录怪物</p>}
                </OverviewModule>
                <OverviewModule id="models" title="模型输出" icon={<ChatText size={16} />}
                    note={`${models.length} 次调用`} onOpen={onOpen}>
                    {model && <ModelCallStats model={model} live={live} compact />}
                    {model ? <p className="overview-model-excerpt">{model.text.slice(0, 800) || "此位置没有模型正文，展开查看调用记录。"}</p>
                        : <p className="overview-empty">此位置尚未发出模型请求</p>}
                </OverviewModule>
                <OverviewModule id="inventory" title="装备库" icon={<Backpack size={16} />}
                    note={inventory ? `${inventory.length} 件，${stored!.length} 件未穿戴` : "未记录"} onOpen={onOpen}>
                    {inventory ? <div className="overview-item-rows">
                        {previewInventory!.map((item) => (
                            <button key={item.instance_id} aria-label={`查看装备 ${item.name}`}
                                onClick={() => onOpen("inventory", { kind: "equipment", id: item.instance_id! })}>
                                <EquipmentIcon templateId={item.template_id} slot={item.slot!} size={17} /><strong title={item.name}>{item.name}</strong>
                                <span>{item.equipped_by != null ? "已穿戴" : item.equipped_by === null ? "未穿戴" : "未记录"}</span>
                            </button>
                        ))}
                        {!inventory.length && <p className="overview-empty">此时仓库里没有装备</p>}
                    </div> : <p className="overview-empty">该快照未记录库存</p>}
                </OverviewModule>
                <OverviewModule id="recruitment" title="招募候选" icon={<Storefront size={16} />}
                    note={candidates ? `${candidates.length} 位，${candidates.filter((candidate) => candidate.can_recruit).length} 位可招募` : "未记录"} onOpen={onOpen}>
                    {candidates ? <div className="overview-recruits">
                        {previewCandidates!.map((candidate) => (
                            <button key={candidate.candidate_id as string} aria-label={`查看招募候选 ${candidate.name}`}
                                onClick={() => onOpen("recruitment", { kind: "candidate", id: candidate.candidate_id as string })}>
                                <Portrait id={candidate.template_id} name={candidate.name!} />
                                <span><strong title={candidate.name}>{candidate.name}</strong>
                                    <small><Coins size={11} />{fmt(candidate.recruit_gold as number)}{candidate.can_recruit ? <Check size={11} /> : <Lock size={11} />}</small></span>
                            </button>
                        ))}
                        {!candidates.length && <p className="overview-empty">此时没有候选</p>}
                    </div> : <p className="overview-empty">该快照未记录招募</p>}
                </OverviewModule>
                <OverviewModule id="upgrades" title="公会发展" icon={<TreeStructure size={16} />}
                    note={upgrades ? `${available!.length} 可解锁 / ${upgrades.filter((upgrade) => upgrade.unlocked).length} 已解锁` : "未记录"} onOpen={onOpen}>
                    {upgrades ? <div className="overview-upgrade-rows">
                        {(previewUpgrades?.length ? previewUpgrades : upgrades).slice(0, 2).map((upgrade) => (
                            <button key={upgrade.upgrade_id as string} aria-label={`查看升级 ${upgrade.name}`}
                                onClick={() => onOpen("upgrades", { kind: "upgrade", id: upgrade.upgrade_id as string })}>
                                {upgrade.unlocked ? <Check size={14} /> : upgrade.can_purchase ? <TreeStructure size={14} /> : <Lock size={14} />}
                                <strong title={upgrade.name}>{upgrade.name}</strong>
                                <span>{upgrade.unlocked ? "已解锁" : upgrade.can_purchase ? "可解锁" : "条件未满足"}</span>
                            </button>
                        ))}
                        {!upgrades.length && <p className="overview-empty">此时没有升级记录</p>}
                    </div> : <p className="overview-empty">该快照未记录发展状态</p>}
                </OverviewModule>
            </div>
        </div>
    );
}
function OverviewModule({ id, title, icon, note, onOpen, children }: {
    id: RunModule; title: string; icon: ReactNode; note: string;
    onOpen: (module: RunModule) => void; children: ReactNode;
}) {
    return <section className={`overview-module overview-${id}`} aria-label={`${title}概览`}>
        <button className="overview-module-heading" data-module-target={id} aria-label={`展开${title}`} onClick={() => onOpen(id)}>
            <span>{icon}<h3>{title}</h3></span><small title={note}>{note}</small><ArrowsOutSimple size={14} />
        </button>
        <div className="overview-module-content">{children}</div>
    </section>;
}
