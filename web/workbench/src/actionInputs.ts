import { MATERIALS, SLOTS } from "./sceneModel";
import type { GameEntity } from "./types";
import type { WatchFrame } from "./watchModel";

export interface InputValue {
    kind: "scalar" | "array" | "object";
    text: string;
    original: string | null;
    children: InputField[];
}
export interface InputField {
    key: string;
    label: string;
    value: InputValue;
}
export type ActionInput = { state: "missing" } | { state: "recorded"; value: InputValue };
export const PLAN_TOOLS: Record<string, string> = {
    craft: "craft_equipment", recruit: "recruit_adventurer", dismiss: "dismiss_adventurer", equip: "equip_item", unequip: "unequip_item",
    purchase_upgrade: "purchase_upgrade", allocate_experience: "allocate_experience", configure_tactics: "configure_tactics", refresh_monsters: "refresh_monsters",
};
const LABELS: Record<string, string> = {
    session_id: "会话", adventurer_id: "成员", candidate_id: "招募候选", monster_id: "怪物",
    equipment_id: "装备", equipment_instance_id: "装备实例", instance_id: "实例", upgrade_id: "天赋 / 升级",
    recipe_id: "制作配方", amount: "分配经验数量", slot: "装备槽位", hunts: "讨伐安排",
    skill_priority: "技能优先级", disabled_skills: "停用技能", locked_monster_ids: "保留的怪物",
    preview: "仅预览", content: "备忘内容", key: "备忘名称", limit: "数量上限", battle_id: "战斗记录",
    since_sequence: "起始事件序号", kind: "预告类型", start_turn: "起始回合", end_turn: "结束回合",
    name_contains: "名称包含", material: "材料", tier: "阶级", entry_index: "条目序号", detail: "详细程度",
    operations: "试算操作", type: "操作类型", material_id: "兑换材料", quantity: "兑换份数",
};
const TARGETS: Record<string, string> = {
    adventurer_id: "adventurer", candidate_id: "recruit", monster_id: "monster", locked_monster_ids: "monster",
    equipment_id: "equipment", equipment_instance_id: "equipment", instance_id: "equipment",
    upgrade_id: "upgrade", recipe_id: "recipe", skill_priority: "skill", disabled_skills: "skill", skill_id: "skill",
};
const KINDS: Record<string, string> = { adventurer: "成员", equipment: "装备", upgrade: "升级", recipe: "配方", recruit: "候选", monster: "怪物", skill: "技能" };

export function actionNames(frame: WatchFrame): Map<string, string> {
    const names = new Map<string, string>(Object.entries(frame.names || {}));
    for (const obs of [frame.before, frame.observation]) {
        if (!obs) continue;
        for (const [items, key] of [[obs.adventurers, "adventurer_id"], [obs.monsters, "monster_id"],
            [obs.equipment_inventory, "instance_id"], [obs.global_upgrades, "upgrade_id"],
            [obs.recruit_candidates, "candidate_id"], [obs.crafting_recipes, "recipe_id"]] as const) {
            for (const item of items || []) names.set((item as GameEntity)[key] as string, item.name!);
        }
        for (const member of obs.adventurers) {
            const skills = [...(member.skills || []), ...((member.tactics?.available_active_skills as GameEntity[] | undefined) || [])];
            for (const skill of skills) names.set(skill.skill_id as string, skill.name!);
        }
    }
    return names;
}
export function readableActionText(text: string, names: Map<string, string>): string {
    return text.replace(/[\w-]+/g, token => names.get(token) || (Object.hasOwn(MATERIALS, token) ? MATERIALS[token] : Object.hasOwn(SLOTS, token) ? SLOTS[token] : token))
        .replace(/\bHP\b/g, "生命").replace(/\bMP\b/g, "法力");
}
export function actionTarget(frame: WatchFrame, kind: string, value: unknown, names: Map<string, string>): string {
    if (value == null) return "";
    const refs = frame.record.event.result?._llm_refs?.[kind] as Record<string, number> | undefined;
    const id = typeof value === "number" ? Object.keys(refs || {}).find(id => refs![id] === value) : String(value);
    return id && names.get(id) || `${KINDS[kind]} #${value}`;
}

export function describeInput(frame: WatchFrame, names: Map<string, string>): ActionInput {
    const raw = frame.record.event.arguments;
    if (raw === undefined) return { state: "missing" };
    function value(raw: unknown, key: string): InputValue {
        if (raw === null) return { kind: "scalar", text: "空值（null）", original: null, children: [] };
        if (Array.isArray(raw)) return { kind: "array", text: raw.length ? `${raw.length} 项` : "空列表 []", original: null,
            children: raw.map((item, index) => ({ key: String(index), label: key === "hunts" ? `派遣 ${index + 1}` : `第 ${index + 1} 项`, value: value(item, key) })) };
        if (typeof raw === "object") {
            const pairs = Object.entries(raw);
            return { kind: "object", text: pairs.length ? "" : "空对象 {}", original: null,
                children: pairs.map(([key, item]) => ({ key, label: Object.hasOwn(LABELS, key) ? LABELS[key] : key, value: value(item, key) })) };
        }
        if (typeof raw === "boolean") return { kind: "scalar", text: raw ? "是（true）" : "否（false）", original: null, children: [] };
        const original = String(raw);
        const target = Object.hasOwn(TARGETS, key) ? TARGETS[key] : undefined;
        const text = target ? actionTarget(frame, target, raw, names) : key === "slot" ? Object.hasOwn(SLOTS, original) ? SLOTS[original] : original : (key === "material" || key === "material_id") ? Object.hasOwn(MATERIALS, original) ? MATERIALS[original] : original : original || '空字符串 ""';
        return { kind: "scalar", text, original: text !== original && (target != null || key === "slot" || (key === "material" || key === "material_id")) ? original : null, children: [] };
    }
    return { state: "recorded", value: value(raw, "") };
}

/** 列表只预览关键输入。详情树保留完整顺序、嵌套结构和原值。 */
export function inputPreview(input: ActionInput): string {
    if (input.state === "missing") return "原日志未记录输入参数";
    function text(value: InputValue): string {
        return value.children.length ? value.children.slice(0, 3).map(field => `${field.label}：${text(field.value)}`).join("，") + (value.children.length > 3 ? "等" : "") : value.text;
    }
    if (!input.value.children.length && input.value.text === "空对象 {}") return "无输入参数";
    const fields = input.value.children;
    const keyFields = fields.filter(field => field.key !== "session_id");
    const shown = keyFields.length ? { ...input.value, children: keyFields } : input.value;
    const preview = text(shown);
    return preview.length > 220 ? preview.slice(0, 220) + "…" : preview;
}
