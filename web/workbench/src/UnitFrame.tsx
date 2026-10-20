import { HeartBreak, HeartHalf, Skull, Sparkle } from "@phosphor-icons/react";
import { monsterStage } from "./monsterModel";
import type { GameEntity, PartyMember } from "./types";
import "./unit-frame.css";

export function adventurerHealth(member: PartyMember) {
    const hp = member.resources.current_hp;
    if (hp <= 0) return "down";
    if (hp < member.effective_stats.hp * 0.3) return "critical";
    return hp < member.effective_stats.hp ? "wounded" : "healthy";
}
export function isLowHealth(member: PartyMember) {
    const state = adventurerHealth(member);
    return state === "critical" || state === "down";
}
const healthLabels = { healthy: "生命完整", wounded: "受伤", critical: "低生命", down: "倒下" };
export function adventurerStateText(member: PartyMember) {
    return `${healthLabels[adventurerHealth(member)]}${member.effective_stats.mp > 0 && member.resources.current_mp === 0 ? "，法力耗尽" : ""}`;
}
export function adventurerFrame(member: PartyMember) {
    return {
        "data-unit-kind": "adventurer",
        "data-unit-state": adventurerHealth(member),
        "data-mana-empty": member.effective_stats.mp > 0 && member.resources.current_mp === 0,
    } as const;
}
export function monsterFrame(monster: GameEntity) {
    const stage = monsterStage(monster);
    return {
        "data-unit-kind": "monster",
        "data-unit-state": stage === "challenge" ? "challenge" : monster.tier === "elite" || monster.tier === "boss"
            ? monster.tier : stage || "unknown",
    } as const;
}

/** 小摘要只突出需要注意的状态，不为每个健康成员重复增加标签。 */
export function AdventurerStatus({ member, compact = false }: { member: PartyMember; compact?: boolean }) {
    const state = adventurerHealth(member);
    const manaEmpty = member.effective_stats.mp > 0 && member.resources.current_mp === 0;
    const showHealth = state === "critical" || state === "down" || (!compact && state === "wounded");
    if (!showHealth && !manaEmpty) return null;
    const Icon = state === "down" ? Skull : state === "critical" ? HeartBreak : HeartHalf;
    return <span className={`unit-indicators${compact ? " is-compact" : ""}`}>
        {showHealth && <span className={`unit-health-label state-${state}`} title={healthLabels[state]} role="img" aria-label={healthLabels[state]}>
            <Icon size={12} weight="fill" />{!compact && healthLabels[state]}
        </span>}
        {manaEmpty && <span className="unit-mana-label" title="法力耗尽" role="img" aria-label="法力耗尽">
            <Sparkle size={12} weight="fill" />{compact ? "0" : "法力耗尽"}
        </span>}
    </span>;
}
