import { actionNames, readableActionText } from "./actionInputs";
import { MATERIALS, requirementLines, SLOTS, STAT_NAMES } from "./sceneModel";
import { fmt } from "./model";
import type { WatchFrame } from "./watchModel";

export type ReturnKind = "members" | "equipment" | "upgrades" | "monsters" | "recruits" | "recipes" | "tactics" | "battles" | "events" | "forecast";
export interface ReturnTable {
    kind: ReturnKind;
    title: string;
    columns: string[];
    rows: string[][];
    records: Record<string, any>[];
}
export interface ActionReturn {
    summary: string;
    tables: ReturnTable[];
    fields: Record<string, unknown>;
    preview: boolean;
}
export const RETURN_LABELS: Record<string, string> = {
    ...STAT_NAMES, ...MATERIALS, name: "名称", adventurer_id: "成员编号", monster_id: "怪物编号", instance_id: "装备实例",
    template_id: "类型编号", upgrade_id: "天赋编号", recipe_id: "配方编号", slot: "槽位", stats: "属性", base_stats: "基础属性",
    effective_stats: "实际属性", resources: "当前资源", current_hp: "当前生命", current_mp: "当前法力", level: "等级", experience: "成员经验",
    skills: "技能", skill_id: "技能编号", description: "说明", effects: "效果", gold: "金币", experience_pool: "未分配经验池", materials: "材料",
    gold_cost: "金币费用", material_costs: "材料费用", experience_cost: "经验费用", recruit_gold: "招募费用", can_recruit: "可招募",
    can_craft: "可制作", can_purchase: "可解锁", unlocked: "已解锁", missing: "缺少条件", output_stats: "成品属性", output_name: "成品名称",
    equipped_by: "穿戴者", allowed_class_names: "职业限制", tactics: "战术", skill_priority: "技能顺序", disabled_skills: "停用技能",
    available_active_skills: "可用主动技能", turn: "回合", max_turns: "回合上限", party_size: "队伍人数", party_size_limit: "队伍容量",
    sequence: "事件序号", event_type: "事件类型", summary: "摘要", changes: "实际变化", before: "之前", after: "之后", label: "项目",
    crafted_equipment_ids: "本回合制作的装备", purchased_upgrade_ids: "本回合购买的天赋", recruited_adventurer_ids: "本回合招募的成员",
    reward: "奖励", base_reward: "基础奖励", tier: "阶级", branch: "分支", party_size_bonus: "编队容量加成", traits: "特性",
    source: "记录来源", kind: "查询类型", detail: "详细程度", current_turn: "当前回合", start_turn: "起始回合", end_turn: "结束回合",
    offset: "本页起点", limit: "本页上限", total_matches: "全部匹配数量", next_offset: "下一页起点", forecast_ref: "未来条目引用",
    record_count: "战斗记录数量", battle_index: "战斗序号", adventurer_name: "成员", monster_name: "怪物", won: "获胜", outcome: "战斗结果",
    reason: "结束原因", actions_taken: "行动数", time_elapsed: "战斗耗时", event_count: "事件数", total_events: "整场事件数", matched_events: "匹配事件数",
    action_index: "行动序号", actor_side: "行动方", target_side: "目标方", action_type: "动作类型", skill_name: "技能名称", status_name: "状态名称",
    mp_restored: "自身实际恢复MP", trigger: "触发时点", minimum: "最低回复量",
    hunt_gold_bonus_percent: "讨伐金币加成百分比", elite_boss_gold_bonus_percent: "高阶金币额外加成百分比", monster_refresh_discount_percent: "刷新基础价折扣百分比",
    damage: "伤害", target_hp: "目标剩余生命", healing: "治疗", healing_target_side: "治疗对象", healing_target_hp: "治疗后生命",
    statistics: "战斗统计", basic_attack_events: "独立普攻次数", skill_uses: "技能释放次数", direct_damage: "直接伤害",
    status_damage_taken: "自身状态伤害", healing_received: "实际收到的治疗", left: "冒险者方", right: "怪物方",
    rank_score: "试算战力", simulation_only: "仅模拟", can_afford: "费用是否满足", monster_refresh: "怪物刷新状态",
    plan_id: "方案凭据", operation_count: "方案步数", required_calls: "执行所需最低预算余额",
    remaining_calls: "当前剩余预算", can_execute: "当前可执行", already_executed: "已提交，本次未重复执行",
    current_rank_score: "当前真实战力", projected_rank_score: "方案战力", rank_score_delta: "战力变化",
    next_gold_cost: "下次刷新费用", content: "备忘正文", count: "备忘数量", dropped_oldest: "已丢弃旧备忘数量",
};
export const returnLabel = (key: string) => Object.hasOwn(RETURN_LABELS, key) ? RETURN_LABELS[key] : key;
const SIDES: Record<string, string> = { left: "冒险者", right: "怪物", preview: "预览记录，未实际执行", actual: "真实战斗记录", basic: "普攻", basic_attack: "普攻", skill: "技能", status: "状态结算" };
const number = (value: unknown) => value == null ? "未记录" : String(value);
export function returnStats(value: Record<string, unknown> | undefined, omit: string[] = []): string {
    if (value == null) return "未记录";
    const fields = Object.entries(value).filter(([key]) => !omit.includes(key));
    if (!fields.length) return "无其他属性项";
    const nonzero = fields.filter(([, value]) => value !== 0);
    return nonzero.length ? nonzero.map(([key, value]) => `${STAT_NAMES[key] || key} ${number(value)}`).join("\n") : "各项均为 0";
}
const price = (item: Record<string, any>) => [item.gold_cost != null ? `金币 ${item.gold_cost}` : item.recruit_gold != null ? `金币 ${item.recruit_gold}` : "",
    item.experience_cost != null ? `经验 ${item.experience_cost}` : "", item.material_costs ? Object.entries(item.material_costs).map(([key, val]) => `${MATERIALS[key] || key} ${val}`).join("、") : ""].filter(Boolean).join(" · ") || "未记录";
