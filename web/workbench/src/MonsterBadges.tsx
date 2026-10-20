import { Crown, FlagBanner, StarFour } from "@phosphor-icons/react";
import { MONSTER_ROLES, MONSTER_STAGES, monsterClassification, monsterStage } from "./monsterModel";
import type { GameEntity } from "./types";
import "./monsters.css";

export function MonsterBadges({ monster, compact = false }: { monster: GameEntity; compact?: boolean }) {
    const stage = monsterStage(monster);
    const role = monster.tier as string | undefined;
    return <span className={`monster-badges${compact ? " is-compact" : ""}`} role="group" aria-label={monsterClassification(monster)}>
        <span className={`monster-stage stage-${stage || "unknown"}`} title={stage ? MONSTER_STAGES[stage].description : "此原型没有可用的分组元数据，不能确定阶级"}>
            {stage === "challenge" && <FlagBanner size={12} weight="fill" />}
            {stage ? MONSTER_STAGES[stage].label : "阶级?"}
        </span>
        {stage !== "challenge" && <span className={`monster-role role-${role || "unknown"}`}>
            {role === "elite" && <StarFour size={11} weight="fill" />}
            {role === "boss" && <Crown size={12} weight="fill" />}
            {MONSTER_ROLES[role || ""] || "身份?"}
        </span>}
    </span>;
}
