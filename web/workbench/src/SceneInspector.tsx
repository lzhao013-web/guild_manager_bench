import { useState } from "react";
import { Tooltip } from "@radix-ui/themes";
import { MemberContribution, contributionNote } from "./MemberContribution";
import type { RankContributions } from "./watchModel";
import { ArrowSquareOut, CaretLeft, CaretRight, Lock, X } from "@phosphor-icons/react";
import { fmt } from "./model";
import { MATERIALS, SLOTS, resolveSubject, subjectId } from "./sceneModel";
import type { Selection, SceneSubject } from "./sceneModel";
import { Portrait, SkillCards, StatTiles, Vital } from "./SceneParts";
import { EquipmentIcon } from "./EquipmentIcon";
import { MonsterBadges } from "./MonsterBadges";
import { AdventurerStatus, adventurerFrame, adventurerStateText, monsterFrame } from "./UnitFrame";
import type { CraftingRecipe, GameEntity, Observation, PartyMember } from "./types";
import { RecipeDetails } from "./RecipeDetails";
import { TalentDetails } from "./TalentDetails";
import { ObjectTrajectories } from "./RunTrajectories";
import type { Talent } from "./talentModel";

export function SceneInspector({
    observation,
    contributions = null,
    collection,
    selection,
    onSelect,
    onClose,
    onInventory,
}: {
    observation: Observation;
    contributions?: RankContributions | null;
    collection: SceneSubject[];
    selection: Selection | null;
    onSelect: (selection: Selection) => void;
    onClose: () => void;
    onInventory: (slot: string) => void;
}) {
    const subject = selection ? resolveSubject(observation, selection) : undefined;
    const index = selection
        ? collection.findIndex((item) => subjectId(selection.kind, item) === selection.id)
        : -1;
    function move(delta: number) {
        if (selection)
            onSelect({
                kind: selection.kind,
                id: subjectId(selection.kind, collection[index + delta]),
            });
    }
    if (!subject || !selection) return null;
    return (
        <section className={`scene-inline-inspector ${selection.kind === "upgrade" ? "talent-inspector" : ""}`} aria-label={`${subject.name} 的详情`}>
            <>
                <header className="scene-object-heading">
                    <div className="scene-object-identity">
                        <span className="scene-note" title="当前回合的局面">
                            <Lock size={11} />
                            {
                                {
                                    member: "编队成员",
                                    monster: "怪物图鉴",
                                    equipment: "装备详情",
                                    candidate: "招募候选",
                                    upgrade: "公会发展",
                                    recipe: "制作配方",
                                }[selection.kind]
                            }
                        </span>
                        <select
                            aria-label="切换查看对象"
                            title={subject.name}
                            value={selection.id}
                            onChange={(event) => onSelect({ kind: selection.kind, id: event.target.value })}
                        >
                            {collection.map((item) => (
                                <option key={subjectId(selection.kind, item)} value={subjectId(selection.kind, item)}>
                                    {item.name}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div className="scene-object-controls">
                        <button
                            aria-label="查看上一个对象"
                            disabled={index <= 0}
                            onClick={() => move(-1)}
                        >
                            <CaretLeft />
                        </button>
                        <span className="mono">
                            {index + 1} / {collection.length}
                        </span>
                        <button
                            aria-label="查看下一个对象"
                            disabled={index >= collection.length - 1}
                            onClick={() => move(1)}
                        >
                            <CaretRight />
                        </button>
                        <button
                            className="scene-object-close"
                            aria-label="收起对象详情"
                            onClick={onClose}
                        >
                            <X size={19} />
                        </button>
                    </div>
                </header>
                <ObjectTrajectories subject={selection} />
                {selection.kind === "member" ? (
                    <MemberInspector member={subject as PartyMember} contributions={contributions} onInventory={onInventory} />
                ) : selection.kind === "recipe" ? (
                    <RecipeDetails recipe={subject as CraftingRecipe} observation={observation} onInventory={onInventory} />
                ) : selection.kind === "upgrade" ? (
                    <TalentDetails
                        talent={subject as Talent}
                        upgrades={observation.global_upgrades as Talent[]}
                        onSelect={(id) => onSelect({ kind: "upgrade", id })}
                    />
                ) : (
                    <EntityInspector
                        entity={subject as GameEntity}
                        kind={selection.kind}
                        observation={observation}
                        onSelect={onSelect}
                    />
                )}
            </>
        </section>
    );
}
function MemberInspector({
    member,
    contributions,
    onInventory,
}: {
    member: PartyMember;
    contributions: RankContributions | null;
    onInventory: (slot: string) => void;
}) {
    const [tab, setTab] = useState("equipment");
    const [slot, setSlot] = useState<string | null>(null);
    const [compareBase, setCompareBase] = useState(false);
    const slots = member.equipment_slots;
    const selected =
        slots?.find((item) => item.slot === slot) ||
        slots?.find((item) => item.item != null) ||
        slots?.[0];
    const item = selected?.item as GameEntity | null | undefined;
    const active = member.skills?.filter((skill) => skill.kind === "active").length;
    const passive = member.skills?.filter((skill) => skill.kind === "passive").length;
    const statKeys = [...new Set(["attack", "defense", "speed", ...Object.keys(member.effective_stats)])];
    const stats = Object.fromEntries(statKeys
        .filter((key) => key in member.effective_stats && (compareBase || (key !== "hp" && key !== "mp")))
        .map((key) => [key, member.effective_stats[key]]));
    return (
        <>
            <div className="scene-member-status">
                <b>Lv. {member.level}<AdventurerStatus member={member} /></b>
                <Vital
                    label="生命"
                    value={member.resources.current_hp}
                    max={member.effective_stats.hp}
                />
                <Vital
                    label="法力"
                    value={member.resources.current_mp}
                    max={member.effective_stats.mp}
                    mana
                />
            </div>
            <div className="scene-member-contribution">
                <Tooltip content={contributionNote(member.adventurer_id, contributions)}>
                    <span tabIndex={0}><MemberContribution adventurerId={member.adventurer_id} contributions={contributions} /></span>
                </Tooltip>
            </div>
            <nav className="scene-inspect-tabs" aria-label="角色详情内容">
                <button
                    className={tab === "equipment" ? "active" : ""}
                    aria-pressed={tab === "equipment"}
                    onClick={() => setTab("equipment")}
                >
                    角色与装备
                </button>
                <button
                    className={tab === "skills" ? "active" : ""}
                    aria-pressed={tab === "skills"}
                    onClick={() => setTab("skills")}
                >
                    技能与战术{active != null ? ` (${active! + passive!})` : ""}
                </button>
            </nav>
            {tab === "equipment" ? (
                <div className="scene-character-detail">
                    <section
                        className="scene-equipment-figure"
                        aria-label={`${member.name} 的装备槽位图`}
                    >
                        <div className="paperdoll">
                            <div className="paperdoll-portrait actor-frame" {...adventurerFrame(member)} data-unit-selected="true" role="img" aria-label={`${member.name}，${adventurerStateText(member)}`}>
                                <Portrait id={member.template_id} name={member.name} />
                                <span>{member.name}</span>
                            </div>
                            {slots?.map((equipmentSlot) => {
                                const equipped = equipmentSlot.item as GameEntity | null;
                                const blocked = equipmentSlot.blocked_by as string | null;
                                return (
                                    <button
                                        key={equipmentSlot.slot}
                                        data-slot={equipmentSlot.slot}
                                        className={`paperdoll-slot ${equipped ? "equipped" : "vacant"} ${selected?.slot === equipmentSlot.slot ? "selected" : ""}`}
                                        aria-label={`${SLOTS[equipmentSlot.slot!] || equipmentSlot.slot}：${equipped?.name || (blocked ? "被占用" : "空")}`}
                                        aria-pressed={selected?.slot === equipmentSlot.slot}
                                        onClick={() => setSlot(equipmentSlot.slot!)}
                                    >
                                        <EquipmentIcon templateId={equipped?.template_id} slot={equipmentSlot.slot!} size={equipped ? 36 : 24} />
                                        {!equipped && <span>
                                            {SLOTS[equipmentSlot.slot!] || equipmentSlot.slot}
                                        </span>}
                                        <strong title={equipped?.name}>
                                            {equipped?.name ||
                                                (blocked
                                                    ? `被${blocked === "hand" ? "单手装备" : SLOTS[blocked] || blocked}占用`
                                                    : "空槽位")}
                                        </strong>
                                    </button>
                                );
                            })}
                        </div>
                        {!slots && <p className="scene-note">该快照未记录装备槽位</p>}
                    </section>
                    <section className="scene-character-stats">
                        <div className="scene-section-title">
                            <h3>当前生效属性</h3>
                            {member.base_stats && (
                                <button
                                    className={compareBase ? "active" : ""}
                                    aria-pressed={compareBase}
                                    onClick={() => setCompareBase(!compareBase)}
                                >
                                    对比基础
                                </button>
                            )}
                        </div>
                        <StatTiles
                            stats={stats}
                            previous={compareBase ? member.base_stats : undefined}
                        />
                        {compareBase && (
                            <p className="scene-note">
                                差值相对于快照中的初始基础属性，包含等级、装备与升级等影响。
                            </p>
                        )}
                        <div className="scene-skill-count">
                            <div>
                                <b className="mono">{active ?? "未知"}</b>
                                <span>主动技能</span>
                            </div>
                            <div>
                                <b className="mono">{passive ?? "未知"}</b>
                                <span>被动技能</span>
                            </div>
                        </div>
                    </section>
                    {selected && (
                        <section className="selected-equipment">
                            <header>
                                <EquipmentIcon templateId={item?.template_id} slot={selected.slot!} size={20} />
                                <h3>{item?.name || `${SLOTS[selected.slot!] || selected.slot}：${selected.blocked_by ? "已被占用" : "尚未装备"}`}</h3>
                            </header>
                            {item && <>
                                <StatTiles stats={item.stats as Record<string, number> | undefined} modifier />
                                {item.skills === undefined && <p className="scene-note">技能未记录</p>}
                                {item.skills !== undefined && (item.skills as GameEntity[]).length > 0 && (
                                    <details className="equipment-skills">
                                        <summary>装备技能：{(item.skills as GameEntity[]).map((skill) => skill.name).join("、")}</summary>
                                        <SkillCards skills={item.skills as GameEntity[]} />
                                    </details>
                                )}
                            </>}
                            <button className="scene-text-action" onClick={() => onInventory(selected.slot!)}>
                                在仓库查看同槽装备<ArrowSquareOut size={13} />
                            </button>
                        </section>
                    )}
                </div>
            ) : (
                <section className="scene-character-skills">
                    <Tactics member={member} />
                    <SkillCards skills={member.skills} />
                </section>
            )}
        </>
    );
}
function Tactics({ member }: { member: PartyMember }) {
    if (!member.tactics) return <p className="scene-note">该快照未记录战术设置</p>;
    const order = member.tactics.skill_priority as string[];
    const disabled = member.tactics.disabled_skills as string[];
    const available = member.tactics.available_active_skills as GameEntity[];
    const name = (id: string) =>
        available.find((skill) => skill.skill_id === id)?.name ||
        member.skills?.find((skill) => skill.skill_id === id)?.name ||
        id;
    return (
        <div className="scene-tactics">
            <h3>已记录的技能优先级</h3>
            {order.length ? (
                <div className="tactic-sequence">
                    {order.map((id, index) => (
                        <span key={id}>
                            <b>{index + 1}</b>
                            {name(id)}
                        </span>
                    ))}
                </div>
            ) : (
                <p className="scene-note">未设置自定义优先级</p>
            )}
            {!!disabled.length && (
                <p className="scene-note">已禁用：{disabled.map(name).join("、")}</p>
            )}
        </div>
    );
}
function EntityInspector({
    entity,
    kind,
    observation,
    onSelect,
}: {
    entity: GameEntity;
    kind: Exclude<Selection["kind"], "member" | "upgrade" | "recipe">;
    observation: Observation;
    onSelect: (selection: Selection) => void;
}) {
    const stats = (entity.effective_stats || entity.stats || entity.base_stats) as Record<string, number> | undefined;
    const owner = entity.equipped_by
        ? observation.adventurers.find((member) => member.adventurer_id === entity.equipped_by)
        : undefined;
    return (
        <div className={`scene-entity-detail entity-${kind}`}>
            <div className="scene-entity-hero">
                {kind === "monster" ? (
                    <div className="scene-portrait unit-portrait-frame actor-frame" {...monsterFrame(entity)} data-unit-selected="true">
                        <Portrait id={entity.archetype_id as string} name={entity.name!} monster />
                    </div>
                ) : kind === "candidate" ? (
                    <Portrait id={entity.template_id} name={entity.name!} />
                ) : (
                    <div className="entity-item-glyph">
                        <EquipmentIcon templateId={entity.template_id} slot={entity.slot || "ultimate"} size={80} />
                    </div>
                )}
                <div>
                    {typeof entity.description === "string" && (
                        <p className="entity-description">{entity.description}</p>
                    )}
                    {kind === "monster" && (
                        <>
                            <MonsterBadges monster={entity} />
                            <Reward value={entity.reward as Record<string, unknown> | undefined} />
                        </>
                    )}
                    {kind === "candidate" && (
                        <>
                            <p className="entity-price">
                                招募费用{" "}
                                <b className="mono">{fmt(entity.recruit_gold as number)}</b>
                            </p>
                            <p className={entity.can_recruit ? "scene-positive" : "scene-note"}>
                                {entity.can_recruit ? "当前满足招募条件" : "当前未满足招募条件"}
                            </p>
                            <Missing
                                value={entity.missing as Record<string, unknown> | undefined}
                            />
                        </>
                    )}
                    {kind === "equipment" && (
                        <>
                            <p className="scene-tag">{SLOTS[entity.slot!] || entity.slot}</p>
                            <p className="scene-note">
                                适用职业：
                                {entity.allowed_class_names === undefined
                                    ? "未记录"
                                    : (entity.allowed_class_names as string[]).join("、") ||
                                      "不限职业"}
                            </p>
                            {owner ? (
                                <button
                                    className="scene-text-action"
                                    onClick={() =>
                                        onSelect({ kind: "member", id: owner.adventurer_id })
                                    }
                                >
                                    由 {owner.name} 穿戴
                                    <ArrowSquareOut size={13} />
                                </button>
                            ) : (
                                <p className="scene-note">
                                    {entity.equipped_by === null
                                        ? "存放于仓库，尚未穿戴"
                                        : "穿戴者未记录"}
                                </p>
                            )}
                        </>
                    )}
                </div>
            </div>
            <h3>{kind === "candidate" ? (entity.effective_stats ? "Lv1入队面板" : "初始基础属性") : kind === "monster" ? "当前快照属性" : "属性加成"}</h3>
            {kind === "candidate" && Boolean(entity.effective_stats) && <p className="scene-note">含公会直接属性加成，条件效果在战斗中生效。</p>}
            <StatTiles stats={stats} modifier={kind === "equipment"} />
            <h3>技能效果</h3>
            <SkillCards skills={entity.skills as GameEntity[] | undefined} />
        </div>
    );
}
function Reward({ value }: { value: Record<string, unknown> | undefined }) {
    if (!value) return <p className="scene-note">奖励未记录</p>;
    return (
        <div className="scene-rewards">
            <span>记录奖励</span>
            <b>金币 {fmt(value.gold as number)}</b>
            <b>经验 {fmt(value.experience as number)}</b>
            {Object.entries((value.materials || {}) as Record<string, number>).map(([key, n]) => (
                <b key={key}>
                    {MATERIALS[key] || key} {n}
                </b>
            ))}
        </div>
    );
}
function Missing({ value }: { value: Record<string, unknown> | undefined }) {
    if (!value || !Object.keys(value).length) return null;
    return (
        <div className="scene-missing">
            {Object.entries(value).map(([key, n]) => (
                <span key={key}>
                    <Lock size={12} />
                    {key === "gold"
                        ? `缺金币 ${n}`
                        : key === "experience" || key === "experience_pool"
                          ? `缺经验 ${n}`
                          : key === "party_size_limit"
                            ? "编队已满"
                            : key === "materials"
                              ? `材料不足：${Object.entries(n as Record<string, number>)
                                    .map(([id, count]) => `${MATERIALS[id] || id} ${count}`)
                                    .join("、")}`
                              : key === "required_upgrade_ids"
                                ? "前置升级未满足"
                                : key === "exclusive_upgrade_ids"
                                  ? "与已解锁分支互斥"
                                  : `${key} 条件未满足`}
                </span>
            ))}
        </div>
    );
}