const flag = (value: unknown, yes: string, no: string) => value === true ? yes : value === false ? no : "未记录";
export function battleResult(battle: Record<string, any>): string {
    return typeof battle.won === "boolean" ? battle.won ? "胜利" : "失败" :
        ({ left_win: "胜利", right_win: "失败", draw: "平局" } as Record<string, string>)[battle.combat?.outcome || battle.outcome] || "结果未记录";
}

export function summarizeBattles(records: Record<string, any>[]) {
    const battles = records.map(battle => ({ name: `${battle.adventurer_name || "成员未记录"} → ${battle.monster_name || "怪物未记录"}`, result: battleResult(battle) }));
    const wins = battles.filter(battle => battle.result === "胜利").length;
    const losses = battles.filter(battle => battle.result === "失败").length;
    const reward = (key: string) => records.every(battle => typeof battle.reward?.[key] === "number")
        ? records.reduce((sum, battle) => sum + battle.reward[key], 0) : null;
    const materials = new Map<string, number>();
    const materialsKnown = records.every(battle => battle.reward?.materials != null);
    if (materialsKnown) for (const battle of records) for (const [id, amount] of Object.entries(battle.reward.materials))
        if (typeof amount === "number") materials.set(id, (materials.get(id) || 0) + amount);
    return { battles, wins, losses, unknown: battles.length - wins - losses, gold: reward("gold"), experience: reward("experience"),
        materials: materialsKnown ? [...materials].filter(([, value]) => value !== 0).map(([id, value]) => `${MATERIALS[id] || id} +${fmt(value)}`) : null };
}

export function returnNames(frame: WatchFrame): Map<string, string> {
    const names = actionNames(frame), r = frame.record.event.result || {}, output = r._action_output || {};
    for (const items of [r.adventurers, r.monsters, r.equipment_inventory, r.global_upgrades, r.recruit_candidates, r.crafting_recipes,
        output.adventurers, output.equipment_inventory, output.global_upgrades, output.dismissed_adventurers])
        for (const item of items || []) {
            for (const key of ["adventurer_id", "monster_id", "instance_id", "upgrade_id", "candidate_id", "recipe_id"])
                if (item[key] != null && item.name) names.set(String(item[key]), item.name);
            for (const skill of [...(item.skills || []), ...(item.tactics?.available_active_skills || [])]) if (skill.name) names.set(skill.skill_id, skill.name);
        }
    return names;
}

