import { TOOLS, fmt } from "./model";
import { SLOTS, STAT_NAMES, signed } from "./sceneModel";
import { actionNames as namesFor, actionTarget as targetName, describeInput, readableActionText as readable } from "./actionInputs";
import type { ActionInput } from "./actionInputs";
import { battleResult, describeReturn } from "./actionReturns";
import type { ActionReturn } from "./actionReturns";
import { stateChanges } from "./watchModel";
import type { EventRecord, GameEntity } from "./types";
import type { WatchFrame } from "./watchModel";

export interface ActionEntry {
    kind: "action" | "query" | "system";
    frame: WatchFrame;
    start: number;
    title: string;
    status: "pending" | "succeeded" | "failed" | "recorded";
    summary: string;
    effects: string[];
    input: ActionInput;
    returned: ActionReturn;
    rolledBack: boolean;
}
export function isRefreshPreview(event: EventRecord["event"]): boolean {
    return event.name === "refresh_monsters" && event.arguments?.preview === true;
}
export const ACTION_STATUS = { pending: "执行中", succeeded: "成功", failed: "失败", recorded: "已记录" };
const WARNINGS = new Set(["retry", "turn_retry", "turn_failed", "run_failed", "worker_failed", "checkpoint_failed"]);
const QUERY_FIELDS: Record<string, [string, string]> = {
    get_party: ["adventurers", "位成员"], get_monsters: ["monsters", "个怪物"],
    get_inventory: ["equipment_inventory", "件装备"], get_recruitment: ["recruit_candidates", "位候选"],
    get_upgrades: ["global_upgrades", "项升级"], get_crafting: ["crafting_recipes", "份配方"], get_events: ["events", "条事件"],
};
const RETRIES: Record<string, string> = {
    empty_response: "模型没有返回正文或工具调用，重新请求", end_turn_failed: "回合提交失败，重新请求模型",
    tool_budget_exhausted: "工具预算已用完，要求模型结束回合",
};

