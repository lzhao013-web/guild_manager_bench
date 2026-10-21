import { useEffect, useLayoutEffect, useRef } from "react";
import type { CSSProperties } from "react";
import {
    Backpack,
    Check,
    Coins,
    Lock,
    MagnifyingGlass,
    Plus,
    Shield,
    Skull,
    Sparkle,
    Storefront,
    Sword,
    TreeStructure,
    UsersThree,
} from "@phosphor-icons/react";
import { fmt } from "./model";
import { SLOTS, materialText, memberChanges, requirementLines, subjectId, subjects } from "./sceneModel";
import type { Selection, SceneSubject } from "./sceneModel";
import {
    KIND_MODULE,
    MODULE_NAMES,
    MODULE_KIND,
} from "./sceneNavigation";
import type { SceneModule, SceneView } from "./sceneNavigation";
import { Portrait, Vital } from "./SceneParts";
import { EquipmentIcon } from "./EquipmentIcon";
import { MonsterBadges } from "./MonsterBadges";
import { AdventurerStatus, adventurerFrame, adventurerStateText, monsterFrame } from "./UnitFrame";
import { monsterClassification } from "./monsterModel";
import { SceneInspector } from "./SceneInspector";
import { UpgradeGraph } from "./UpgradeGraph";
import type { GameEntity, Observation, PartyMember } from "./types";
import "./scene.css";