export function describeReturn(frame: WatchFrame): ActionReturn {
    const event = frame.record.event, r = event.result;
    const view: ActionReturn = { summary: "", tables: [], fields: {}, preview: event.name?.startsWith("preview_") || event.name === "refresh_monsters" && event.arguments?.preview === true || r?.simulation_only === true || r?.source === "preview" || event.name === "get_forecast" };
    if (event.type !== "tool_result" || !r) return view;
    if (r.ok === false) {
        view.fields = Object.fromEntries(Object.entries(r).filter(([key]) => !["ok", "error", "event", "session_id"].includes(key) && !key.startsWith("_")));
        return view;
    }
    const names = returnNames(frame);
    const output = r._action_output || {};
    const readable = (text: unknown) => text == null ? "未记录" : readableActionText(String(text), names);
    const skillNames = (items: Record<string, any>[] | undefined) => items == null ? "" : items.map(skill => skill.name || skill.skill_id).join("、");
    const used = new Set(["ok", "event", "_llm_refs", "_observation_after", "_action_output", "session_id", "error"]);
    function table(kind: ReturnKind, title: string, columns: string[], records: Record<string, any>[], row: (record: Record<string, any>) => string[], first = false) {
        const result = { kind, title, columns, records, rows: records.map(row) };
        if (first) view.tables.unshift(result);
        else view.tables.push(result);
    }
    function members(items: Record<string, any>[], title: string) {
        if (event.name !== "configure_tactics") table("members", title, ["成员 / 编号", "等级", "生命 / 法力", "属性", "技能"], items, item => [
            `${item.name || "名称未记录"}\n${item.adventurer_id ?? ""}`, number(item.level),
            `生命 ${number(item.resources?.current_hp)} / ${number(item.effective_stats?.hp)}\n法力 ${number(item.resources?.current_mp)} / ${number(item.effective_stats?.mp)}`,
            returnStats(item.effective_stats, ["hp", "mp"]), skillNames(item.skills),
        ]);
        for (const item of items) if (event.name === "configure_tactics" && item.tactics) table("tactics", `${item.name || "成员"}的实际战术`, ["项目", "结果"], Object.entries(item.tactics).filter(([key]) => key !== "available_active_skills").map(([key, value]) => ({ key, value })), pair => [
            RETURN_LABELS[pair.key] || pair.key,
            Array.isArray(pair.value) ? pair.value.length ? pair.value.map(readable).join(pair.key === "skill_priority" ? " → " : "、") : pair.key === "skill_priority" ? "默认顺序" : "无" : readable(pair.value),
        ]);
    }
    function equipment(items: Record<string, any>[], title: string) {
        table("equipment", title, ["装备 / 实例", "槽位", "属性", "穿戴者", "技能"], items, item => [
            `${item.name || "名称未记录"}\n${item.instance_id ?? ""}`, SLOTS[item.slot] || item.slot || "未记录",
            returnStats(item.stats), item.equipped_by === null ? "未穿戴" : readable(item.equipped_by), skillNames(item.skills),
        ]);
    }
    function upgrades(items: Record<string, any>[], title: string, costs = true) {
        table("upgrades", title, costs ? ["天赋", "状态", "效果", "费用 / 缺口"] : ["天赋", "状态", "效果"], items, item => [item.name || item.upgrade_id,
            item.unlocked === true ? "已解锁" : flag(item.can_purchase, "可解锁", "条件未满足"),
            [item.description, item.stats && returnStats(item.stats), typeof item.party_size_bonus === "number" && item.party_size_bonus !== 0 ? `编队容量加成 ${item.party_size_bonus}` : "", skillNames(item.skills)].filter(Boolean).join("\n") || "未记录",
            ...(costs ? [[price(item), ...requirementLines(item.missing, items)].join("\n")] : []),
        ]);
    }
    function monsters(items: Record<string, any>[], title: string) {
        table("monsters", title, ["怪物 / 引用", "属性", "目标奖励", "技能"], items, item => [
            `${item.name || "名称未记录"}\n${item.monster_id || item.refresh_ref || item.forecast_ref || ""}`, returnStats(item.stats),
            item.reward ? `金币 ${number(item.reward.gold)} · 经验 ${number(item.reward.experience)}${item.reward.materials ? "\n" + Object.entries(item.reward.materials).map(([key, value]) => `${MATERIALS[key] || key} ${value}`).join("、") : ""}` : "未记录", item.skills == null ? "未记录" : skillNames(item.skills) || "无",
        ]);
    }
    if (Array.isArray(r.adventurers)) { used.add("adventurers"); members(r.adventurers, "返回的队伍"); }
    if (Array.isArray(r.monsters)) { used.add("monsters"); monsters(r.monsters, view.preview ? "刷新候选" : "返回的怪物"); }
    if (Array.isArray(r.equipment_inventory)) { used.add("equipment_inventory"); equipment(r.equipment_inventory, "返回的装备库存"); }
    if (Array.isArray(r.global_upgrades)) { used.add("global_upgrades"); upgrades(r.global_upgrades, "返回的天赋与升级"); }
    if (Array.isArray(r.recruit_candidates)) {
        used.add("recruit_candidates"); table("recruits", "返回的招募候选", ["候选", "招募费用", "资格", "属性"], r.recruit_candidates, item => [
            item.name || item.candidate_id, price(item), flag(item.can_recruit, "可招募", requirementLines(item.missing).join(" · ") || "条件未满足"),
            [item.effective_stats ? "Lv1面板（含公会直接加成）" : "基础属性", returnStats(item.effective_stats || item.base_stats)].join("\n"),
        ]);
    }
    if (Array.isArray(r.crafting_recipes)) {
        used.add("crafting_recipes"); table("recipes", "返回的制作配方", ["配方 / 成品", "费用", "资格", "成品属性"], r.crafting_recipes, item => [
            `${item.name || item.recipe_id}\n${item.output_name || ""}`, price(item), flag(item.can_craft, "可制作", requirementLines(item.missing).join(" · ") || "条件未满足"), returnStats(item.output_stats),
        ]);
    }
    const membersById = new Map<string, Record<string, any>>((output.adventurers || []).map((item: any) => [item.adventurer_id, item]));
    if (r.recruited_adventurer) {
        const item = r.recruited_adventurer;
        membersById.set(item.adventurer_id, { ...membersById.get(item.adventurer_id), ...item });
    }
    const returnedMembers = [...membersById.values()];
    used.add("recruited_adventurer");
    if (output.global_upgrades?.length) upgrades(output.global_upgrades, "本次已解锁天赋", false);
    if (output.equipment_inventory?.length) equipment(output.equipment_inventory, event.name === "craft_equipment" ? "实际制作出的装备" : "操作后的装备");
    if (returnedMembers.length) members(returnedMembers, event.name === "recruit_adventurer" ? "招募结果" : "操作后的成员");
    if (output.dismissed_adventurers?.length) members(output.dismissed_adventurers, "已解雇成员，以下为离队前记录");
    const battles = r.turn_result?.battles || r.event?.battles || r.battles || (r.preview ? [r.preview] : r.battle ? [r.battle] : undefined);
    if (Array.isArray(battles)) {
        used.add("battles"); used.add("preview"); used.add("battle");
        const timing = battles.some(item => item.actions_taken != null || item.time_elapsed != null || item.combat?.actions_taken != null || item.combat?.time_elapsed != null);
        table("battles", view.preview ? "模拟战果" : "战斗结果", ["成员 → 怪物", "结果", view.preview ? "模拟奖励" : "奖励", ...(timing ? ["行动 / 耗时"] : [])], battles, item => [
            `${item.adventurer_name || "成员未记录"} → ${item.monster_name || "怪物未记录"}`, battleResult(item),
            item.reward ? `金币 ${number(item.reward.gold)} · 经验 ${number(item.reward.experience)}${item.reward.materials ? "\n" + Object.entries(item.reward.materials).map(([key, value]) => `${MATERIALS[key] || key} ${value}`).join("、") : ""}` : "奖励未记录",
            ...(timing ? [`行动 ${number(item.actions_taken ?? item.combat?.actions_taken)}\n耗时 ${number(item.time_elapsed ?? item.combat?.time_elapsed)}`] : []),
        ], true);
        if (r.preview) view.fields["试算详细返回"] = r.preview;
        if (r.turn_result) {
            used.add("turn_result");
            const rest = Object.fromEntries(Object.entries(r.turn_result).filter(([key]) => key !== "battles"));
            if (Object.keys(rest).length) view.fields["回合结算明细"] = rest;
        }
    }
    if (Array.isArray(r.events)) {
        used.add("events");
        if (event.name === "get_events") table("events", "事件记录", ["回合", "内容"], r.events, item => [number(item.turn), item.summary || "未记录"]);
        // 方案事件由方案面板按准备动作展示。
        else if (event.name !== "execute_plan") table("events", "战斗过程", ["回合 / 行动", "事件 / 技能", "内容 / 伤害", "目标生命 / 治疗"], r.events, item => [
            number(item.turn ?? item.action_index ?? item.sequence), item.skill_name || item.status_name || (item.skill_id ? readable(item.skill_id) : "") || SIDES[item.action_type] || item.event_type || "未记录",
            item.summary || `${item.actor_side ? SIDES[item.actor_side] || item.actor_side : ""} → ${item.target_side ? SIDES[item.target_side] || item.target_side : ""} · 伤害 ${number(item.damage)}`,
            `生命 ${number(item.target_hp)} · 治疗 ${number(item.healing)}`,
        ]);
    }
    if (Array.isArray(r.items) && event.name === "get_forecast") {
        used.add("items"); table("forecast", "预告条目", ["回合 / 引用", "名称", "属性", "费用 / 基础奖励"], r.items, item => [
            `${number(item.turn)} / ${item.forecast_ref || ""}`, item.name || "未记录", returnStats(item.stats || item.base_stats),
            item.base_reward ? `基础金币 ${number(item.base_reward.gold)} · 基础经验 ${number(item.base_reward.experience)}` : price(item),
        ]);
    }
    view.fields = { ...view.fields, ...Object.fromEntries(Object.entries(r).filter(([key]) => !used.has(key) && !key.startsWith("_"))) };
    const resultNames = (items: Record<string, any>[]) => items.map(item => item.name || item.adventurer_id || item.instance_id || item.upgrade_id).join("、");
    if (event.name === "execute_plan" && r.operation_count != null) view.summary = r.already_executed ? "重复提交，未再次执行" : `已执行 ${r.operation_count} 步`;
    else if (event.name === "recruit_adventurer" && returnedMembers.length) view.summary = `招募到 ${resultNames(returnedMembers)}${returnedMembers[0].level != null ? `，等级 ${returnedMembers[0].level}` : ""}`;
    else if (event.name === "craft_equipment" && output.equipment_inventory?.length) view.summary = `制成 ${resultNames(output.equipment_inventory)}`;
    else if (event.name === "purchase_upgrade" && output.global_upgrades?.length) view.summary = `解锁 ${resultNames(output.global_upgrades)}`;
    else if (event.name === "dismiss_adventurer" && output.dismissed_adventurers?.length) view.summary = `已解雇 ${resultNames(output.dismissed_adventurers)}`;
    else if (event.name === "configure_tactics" && returnedMembers.some(item => item.tactics)) view.summary = `已配置 ${resultNames(returnedMembers.filter(item => item.tactics))}`;
    else if (returnedMembers.length) view.summary = `操作后的成员：${returnedMembers.map((item: any) => `${item.name}${item.level != null ? `（等级 ${item.level}）` : ""}`).join("、")}`;
    else if (output.equipment_inventory?.length) view.summary = `装备变更：${output.equipment_inventory.map((item: any) => `${item.name} → ${item.equipped_by === null ? "库存" : readable(item.equipped_by)}`).join("、")}`;
    else if (view.tables.length) view.summary = view.tables.map(item => `${item.title} ${item.rows.length} 条`).join(" · ");
    return view;
}