function inferredTitle(frame: WatchFrame, names: Map<string, string>): string {
    const e = frame.record.event, result = e.result, args = e.arguments || {};
    const target = (kind: string, value: unknown) => targetName(frame, kind, value, names);
    const member = target("adventurer", args.adventurer_id);
    switch (e.name) {
        case "recruit_adventurer": {
            const recruited = result?.recruited_adventurer || frame.observation?.adventurers.find(a => !frame.before?.adventurers.some(b => b.adventurer_id === a.adventurer_id));
            const name = recruited?.name || target("recruit", args.candidate_id);
            if (name) return `招募 ${name}`;
            break;
        }
        case "allocate_experience": if (member) return `给 ${member} 分配${args.amount != null ? ` ${fmt(args.amount)}` : ""}经验`; break;
        case "equip_item": {
            const item = target("equipment", args.equipment_id ?? args.equipment_instance_id ?? args.instance_id);
            if (member || item) return `${member || "成员未记录"} 装备 ${item || "装备未记录"}`;
            break;
        }
        case "unequip_item": if (member) return `${member} 卸下 ${SLOTS[args.slot] || args.slot || "装备"}`; break;
        case "purchase_upgrade": {
            const item = frame.observation?.global_upgrades?.find(u => u.unlocked && frame.before?.global_upgrades?.some(old => old.upgrade_id === u.upgrade_id && !old.unlocked));
            const name = item?.name || target("upgrade", args.upgrade_id);
            if (name) return `购买升级 ${name}`;
            break;
        }
        case "craft_equipment": {
            const name = target("recipe", args.recipe_id);
            if (name) return `制作 ${name}`;
            break;
        }
        case "configure_tactics": if (member) return `为 ${member} 配置战术`; break;
        case "dismiss_adventurer": if (member) return `解雇 ${member}`; break;
    }
    return TOOLS[e.name] || e.name;
}
function valueText(value: unknown, names: Map<string, string>): string {
    return typeof value === "string" ? readable(value, names) : typeof value === "number" ? fmt(value) : JSON.stringify(value);
}
function observedEffects(frame: WatchFrame, names: Map<string, string>): string[] {
    const result = frame.record.event.result;
    if (result?.ok !== true) return [];
    const changes = result.event?.changes as { kind: string; label: string; before?: unknown; after: unknown }[] | undefined;
    if (changes?.length) return changes.filter(change => change.kind !== "turn").map(change => {
        const label = readable(change.label, names);
        if (change.before == null) return `${label}：${valueText(change.after, names)}`;
        const delta = typeof change.before === "number" && typeof change.after === "number" ? `（${signed(change.after - change.before)}）` : "";
        return `${label} ${valueText(change.before, names)} → ${valueText(change.after, names)}${delta}`;
    });
    // 旧记录没有事件变化清单时，只比较确实保存过的前后快照。
    const { before, observation: after } = frame;
    const effects = stateChanges(before, after).map(text => readable(text, names));
    if (!before || !after || before === after) return effects;
    for (const member of after.adventurers) {
        const old = before.adventurers.find(a => a.adventurer_id === member.adventurer_id);
        if (!old) continue;
        for (const [key, value] of Object.entries(member.effective_stats)) {
            const from = old.effective_stats[key];
            if (from != null && from !== value) effects.push(`${member.name} ${STAT_NAMES[key] || key} ${fmt(from)} → ${fmt(value)}（${signed(value - from)}）`);
        }
        if (old.tactics && member.tactics && JSON.stringify(old.tactics) !== JSON.stringify(member.tactics)) {
            const order = member.tactics.skill_priority as string[];
            const disabled = member.tactics.disabled_skills as string[];
            effects.push(`${member.name} 技能顺序：${order.length ? order.map(id => names.get(id) || id).join(" → ") : "默认顺序"}`);
            effects.push(`${member.name} 停用技能：${disabled.length ? disabled.map(id => names.get(id) || id).join("、") : "无"}`);
        }
    }
    if (before.equipment_inventory && after.equipment_inventory)
        for (const item of after.equipment_inventory)
            if (!before.equipment_inventory.some(old => old.instance_id === item.instance_id)) effects.push(`获得装备 ${item.name}`);
    if (before.global_upgrades && after.global_upgrades)
        for (const item of after.global_upgrades)
            if (item.unlocked && before.global_upgrades.some(old => old.upgrade_id === item.upgrade_id && !old.unlocked)) effects.push(`解锁 ${item.name}`);
    return effects;
}
function querySummary(frame: WatchFrame): string {
    const e = frame.record.event, r = e.result || {}, field = QUERY_FIELDS[e.name];
    if (field && Array.isArray(r[field[0]])) {
        const items = r[field[0]] as GameEntity[];
        const names = items.slice(0, 3).map(item => item.name).filter(Boolean).join("、");
        return `返回 ${items.length} ${field[1]}${names ? `：${names}${items.length > 3 ? "等" : ""}` : ""}`;
    }
    if (e.name === "preview_battle" && r.preview) return `${r.preview.adventurer_name} 对 ${r.preview.monster_name}：模拟${battleResult(r.preview)}，不是实际讨伐`;
    if (e.name === "preview_team_power" && r.rank_score != null) return `试算评分 ${fmt(r.rank_score)}，未执行真实讨伐`;
    if (e.name === "preview_monster_refresh" || isRefreshPreview(e)) return "仅查看刷新候选，未刷新怪物或消耗金币";
    if (e.name === "get_battle_log" && r.battle) return `${r.battle.adventurer_name} 对 ${r.battle.monster_name}的已有战斗记录`;
    return "";
}
export function describeAction(frame: WatchFrame, start = frame.cursor, call?: WatchFrame): ActionEntry {
    // 旧日志的结果可能未重复参数。仅从匹配调用继承，保留真实结果帧与游标。
    const described = frame.record.event.arguments === undefined && call?.record.event.arguments !== undefined
        ? { ...frame, record: { ...frame.record, event: { ...frame.record.event, arguments: call.record.event.arguments } } }
        : frame;
    const e = described.record.event, names = namesFor(described);
    const tool = e.type === "tool_call" || e.type === "tool_result";
    const kind = !tool ? "system" : /^(get_|preview_)/.test(e.name) || isRefreshPreview(e) ? "query" : "action";
    const status = e.type === "tool_call" ? "pending" : frame.failed ? "failed" : e.result?.ok === true ? "succeeded" : "recorded";
    let title = tool ? readable((frame.failed ? "" : e.result?.event?.summary) || inferredTitle(described, names), names) : frame.title;
    if (isRefreshPreview(e)) title = "预览怪物刷新";
    let summary = frame.failed ? readable(String(e.result?.error || e.error || e.failure_reason || frame.summary), names) : "";
    const effects = tool && kind === "action" ? observedEffects(frame, names) : [];
    const returned = describeReturn(described);
    if (status === "pending") summary = "工具已发出，尚未记录执行结果";
    else if (tool && !frame.failed) {
        if (kind === "query") summary = querySummary(described);
        else if (e.name === "write_memo" && e.result?.memo) {
            const memo = e.result.memo;
            if (memo.content != null) summary = "已返回备忘正文，完整内容见业务返回。";
            else if (memo.count != null) summary = `已记录备忘 ${memo.count} 条${memo.dropped_oldest ? `，已丢弃最早 ${memo.dropped_oldest} 条` : ""}`;
        }
        else if (e.name === "end_turn") {
            const battles = e.result?.turn_result?.battles;
            if (Array.isArray(battles)) {
                if (!e.result.event?.summary) title = `结束回合：${battles.length} 场实际讨伐`;
                summary = battles.length ? battles.map(b => `${b.adventurer_name} → ${b.monster_name}：${battleResult(b)}`).join(" · ") : "本回合没有安排讨伐";
            }
        }
    } else if (!tool) summary ||= RETRIES[e.reason] || frame.summary;
    if (tool && !frame.failed && status !== "pending") summary ||= returned.summary;
    return { kind, frame, start, title, status, summary, effects, input: describeInput(described, names), returned, rolledBack: false };
}

