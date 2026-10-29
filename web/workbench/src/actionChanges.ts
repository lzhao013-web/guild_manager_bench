import type { ActionEntry } from "./actionModel";
import { actionNames, readableActionText } from "./actionInputs";
import { fmt } from "./model";
import type { GameEntity } from "./types";
import { MATERIALS, SLOTS, STAT_NAMES, signed } from "./sceneModel";

export interface ActionChange { kind: string; label: string; before?: unknown; after: unknown }
export interface ChangedMember { name: string; level?: ActionChange; stats: ActionChange[]; configuration: ActionChange[] }
export interface ActionChanges {
    preview: boolean;
    resources: ActionChange[];
    members: ChangedMember[];
    other: ActionChange[];
    changesRecorded: boolean;
}

/** 只比较本条记录保存的快照。 */
function snapshotChanges(entry: ActionEntry): ActionChange[] | null {
    const { before, observation: after } = entry.frame;
    if (!before || !after || before === after) return null;
    const changes: ActionChange[] = [];
    const add = (kind: string, label: string, from: unknown, to: unknown) => {
        if (JSON.stringify(from) !== JSON.stringify(to)) changes.push({ kind, label, before: from, after: to });
    };
    add("resource", "金币", before.gold, after.gold);
    add("resource", "经验池", before.experience_pool, after.experience_pool);
    add("resource", "队伍人数上限", before.party_size_limit, after.party_size_limit);
    if (before.material_exchange && after.material_exchange) add("resource", "兑换剩余额度", before.material_exchange.remaining_points, after.material_exchange.remaining_points);
    for (const id of new Set([...Object.keys(before.materials), ...Object.keys(after.materials)]))
        add("material", MATERIALS[id] || id, before.materials[id] || 0, after.materials[id] || 0);
    for (const member of after.adventurers) {
        const old = before.adventurers.find(item => item.adventurer_id === member.adventurer_id);
        if (!old) { changes.push({ kind: "adventurer", label: "新冒险者", after: member.name }); continue; }
        add("adventurer", `${member.name} 等级`, old.level, member.level);
        add("adventurer", `${member.name} 当前生命`, old.resources.current_hp, member.resources.current_hp);
        add("adventurer", `${member.name} 当前法力`, old.resources.current_mp, member.resources.current_mp);
        for (const [key, value] of Object.entries(member.effective_stats))
            if (old.effective_stats[key] != null) add("adventurer", `${member.name} ${STAT_NAMES[key] || key}`, old.effective_stats[key], value);
        if (old.equipment_slots && member.equipment_slots) {
            const changed = member.equipment_slots.filter(slot => {
                const previous = old.equipment_slots!.find(item => item.slot === slot.slot);
                return JSON.stringify(previous?.item) !== JSON.stringify(slot.item);
            });
            const slots = (items: GameEntity[]) => changed.map(slot => {
                const gear = items.find(item => item.slot === slot.slot)!.item as GameEntity | null;
                return `${SLOTS[String(slot.slot)] || slot.slot}：${gear === null ? "空槽位" : gear.name}`;
            }).join("、");
            if (changed.length) add("adventurer", `${member.name} 装备`, slots(old.equipment_slots), slots(member.equipment_slots));
        }
        if (old.tactics && member.tactics) {
            add("adventurer", `${member.name} 技能顺序`, old.tactics.skill_priority, member.tactics.skill_priority);
            add("adventurer", `${member.name} 停用技能`, old.tactics.disabled_skills, member.tactics.disabled_skills);
        }
    }
    for (const member of before.adventurers)
        if (!after.adventurers.some(item => item.adventurer_id === member.adventurer_id)) changes.push({ kind: "adventurer", label: "成员离队", after: member.name });
    if (before.equipment_inventory && after.equipment_inventory)
        for (const item of after.equipment_inventory) {
            const old = before.equipment_inventory.find(old => old.instance_id === item.instance_id);
            if (!old) changes.push({ kind: "equipment", label: "获得装备", after: item.name });
            else add("equipment", `${item.name} 装备者`, old.equipped_by, item.equipped_by);
        }
    if (before.global_upgrades && after.global_upgrades)
        for (const item of after.global_upgrades)
            if (item.unlocked && before.global_upgrades.some(old => old.upgrade_id === item.upgrade_id && !old.unlocked)) changes.push({ kind: "upgrade", label: "解锁加成", after: item.name });
    return changes;
}

