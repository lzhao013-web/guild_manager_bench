import type { GameEntity, Observation } from "./types";

export type MonsterStage = "t1" | "t2" | "t3" | "challenge";
export type MonsterStages = Record<string, MonsterStage>;
export const MONSTER_STAGES = {
    t1: { label: "T1", description: "早期原型" },
    t2: { label: "T2", description: "中期原型" },
    t3: { label: "T3", description: "晚期原型，包含晚期变体" },
    challenge: { label: "挑战", description: "固定挑战" },
};
export const MONSTER_ROLES: Record<string, string> = {
    normal: "普通", elite: "精英", boss: "首领",
};
export function monsterStage(monster: GameEntity): MonsterStage | undefined {
    return monster.tier === "challenge" ? "challenge" : monster.display_stage as MonsterStage | undefined;
}
export function monsterClassification(monster: GameEntity): string {
    const stage = monsterStage(monster);
    if (stage === "challenge") return "固定挑战";
    return `${stage ? MONSTER_STAGES[stage].label : "阶级未记录"} · ${MONSTER_ROLES[monster.tier as string] || "身份未记录"}`;
}

/** 仅供界面使用的副本。不能把显示分组写回原始事件或暂停快照。 */
export function withMonsterStages(value: Observation | null, stages: MonsterStages): Observation | null {
    if (!value?.monsters) return value;
    return { ...value, monsters: value.monsters.map(monster => ({
        ...monster, display_stage: stages[monster.archetype_id as string],
    })) };
}
