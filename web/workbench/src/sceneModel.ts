import { fmt } from "./model";
import type { GameEntity, Observation, PartyMember } from "./types";

export const STAT_NAMES: Record<string, string> = {
    hp: "生命上限",
    mp: "法力上限",
    attack: "攻击",
    defense: "防御",
    speed: "速度",
    recovery: "战后回血",
    mp_recovery: "战后回魔",
};
export const SLOTS: Record<string, string> = {
    main_hand: "主手",
    off_hand: "副手",
    two_hand: "双手",
    boots: "鞋靴",
    helmet: "头盔",
    armor: "护甲",
    accessory: "饰品",
    ultimate: "终极装备",
};
export const MATERIALS: Record<string, string> = {
    iron_ore: "铁矿石",
    wood: "木材",
    leather: "皮革",
    herb: "草药",
    bone: "骨头",
    beast_hide: "兽皮",
    sharp_claw: "利爪",
    arcane_dust: "奥术之尘",
    spider_silk: "蛛丝",
    mithril_shard: "秘银碎片",
    dragon_scale: "龙鳞",
    demon_core: "恶魔核心",
    soul_shard: "灵魂碎片",
    dragon_blood: "龙血",
    siege_core: "破城核心",
    aegis_core: "不屈核心",
    renewal_core: "轮回核心",
};
export const BRANCHES: Record<string, string> = {
    common: "公共发展",
    economy: "经济建设",
    martial: "武备",
    arcane: "奥术",
    survival: "生存",
    magic: "魔法",
    defense: "防御",
};
export type Selection = {
    kind: "member" | "monster" | "equipment" | "candidate" | "upgrade" | "recipe";
    id: string;
};
export type SceneSubject = PartyMember | GameEntity;
const KEYS = {
    member: "adventurer_id",
    monster: "monster_id",
    equipment: "instance_id",
    candidate: "candidate_id",
    upgrade: "upgrade_id",
    recipe: "recipe_id",
} as const;
export function subjectId(kind: Selection["kind"], item: SceneSubject): string {
    return (item as Record<string, unknown>)[KEYS[kind]] as string;
}
export function subjects(
    observation: Observation,
    kind: Selection["kind"],
): SceneSubject[] | undefined {
    return {
        member: observation.adventurers,
        monster: observation.monsters,
        equipment: observation.equipment_inventory,
        candidate: observation.recruit_candidates,
        upgrade: observation.global_upgrades,
        recipe: observation.crafting_recipes,
    }[kind];
}
export function resolveSubject(
    observation: Observation,
    selection: Selection,
): SceneSubject | undefined {
    return subjects(observation, selection.kind)?.find(
        (item) => subjectId(selection.kind, item) === selection.id,
    );
}
export function materialText(materials: Record<string, number>): string {
    return Object.entries(materials).map(([id, count]) => `${MATERIALS[id] || id} ${fmt(count)}`).join("、");
}
export function requirementLines(missing: Record<string, unknown> | undefined, upgrades?: GameEntity[]): string[] {
    return Object.entries(missing || {}).map(([key, value]) => {
        if (key === "gold") return `缺金币 ${fmt(value as number)}`;
        if (key === "experience" || key === "experience_pool") return `缺经验 ${fmt(value as number)}`;
        if (key === "party_size_limit") return "编队已满";
        if (key === "materials") return `缺材料：${materialText(value as Record<string, number>)}`;
        if (key in MATERIALS && typeof value === "number") return `缺${MATERIALS[key]} ${fmt(value)}`;
        if (key === "required_upgrade_ids" || key === "exclusive_upgrade_ids") {
            const names = (value as string[]).map(id => upgrades?.find(upgrade => upgrade.upgrade_id === id)?.name || id).join("、");
            return `${key === "required_upgrade_ids" ? "缺前置" : "互斥锁定"}：${names}`;
        }
        return `${key} 条件未满足`;
    });
}
export const signed = (value: number) => `${value > 0 ? "+" : ""}${fmt(value)}`;
export function memberChanges(member: PartyMember, before: Observation | null): string[] {
    if (!before) return [];
    const previous = before.adventurers.find((m) => m.adventurer_id === member.adventurer_id);
    if (!previous) return ["新成员"];
    const changes: string[] = [];
    if (previous.level !== member.level)
        changes.push(`等级 ${signed(member.level - previous.level)}`);
    if (JSON.stringify(previous.equipment_slots) !== JSON.stringify(member.equipment_slots))
        changes.push("装备变化");
    if (member.resources.current_hp !== previous.resources.current_hp)
        changes.push(`HP ${signed(member.resources.current_hp - previous.resources.current_hp)}`);
    if (member.resources.current_mp !== previous.resources.current_mp)
        changes.push(`MP ${signed(member.resources.current_mp - previous.resources.current_mp)}`);
    return changes;
}
export function statEntries(stats: Record<string, number> | undefined, nonzero = false) {
    return Object.entries(stats || {}).filter(([, value]) => !nonzero || value !== 0);
}
export interface SkillCondition {
    type: string;
    value: number | null;
    conditions: SkillCondition[];
}
export interface SkillEffect {
    type: string;
    value: number | null;
    minimum?: number;
    stat: string | null;
    target: string;
    status: { name: string; duration: number; effects: SkillEffect[] } | null;
}
export interface SceneSkill extends GameEntity {
    skill_id: string;
    kind: string;
    trigger?: "action" | "action_start";
    mp_cost: number;
    free: boolean;
    once_per_battle: boolean;
    condition: SkillCondition;
    effects: SkillEffect[];
}
const percent = (value: number | null) =>
    value == null
        ? "未记录"
        : `${Intl.NumberFormat("zh-CN", { maximumFractionDigits: 8 }).format(value * 100)}%`;
