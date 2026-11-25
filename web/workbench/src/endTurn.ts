import type { ActionEntry } from "./actionModel";
import { actionTarget } from "./actionInputs";
import { returnNames } from "./actionReturns";

type Vitals = { current_hp?: number; current_mp?: number };
export interface EndTurnMember {
    id: string;
    name: string;
    before?: Vitals;
    after?: Vitals;
    activity: "出战" | "休整" | null;
}

export function describeEndTurn(entry: ActionEntry) {
    const result = entry.event.result;
    const battles = entry.returned.tables.find(table => table.kind === "battles");
    const names = returnNames(entry.frame);
    for (const battle of battles?.records || []) {
        if (battle.adventurer_id != null && battle.adventurer_name) names.set(String(battle.adventurer_id), battle.adventurer_name);
        if (battle.monster_id != null && battle.monster_name) names.set(String(battle.monster_id), battle.monster_name);
    }
    const action = result?.event?.action;
    const confirmedHunts: Record<string, any>[] | undefined = action?.type === "end_turn" && Array.isArray(action.hunts) ? action.hunts : undefined;
    const submitted = entry.input.state === "recorded" ? entry.input.value.children.find(field => field.key === "hunts") : undefined;
    let orders: { member: string; target: string }[] | null = null;
    if (confirmedHunts) orders = confirmedHunts.map(hunt => ({
        member: actionTarget(entry.frame, "adventurer", hunt.adventurer_id, names) || "成员未记录",
        target: actionTarget(entry.frame, "monster", hunt.monster_id, names) || "目标未记录",
    }));
    else if (submitted?.value.kind === "array") orders = submitted.value.children.map(hunt => ({
        member: hunt.value.children.find(field => field.key === "adventurer_id")?.value.text || "成员未记录",
        target: hunt.value.children.find(field => field.key === "monster_id")?.value.text || "目标未记录",
    }));
    else if (battles) orders = battles.records.map(battle => ({ member: battle.adventurer_name || "成员未记录", target: battle.monster_name || "目标未记录" }));

    const hunted = battles?.records.every(battle => typeof battle.adventurer_id === "string")
        ? new Set<string>(battles.records.map(battle => battle.adventurer_id))
        : confirmedHunts?.every(hunt => typeof hunt.adventurer_id === "string") ? new Set<string>(confirmedHunts.map(hunt => hunt.adventurer_id)) : null;
    const before = entry.frame.before?.adventurers;
    const afterObservation = entry.frame.observation !== entry.frame.before ? entry.frame.observation : null;
    const after = afterObservation?.adventurers;
    const party = new Map<string, EndTurnMember>();
    const member = (id: string, name: string) => {
        let row = party.get(id);
        if (!row) {
            row = { id, name, activity: hunted ? hunted.has(id) ? "出战" : "休整" : null };
            party.set(id, row);
        }
        return row;
    };
    for (const hero of before || []) member(hero.adventurer_id, hero.name).before = hero.resources;
    for (const battle of battles?.records || []) if (typeof battle.adventurer_id === "string") {
        const row = member(battle.adventurer_id, battle.adventurer_name || names.get(battle.adventurer_id) || "成员未记录");
        row.before ??= battle.adventurer_before_resources;
        row.after = battle.adventurer_after_resources;
    }
    for (const hero of result?._action_output?.adventurers || []) {
        const row = member(hero.adventurer_id, hero.name || names.get(hero.adventurer_id) || "成员未记录");
        if (hero.resources != null) row.after = hero.resources;
    }
    for (const hero of after || []) {
        const row = member(hero.adventurer_id, hero.name);
        row.name = hero.name;
        row.after = hero.resources;
    }
    const members = after === undefined ? [...party.values()] : after.map(hero => party.get(hero.adventurer_id)!);
    const turn = result?.event?.turn ?? entry.frame.before?.turn;
    const nextTurn = afterObservation && typeof turn === "number" && afterObservation.turn > turn && afterObservation.turn <= afterObservation.max_turns ? afterObservation.turn : null;
    return { battles, orders, members, afterPartyComplete: after !== undefined, nextTurn, finished: afterObservation?.finished === true };
}