function netChanges(changes: ActionChange[]): ActionChange[] {
    const merged = new Map<string, ActionChange>();
    for (const change of changes) {
        const key = JSON.stringify([change.kind, change.label]);
        const old = merged.get(key);
        // 新增对象没有 before，同名产物也各自保留。
        if (change.before === undefined) merged.set(`${key}:${merged.size}`, { ...change });
        else merged.set(key, old ? { ...change, before: old.before } : { ...change });
    }
    return [...merged.values()].filter(change => change.before === undefined || JSON.stringify(change.before) !== JSON.stringify(change.after));
}

export function describeChanges(entry: ActionEntry): ActionChanges {
    const preview = entry.event.name === "preview_team_power";
    const r = entry.event.result;
    const view: ActionChanges = { preview, resources: [], members: [], other: [], changesRecorded: false };
    if (entry.status !== "succeeded" || r?.already_executed === true || entry.kind !== "action" && !preview) return view;
    const names = actionNames(entry.frame);
    const label = (change: ActionChange) => normalizeLabel(readableActionText(change.label, names));
    let changes: ActionChange[] | null;
    if (preview) changes = r?.changes ?? null;
    else {
        const recorded: ActionChange[] | null = entry.event.name === "execute_plan"
            ? r?.events?.some((event: Record<string, any>) => Array.isArray(event.changes))
                ? netChanges(r.events.flatMap((event: Record<string, any>) => event.changes || [])) : null
            : r?.event?.changes ?? null;
        const snapshots = snapshotChanges(entry);
        changes = recorded === null ? snapshots : [...recorded, ...(snapshots || []).filter(change => !recorded.some(item => item.kind === change.kind && label(item) === label(change)))];
    }
    view.changesRecorded = changes !== null;
    const members = new Map<string, ChangedMember>();
    for (const change of changes || []) {
        if (change.kind === "turn") continue;
        const readable = { ...change, label: label(change) };
        if (change.kind === "resource" || change.kind === "material") { view.resources.push(readable); continue; }
        const match = change.kind === "adventurer" && readable.label.match(/^(.*) (等级|经验|当前生命|当前法力|生命上限|法力上限|攻击|防御|速度|战后回血|战后回魔|装备|技能顺序|停用技能)$/);
        if (!match) { view.other.push(readable); continue; }
        const [, name, fieldLabel] = match;
        const member = members.get(name) || { name, stats: [], configuration: [] };
        const field = { ...readable, label: fieldLabel };
        if (fieldLabel === "等级") member.level = field;
        else if (["装备", "技能顺序", "停用技能"].includes(fieldLabel)) member.configuration.push(field);
        else member.stats.push(field);
        members.set(name, member);
    }
    view.members = [...members.values()];
    return view;
}
function normalizeLabel(label: string): string {
    return label.replace(/ 最大 HP$| 最大 生命$/, " 生命上限").replace(/ 最大 MP$| 最大 法力$/, " 法力上限")
        .replace(/ HP$| 生命$/, " 当前生命").replace(/ MP$| 法力$/, " 当前法力");
}
export function changeValue(value: unknown, entry: ActionEntry, label = ""): string {
    const names = actionNames(entry.frame);
    if (value == null) return label.endsWith("装备者") ? "未穿戴" : "未记录";
    if (Array.isArray(value)) return value.length ? value.map(item => readableActionText(String(item), names)).join(label === "技能顺序" ? " → " : "、") : label === "技能顺序" ? "默认顺序" : "无";
    return typeof value === "number" ? fmt(value) : readableActionText(String(value), names).replace(/^(.*) \(\1\)$/, "$1");
}
export function changeDelta(change: ActionChange, entry: ActionEntry): string {
    return typeof change.before === "number" && typeof change.after === "number" ? signed(change.after - change.before) : changeValue(change.after, entry, change.label);
}