export function GameScene({
    observation,
    before = null,
    module,
    view,
    onView,
    onNavigate,
    active = true,
}: {
    observation: Observation | null;
    before?: Observation | null;
    module: SceneModule;
    view: SceneView;
    onView: (view: SceneView) => void;
    onNavigate: (module: SceneModule, selection?: Selection, slot?: string) => void;
    active?: boolean;
}) {
    const browser = useRef<HTMLDivElement>(null);
    const detail = useRef<HTMLDivElement>(null);
    const scroll = useRef({ browser: 0, detail: 0 });
    const { selection, search, slot, stock, inventoryMode } = view;
    const filteredInventory = observation?.equipment_inventory?.filter(
        (item) =>
            (item.name || "").toLowerCase().includes(search.toLowerCase()) &&
            (slot === "all" || item.slot === slot) &&
            (stock === "all" ||
                (stock === "equipped" ? item.equipped_by != null : item.equipped_by === null)),
    );
    const filteredRecipes = observation?.crafting_recipes?.filter(recipe =>
        `${recipe.name} ${recipe.output_name}`.toLowerCase().includes(search.toLowerCase()) &&
        (slot === "all" || recipe.output_slot === slot));
    const collection = observation
        ? module === "inventory" ? inventoryMode === "recipes" ? filteredRecipes : filteredInventory : subjects(observation, MODULE_KIND[module])
        : undefined;
    const subject = selection ? collection?.find((item) => subjectId(selection.kind, item) === selection.id) : undefined;
    useEffect(() => {
        if (selection && !subject) onView({ ...view, selection: null });
    }, [selection, subject, onView, view]);
    useLayoutEffect(() => {
        scroll.current.detail = 0;
        if (detail.current) detail.current.scrollTop = 0;
    }, [selection?.kind, selection?.id]);
    useLayoutEffect(() => {
        if (active) {
            if (browser.current) browser.current.scrollTop = scroll.current.browser;
            if (detail.current) detail.current.scrollTop = scroll.current.detail;
        }
    }, [active, !!subject]);
    if (!observation)
        return (
            <div className="scene-unavailable">
                <UsersThree size={40} weight="duotone" />
                <h3>还没有可展示的局面</h3>
                <p>等待初始化，或选择一个已经记录的行动。</p>
            </div>
        );
    function select(next: Selection) {
        if (KIND_MODULE[next.kind] !== module) onNavigate(KIND_MODULE[next.kind], next);
        else onView({ ...view, selection: next });
    }
    function inspect(kind: Selection["kind"], item: SceneSubject) {
        select({ kind, id: subjectId(kind, item) });
    }
    const equippedCount = observation.equipment_inventory?.filter(
        (item) => item.equipped_by != null,
    ).length;
    return (
        <div
            className={`game-scene graphical-scene scene-module-${module}`}
        >
            <div
                className={`scene-module-content ${subject ? "has-selection" : ""} ${module === "upgrades" ? "with-tree" : ""}`}
            >
                {(!subject || module === "inventory" || module === "upgrades") && <div
                    className="scene-canvas"
                    ref={browser}
                    onScroll={(event) => {
                        if (active) scroll.current.browser = event.currentTarget.scrollTop;
                    }}
                >
                    {!subject && <div className="scene-zone-heading">
                        <div>
                            <h3>{MODULE_NAMES[module]}</h3>
                        </div>
                        <span>
                            {subject ? "点选其他对象切换详情" : "点选对象，在此模块内完整查看"}
                        </span>
                    </div>}
                    {module === "inventory" && <nav className="scene-inventory-mode" aria-label="装备模块内容">
                        <button aria-pressed={inventoryMode === "items"} onClick={() => onView({ ...view, inventoryMode: "items", search: "", slot: "all", stock: "all", selection: null })}>装备库存</button>
                        <button aria-pressed={inventoryMode === "recipes"} onClick={() => onView({ ...view, inventoryMode: "recipes", search: "", slot: "all", stock: "all", selection: null })}>制作配方</button>
                    </nav>}
                    {module === "inventory" && (
                        <div className="scene-inventory-toolbar">
                            <label className="scene-search">
                                <MagnifyingGlass size={16} />
                                <input
                                    aria-label={inventoryMode === "recipes" ? "搜索制作配方" : "搜索装备"}
                                    placeholder={inventoryMode === "recipes" ? "按配方或成品名称查找" : "按装备名称查找"}
                                    value={search}
                                    onChange={(e) =>
                                        onView({ ...view, search: e.target.value, selection: null })
                                    }
                                />
                            </label>
                            <select
                                aria-label="筛选装备槽位"
                                value={slot}
                                onChange={(e) =>
                                    onView({ ...view, slot: e.target.value, selection: null })
                                }
                            >
                                <option value="all">所有槽位</option>
                                {Object.entries(SLOTS).map(([key, label]) => (
                                    <option key={key} value={key}>
                                        {label}
                                    </option>
                                ))}
                            </select>
                            {inventoryMode === "items" && <select
                                aria-label="筛选穿戴状态"
                                value={stock}
                                onChange={(e) =>
                                    onView({ ...view, stock: e.target.value, selection: null })
                                }
                            >
                                <option value="all">全部装备</option>
                                <option value="stored">未穿戴</option>
                                <option value="equipped">已穿戴</option>
                            </select>}
                        </div>
                    )}
                    {!subject && module === "party" && (
                        <>
                            <div
                                className="scene-formation"
                                aria-label="编队概览"
                                style={
                                    {
                                        "--party-columns": Math.min(
                                            6,
                                            Math.max(
                                                1,
                                                observation.adventurers.length +
                                                    Number(
                                                        observation.adventurers.length <
                                                            observation.party_size_limit,
                                                    ),
                                            ),
                                        ),
                                    } as CSSProperties
                                }
                            >
                                {observation.adventurers.map((member) => (
                                    <MemberCard
                                        key={member.adventurer_id}
                                        member={member}
                                        before={before}
                                        onClick={() => inspect("member", member)}
                                    />
                                ))}
                                {observation.adventurers.length < observation.party_size_limit && (
                                    <button
                                        className="scene-open-slot"
                                        onClick={() => onNavigate("recruitment")}
                                    >
                                        <Plus size={26} />
                                        <strong>
                                            {observation.adventurers.length
                                                ? `还有 ${observation.party_size_limit - observation.adventurers.length} 个空位`
                                                : "此时尚未招募冒险者"}
                                        </strong>
                                        <span>查看招募候选</span>
                                    </button>
                                )}
                            </div>
                        </>
                    )}
                    {!subject && module === "monsters" && (
                        <>
                            {observation.monsters === undefined ? (
                                <p className="scene-note">该快照未记录怪物</p>
                            ) : observation.monsters.length ? (
                                <div className="scene-monster-grid">
                                    {observation.monsters.map((monster) => (
                                        <button
                                            key={monster.monster_id as string}
                                            className="scene-monster-card actor-frame"
                                            {...monsterFrame(monster)}
                                            aria-label={`查看怪物 ${monster.name}`}
                                            aria-description={monsterClassification(monster)}
                                            onClick={() => inspect("monster", monster)}
                                        >
                                            <Portrait
                                                monster
                                                id={monster.archetype_id as string}
                                                name={monster.name!}
                                            />
                                            <div>
                                                <MonsterBadges monster={monster} />
                                                <strong>{monster.name}</strong>
                                                <ItemStats item={monster} />
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            ) : (
                                <p className="scene-zone-empty">
                                    {observation.finished
                                        ? "本局已结束，没有新的怪物。"
                                        : "此时没有怪物记录。"}
                                </p>
                            )}
                        </>
                    )}
                    {!subject && module === "inventory" && inventoryMode === "items" && (
                        <>
                            <p className="scene-note">
                                {observation.equipment_inventory
                                    ? `${observation.equipment_inventory.length} 件装备，${equippedCount} 件已穿戴`
                                    : "该快照未记录库存"}
                            </p>
                            {observation.equipment_inventory !== undefined && (
                                <div className="scene-equipment-grid">
                                    {filteredInventory!.map((item) => (
                                        <button
                                            className={`scene-item-card ${item.equipped_by ? "is-equipped" : ""}`}
                                            key={item.instance_id}
                                            onClick={() => inspect("equipment", item)}
                                            aria-label={`查看装备 ${item.name}`}
                                        >
                                            <span className="equipment-glyph">
                                                <EquipmentIcon templateId={item.template_id} slot={item.slot!} size={42} />
                                            </span>
                                            <strong>{item.name}</strong>
                                            <span className="scene-note">
                                                {SLOTS[item.slot!] || item.slot}
                                            </span>
                                            <ItemStats item={item} />
                                            {item.equipped_by != null && (
                                                <span className="scene-tag">
                                                    {observation.adventurers.find(
                                                        (member) =>
                                                            member.adventurer_id ===
                                                            item.equipped_by,
                                                    )?.name || "已穿戴"}
                                                </span>
                                            )}
                                        </button>
                                    ))}
                                    {!observation.equipment_inventory.length ? (
                                        <p className="scene-zone-empty">此时仓库里没有装备</p>
                                    ) : (
                                        !filteredInventory!.length && (
                                            <p className="scene-zone-empty">
                                                没有符合筛选条件的装备
                                            </p>
                                        )
                                    )}
                                </div>
                            )}
                        </>
                    )}
                    {!subject && module === "inventory" && inventoryMode === "recipes" && (
                        observation.crafting_recipes === undefined ? <p className="scene-note">该快照未记录制作配方</p> :
                        <div className="scene-equipment-grid">
                            {filteredRecipes!.map(recipe => <button key={recipe.recipe_id} className="scene-item-card scene-recipe-card" aria-label={`查看制作配方 ${recipe.output_name}`}
                                onClick={() => inspect("recipe", recipe)}>
                                <span className="equipment-glyph"><EquipmentIcon templateId={recipe.output_template_id} slot={recipe.output_slot} size={42} /></span>
                                <strong>{recipe.output_name}</strong><span className="scene-note">{SLOTS[recipe.output_slot] || recipe.output_slot}</span>
                                <span className="candidate-price"><Coins size={13} />{fmt(recipe.gold_cost)}</span>
                                <small className="scene-note">{materialText(recipe.material_costs) || "无需材料"}</small>
                                <span className={`scene-availability ${recipe.can_craft ? "available" : ""}`}>{recipe.can_craft ? "可制作" : requirementLines(recipe.missing).join("、")}</span>
                            </button>)}
                            {!filteredRecipes!.length && <p className="scene-zone-empty">{observation.crafting_recipes.length ? "没有符合筛选条件的制作配方" : "该位置没有制作配方"}</p>}
                        </div>
                    )}
                    {!subject && module === "recruitment" && (
                        <>
                            {observation.recruit_candidates === undefined ? (
                                <p className="scene-note">该快照未记录招募候选</p>
                            ) : (
                                <div className="scene-candidate-grid">
                                    {observation.recruit_candidates.map((candidate) => (
                                        <button
                                            className="scene-candidate-card"
                                            key={candidate.candidate_id as string}
                                            aria-label={`查看招募候选 ${candidate.name}`}
                                            onClick={() => inspect("candidate", candidate)}
                                        >
                                            <Portrait
                                                id={candidate.template_id}
                                                name={candidate.name!}
                                            />
                                            <div className="candidate-card-info">
                                                <strong>{candidate.name}</strong>
                                                <span className="candidate-price">
                                                    <Coins size={14} />
                                                    {fmt(candidate.recruit_gold as number)}
                                                </span>
                                                <ItemStats item={candidate} />
                                                <span
                                                    className={`scene-availability ${candidate.can_recruit ? "available" : ""}`}
                                                >
                                                    {candidate.can_recruit ? (
                                                        <Check size={12} />
                                                    ) : (
                                                        <Lock size={12} />
                                                    )}
                                                    {candidate.can_recruit
                                                        ? "可招募"
                                                        : "条件未满足"}
                                                </span>
                                            </div>
                                        </button>
                                    ))}
                                    {!observation.recruit_candidates.length && (
                                        <p className="scene-zone-empty">此时没有招募候选</p>
                                    )}
                                </div>
                            )}
                        </>
                    )}
                    {module === "upgrades" &&
                        (observation.global_upgrades === undefined ? (
                            <p className="scene-note">该快照未记录升级</p>
                        ) : (
                            <UpgradeGraph
                                upgrades={observation.global_upgrades}
                                selectedId={subject ? selection!.id : null}
                                onSelect={(upgrade) => inspect("upgrade", upgrade)}
                                active={active}
                            />
                        ))}
                </div>}
                {subject && (
                    <div
                        className="scene-inspector-scroll"
                        ref={detail}
                        onScroll={(event) => {
                            if (active) scroll.current.detail = event.currentTarget.scrollTop;
                        }}
                    >
                        <SceneInspector
                            observation={observation}
                            collection={collection!}
                            selection={selection}
                            onSelect={select}
                            onClose={() => onView({ ...view, selection: null })}
                            onInventory={(filter) => onNavigate("inventory", undefined, filter)}
                        />
                    </div>
                )}
            </div>
        </div>
    );
}

function MemberCard({
    member,
    before,
    onClick,
}: {
    member: PartyMember;
    before: Observation | null;
    onClick: () => void;
}) {
    const previous = before?.adventurers.find((m) => m.adventurer_id === member.adventurer_id);
    const changes = memberChanges(member, before);
    const equipped = member.equipment_slots?.filter((slot) => slot.item != null).length;
    return (
        <button
            className={`scene-member-card spectator-member actor-frame ${changes.length ? "has-change" : ""}`}
            {...adventurerFrame(member)}
            aria-label={`查看成员 ${member.name}`}
            aria-description={adventurerStateText(member)}
            onClick={onClick}
        >
            <div className="member-art">
                <Portrait id={member.template_id} name={member.name} />
                <span className="member-level mono">Lv. {member.level}</span>
                <AdventurerStatus member={member} />
                {changes.length > 0 && <span className="member-change-label">{changes[0]}</span>}
            </div>
            <div className="member-card-info">
                <strong>{member.name}</strong>
                <Vital
                    label="生命"
                    value={member.resources.current_hp}
                    max={member.effective_stats.hp}
                    delta={
                        previous
                            ? member.resources.current_hp - previous.resources.current_hp
                            : undefined
                    }
                />
                <Vital
                    label="法力"
                    value={member.resources.current_mp}
                    max={member.effective_stats.mp}
                    mana
                    delta={
                        previous
                            ? member.resources.current_mp - previous.resources.current_mp
                            : undefined
                    }
                />
                <div className="scene-mini-stats">
                    <span>
                        <Sword size={12} />
                        {fmt(member.effective_stats.attack)}
                    </span>
                    <span>
                        <Shield size={12} />
                        {fmt(member.effective_stats.defense)}
                    </span>
                    <span>
                        <Sparkle size={12} />
                        {fmt(member.effective_stats.speed)}
                    </span>
                </div>
                <span className="member-loadout">
                    装备 {equipped ?? "未知"} / 技能 {member.skills?.length ?? "未知"}
                </span>
            </div>
        </button>
    );
}
function ItemStats({ item }: { item: GameEntity }) {
    const stats = (item.stats || item.base_stats) as Record<string, number> | undefined;
    return (
        <div className="scene-mini-stats">
            {stats ? (
                Object.entries(stats)
                    .filter(([, value]) => value !== 0)
                    .slice(0, 3)
                    .map(([key, value]) => (
                        <span key={key}>
                            {(
                                {
                                    hp: "生命上限",
                                    mp: "法力上限",
                                    attack: "攻击",
                                    defense: "防御",
                                    speed: "速度",
                                    recovery: "回血",
                                    mp_recovery: "回魔",
                                } as Record<string, string>
                            )[key] || key}{" "}
                            {fmt(value)}
                        </span>
                    ))
            ) : (
                <span>属性未记录</span>
            )}
        </div>
    );
}