/** 展示层合并调用与结果；原始帧和游标不改动，回看仍定位到真实结果帧。 */
export function actionEntries(frames: WatchFrame[]): ActionEntry[] {
    const entries: ActionEntry[] = [];
    const pending: { entry: number; call: WatchFrame }[] = [];
    const rolledBack = frames.some(frame => frame.record.event.type === "turn_retry");
    for (const frame of frames) {
        const e = frame.record.event;
        if (e.type === "tool_result") {
            const index = pending.findIndex(({ call }) => call.record.event.name === e.name &&
                (e.call_id != null ? call.record.event.call_id === e.call_id : call.record.event.call_id == null));
            if (index >= 0) {
                const pair = pending.splice(index, 1)[0];
                entries[pair.entry] = describeAction(frame, pair.call.cursor, pair.call);
                continue;
            }
        }
        const entry = describeAction(frame);
        entries.push(entry);
        if (e.type === "tool_call") pending.push({ entry: entries.length - 1, call: frame });
    }
    if (rolledBack) for (const entry of entries) if (entry.kind === "action") entry.rolledBack = true;
    return entries;
}
export function actionResult(entry: ActionEntry): string {
    if (entry.summary) return entry.summary;
    if (entry.kind === "system") return entry.title;
    if (entry.status === "pending") return "工具已发出，尚未记录执行结果";
    if (entry.status === "failed") return "执行失败，原日志未记录具体原因。";
    if (entry.status === "recorded") return "已记录调用，但原日志未明确记录执行是否成功。";
    return entry.effects.length ? entry.effects.join(" · ") : "已记录操作成功，没有可展示的额外业务返回。";
}
export function actionSummary(entry: ActionEntry): string {
    const facts = [entry.summary, ...entry.effects].filter(Boolean).join(" · ");
    return facts || actionResult(entry);
}
export function recentActions(frames: WatchFrame[]) {
    const entries = actionEntries(frames);
    const actions = entries.filter(entry => entry.kind === "action" || WARNINGS.has(entry.frame.record.event.type) || entry.status === "failed").reverse();
    const queries = entries.filter(entry => entry.kind === "query");
    const queryOnly = queries.length > 0 && !entries.some(entry => entry.kind === "action");
    return { actions, queries, entries, queryOnly };
}
export interface ActionGroup { kind: ActionEntry["kind"]; entries: ActionEntry[] }
export function actionGroups(entries: ActionEntry[]): ActionGroup[] {
    const groups: ActionGroup[] = [];
    const system: ActionEntry[] = [];
    for (const entry of entries) {
        // 技术事件集中收起，不打断连续查询。失败和回滚仍留在业务时间顺序中。
        if (entry.kind === "system" && !WARNINGS.has(entry.frame.record.event.type)) {
            system.push(entry);
            continue;
        }
        const last = groups.at(-1);
        const kind = entry.status === "failed" || WARNINGS.has(entry.frame.record.event.type) ? "action" : entry.kind;
        if (kind !== "action" && last?.kind === kind) last.entries.push(entry);
        else groups.push({ kind, entries: [entry] });
    }
    if (system.length) groups.push({ kind: "system", entries: system });
    return groups;
}