export function conditionText(condition: SkillCondition): string {
    const { type, value } = condition;
    if (type === "always") return "无额外触发条件";
    if (type === "all" || type === "any")
        return `${type === "all" ? "同时满足" : "满足任一"}：${condition.conditions.map(conditionText).join("，")}`;
    if (type === "action_index_lte") return `行动序号不超过 ${value}`;
    if (type === "action_index_gte") return `行动序号至少为 ${value}`;
    const parts = /^(self|target)_(hp|mp)_pct_(lte|gte)$/.exec(type);
    if (parts)
        return `${parts[1] === "self" ? "自身" : "目标"}${parts[2] === "hp" ? "生命" : "法力"}${parts[3] === "lte" ? "不高于" : "不低于"} ${percent(value)}`;
    return `${type}${value == null ? "" : ` ${value}`}`;
}
export function effectText(effect: SkillEffect): string {
    const { type, value } = effect;
    const target = effect.target === "self" ? "自身" : "目标";
    if (type === "damage_multiplier") return `造成普通攻击伤害的 ${value} 倍`;
    if (type === "damage_bonus") return `额外增加 ${value} 点伤害`;
    if (type === "true_damage") return `造成 ${value} 点无视防御伤害`;
    if (type === "atk_ratio_damage") return `造成攻击力 ${percent(value)} 的无视防御伤害`;
    if (type === "self_damage") return `自身受到 ${value} 点伤害`;
    if (type === "heal") return `为${target}恢复 ${value} 点生命`;
    if (type === "heal_percent") return `为${target}恢复 ${percent(value)} 最大生命`;
    if (type === "mp_restore") return `为${target}恢复 ${value} 点法力`;
    if (type === "mp_restore_percent") return `为${target}恢复 ${percent(value)} 最大法力${effect.minimum ? `，至少 ${effect.minimum} MP` : ""}`;
    if (type === "stat_bonus")
        return `${target}${STAT_NAMES[effect.stat!] || effect.stat} ${signed(value!)}`;
    if (type === "stat_multiplier")
        return `${target}${STAT_NAMES[effect.stat!] || effect.stat} × ${value}`;
    if (type === "apply_status" && effect.status)
        return `对${target}施加「${effect.status.name}」，持续 ${effect.status.duration}：${effect.status.effects.map(effectText).join("，")}`;
    return `${type}${value == null ? "" : ` ${value}`}`;
}
export function upgradeDepths(upgrades: GameEntity[]): Map<string, number> {
    const byId = new Map(upgrades.map((u) => [u.upgrade_id as string, u]));
    const depths = new Map<string, number>();
    // 前置来自游戏已校验的无环升级定义，缺失的旧版前置仍由界面按原 ID 展示。
    function visit(id: string): number {
        if (depths.has(id)) return depths.get(id)!;
        const parents = (byId.get(id)?.required_upgrade_ids || []) as string[];
        const depth = parents.length
            ? 1 + Math.max(...parents.filter((parent) => byId.has(parent)).map(visit), -1)
            : 0;
        depths.set(id, depth);
        return depth;
    }
    for (const id of byId.keys()) visit(id);
    return depths;
}
