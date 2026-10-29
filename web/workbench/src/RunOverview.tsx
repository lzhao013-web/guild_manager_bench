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
import { OverviewItems } from "./OverviewItems";
import { monsterClassification } from "./monsterModel";
import type { Selection } from "./sceneModel";
import type { RunModule } from "./sceneNavigation";
import type { Observation } from "./types";
import { actionBrief, recentActions } from "./actionModel";
import { prioritizeUpgrades } from "./previewOrder";
import { useNewItemKeys } from "./motion";
import type { WatchFrame, WatchRecord } from "./watchModel";

const lowHealth = isLowHealth;

export function RunOverview({ observation, frames, records, onOpen, onFrame, live = false, returnModule = null }: {
    observation: Observation | null;
    frames: WatchFrame[];
    records: WatchRecord[];
    onOpen: (module: RunModule, selection?: Selection) => void;
    onFrame: (frame: WatchFrame) => void;
    live?: boolean;
    returnModule?: RunModule | null;
}) {
    const models = modelEntries(records);
    const model = models.at(-1);
    const { actions, queries, queryOnly } = recentActions(frames);
    const current = actions[0] || queries.at(-1);
    const previewActions = actions.slice(1);
    const finished = observation?.finished === true;
    const actionTitle = (entry: typeof actions[number]) => entry.event.name === "end_turn" && entry.status === "succeeded" ? "结束回合" : entry.title;
    const newEntries = useNewItemKeys([...actions, ...queries].map(entry => entry.frame.cursor), live);
    const upgrades = observation?.global_upgrades && prioritizeUpgrades(observation.global_upgrades);
    const available = upgrades?.filter((upgrade) => upgrade.can_purchase && !upgrade.unlocked);
    const previewUpgrades = available?.length
        ? available
        : upgrades?.filter((upgrade) => upgrade.unlocked);
    const inventory = observation?.equipment_inventory;
    const stored = inventory?.filter((item) => item.equipped_by === null);
    const previewInventory = inventory && [...inventory].reverse().sort((a, b) =>
        Number(a.equipped_by !== null) - Number(b.equipped_by !== null));
    const candidates = observation?.recruit_candidates;
    const previewCandidates = candidates && [...candidates].sort((a, b) =>
        Number(!!b.can_recruit) - Number(!!a.can_recruit));
    const members = observation && [...observation.adventurers].sort((a, b) =>
        Number(lowHealth(b)) - Number(lowHealth(a)));
    const lowCount = observation?.adventurers.filter(lowHealth).length;
    const openAction = (frame: WatchFrame) => { onFrame(frame); onOpen("actions"); };
    const modelModule = <OverviewModule id="models" returning={returnModule === "models"} title="模型输出" icon={<ChatText size={16} />}
        emptyNote={!model ? "此位置尚未发出模型请求" : undefined}
        note={`${models.length} 次调用`} onOpen={onOpen}>
        {model && <ModelCallStats model={model} live={live} compact />}
        {model ? <p className="overview-model-excerpt">{model.text || "此位置没有模型正文，展开查看调用记录。"}</p>
            : <p className="overview-empty">此位置尚未发出模型请求</p>}
    </OverviewModule>;
    return (
        <div className="run-overview" role="region" aria-label="单局总览">
            <div className={`overview-grid ${finished ? "is-finished" : ""}`}>
                <OverviewModule id="party" returning={returnModule === "party"} title="编队" icon={<UsersThree size={16} />}
                    emptyNote={!members?.length ? observation ? "此时尚未招募冒险者" : "该位置没有编队快照" : undefined}
                    note={observation ? `${observation.adventurers.length} / ${observation.party_size_limit}${lowCount ? `，${lowCount} 位生命偏低` : ""}` : "未记录"} onOpen={onOpen}>
                    {members?.length ? (
                        <div className="overview-party-grid" data-members={members.length}>
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
                <OverviewModule id="actions" returning={returnModule === "actions"} title="最近行动" icon={<ListBullets size={16} />}
                    emptyNote={!current ? "此位置尚无经营行动" : undefined}
                    note={queryOnly ? `${queries.length} 次查询` : `${actions.length} 条操作与提示 · ${queries.length} 次查询`} onOpen={onOpen}>
                    {current && (
                        <button className={`overview-current-action ${current.status === "failed" ? "failed" : ""} ${newEntries.has(current.frame.cursor) ? "motion-new" : ""}`} onClick={() => openAction(current.frame)}>
                            <span className="overview-current-label">{current.kind === "query" ? "暂时只有查询记录" : current.kind === "system" ? "运行提示" : current.rolledBack ? "当时的操作，已回滚" : current.status === "pending" ? "正在执行" : "最近操作"} <time className="mono">{time(current.frame.record.at)}</time></span>
                            <strong>{actionTitle(current)}</strong>
                            {actionBrief(current) && <span className="overview-current-summary">{actionBrief(current)}</span>}
                        </button>
                    )}
                    <OverviewItems className="overview-action-rows" items={previewActions} rowHeight={28}>
                        {(entry) => (
                            <button key={entry.frame.cursor} className={`${entry.status === "failed" ? "failed" : ""} ${newEntries.has(entry.frame.cursor) ? "motion-new" : ""}`} onClick={() => openAction(entry.frame)}>
                                <time className="mono">{time(entry.frame.record.at)}</time>
                                <strong title={entry.title}>{actionTitle(entry)}</strong>
                                <span>{entry.rolledBack ? "已回滚 · " : ""}{actionBrief(entry)}</span>
                            </button>
                        )}
                    </OverviewItems>
                    {!current && <p className="overview-empty">此位置尚无经营行动</p>}
                </OverviewModule>
                {!finished && <OverviewModule id="monsters" returning={returnModule === "monsters"} title="本回合怪物" icon={<Skull size={16} />}
                    emptyNote={!observation?.monsters?.length ? observation?.monsters ? "此时没有怪物记录" : "该快照未记录怪物" : undefined}
                    note={observation?.monsters ? `${observation.monsters.length} 个` : "未记录"} onOpen={onOpen}>
                    {observation?.monsters ? (
                        <OverviewItems className="overview-enemies" items={observation.monsters} rowHeight={46} minItemWidth={140} gap={8}>
                            {(monster) => (
                                <button key={monster.monster_id as string} className="actor-frame" {...monsterFrame(monster)} aria-label={`查看怪物 ${monster.name}`} aria-description={monsterClassification(monster)}
                                    onClick={() => onOpen("monsters", { kind: "monster", id: monster.monster_id as string })}>
                                    <Portrait monster id={monster.archetype_id as string} name={monster.name!} />
                                    <span><strong title={monster.name}>{monster.name}</strong>
                                        <MonsterBadges monster={monster} compact /></span>
                                </button>
                            )}
                        </OverviewItems>
                    ) : <p className="overview-empty">该快照未记录怪物</p>}
                </OverviewModule>}
                {!finished && modelModule}
                <OverviewModule id="inventory" returning={returnModule === "inventory"} title="装备库" icon={<Backpack size={16} />}
                    emptyNote={!inventory?.length ? inventory ? "此时仓库里没有装备" : "该快照未记录库存" : undefined}
                    note={inventory ? `${inventory.length} 件，${stored!.length} 件未穿戴` : "未记录"} onOpen={onOpen}>
                    {inventory ? <OverviewItems className="overview-item-rows" items={previewInventory!} rowHeight={28} minItemWidth={180}>
                        {(item) => (
                            <button key={item.instance_id} aria-label={`查看装备 ${item.name}`}
                                onClick={() => onOpen("inventory", { kind: "equipment", id: item.instance_id! })}>
                                <EquipmentIcon templateId={item.template_id} slot={item.slot!} size={17} /><strong title={item.name}>{item.name}</strong>
                                <span>{item.equipped_by != null ? "已穿戴" : item.equipped_by === null ? "未穿戴" : "未记录"}</span>
                            </button>
                        )}
                    </OverviewItems> : <p className="overview-empty">该快照未记录库存</p>}
                </OverviewModule>
                {!finished && <OverviewModule id="recruitment" returning={returnModule === "recruitment"} title="招募候选" icon={<Storefront size={16} />}
                    emptyNote={!candidates?.length ? candidates ? "此时没有候选" : "该快照未记录招募" : undefined}
                    note={candidates ? `${candidates.length} 位，${candidates.filter((candidate) => candidate.can_recruit).length} 位可招募` : "未记录"} onOpen={onOpen}>
                    {candidates ? <OverviewItems className="overview-recruits" items={previewCandidates!} rowHeight={32}>
                        {(candidate) => (
                            <button key={candidate.candidate_id as string} aria-label={`查看招募候选 ${candidate.name}`}
                                onClick={() => onOpen("recruitment", { kind: "candidate", id: candidate.candidate_id as string })}>
                                <Portrait id={candidate.template_id} name={candidate.name!} />
                                <span><strong title={candidate.name}>{candidate.name}</strong>
                                    <small><Coins size={11} />{fmt(candidate.recruit_gold as number)}{candidate.can_recruit ? <Check size={11} /> : <Lock size={11} />}</small></span>
                            </button>
                        )}
                    </OverviewItems> : <p className="overview-empty">该快照未记录招募</p>}
                </OverviewModule>}
                <OverviewModule id="upgrades" returning={returnModule === "upgrades"} title="公会发展" icon={<TreeStructure size={16} />}
                    emptyNote={!upgrades?.length ? upgrades ? "此时没有升级记录" : "该快照未记录发展状态" : undefined}
                    note={upgrades ? `${available!.length} 可解锁 / ${upgrades.filter((upgrade) => upgrade.unlocked).length} 已解锁` : "未记录"} onOpen={onOpen}>
                    {upgrades ? <OverviewItems className="overview-upgrade-rows" items={previewUpgrades?.length ? previewUpgrades : upgrades} rowHeight={28} minItemWidth={180}>
                        {(upgrade) => (
                            <button key={upgrade.upgrade_id as string} aria-label={`查看升级 ${upgrade.name}`}
                                onClick={() => onOpen("upgrades", { kind: "upgrade", id: upgrade.upgrade_id as string })}>
                                {upgrade.unlocked ? <Check size={14} /> : upgrade.can_purchase ? <TreeStructure size={14} /> : <Lock size={14} />}
                                <strong title={upgrade.name}>{upgrade.name}</strong>
                                <span>{upgrade.unlocked ? "已解锁" : upgrade.can_purchase ? "可解锁" : "条件未满足"}</span>
                            </button>
                        )}
                    </OverviewItems> : <p className="overview-empty">该快照未记录发展状态</p>}
                </OverviewModule>
                {finished && modelModule}
            </div>
        </div>
    );
}
function OverviewModule({ id, title, icon, note, emptyNote, onOpen, children, returning = false }: {
    id: RunModule; title: string; icon: ReactNode; note: string; emptyNote?: string;
    onOpen: (module: RunModule) => void; children: ReactNode; returning?: boolean;
}) {
    return <section className={`overview-module overview-${id} ${emptyNote ? "is-empty" : ""} ${returning ? "is-return-target" : ""}`} aria-label={`${title}概览`}>
        <button className="overview-module-heading" data-module-target={id} aria-label={`展开${title}`} onClick={() => onOpen(id)}>
            <span>{icon}<h3>{title}</h3></span><small title={emptyNote || note}>{emptyNote || note}</small><ArrowsOutSimple size={14} />
        </button>
        {!emptyNote && <div className="overview-module-content">{children}</div>}
    </section>;
}
