import type { ComponentType, CSSProperties } from "react";
import {
    Boot,
    Crown,
    Hand,
    Heart,
    Lightning,
    Shield,
    Skull,
    Sparkle,
    Sword,
    TShirt,
    User,
    MagicWand,
} from "@phosphor-icons/react";
import { fmt } from "./model";
import { ChangeValue } from "./motion";
import { conditionText, effectText, signed, STAT_NAMES, statEntries } from "./sceneModel";
import type { SceneSkill } from "./sceneModel";
import type { GameEntity } from "./types";

const portraits = import.meta.glob<string>("./portraits/**/*.webp", {
    eager: true,
    import: "default",
    query: "?url",
});
export function Portrait({
    id,
    name,
    monster = false,
}: {
    id: string | undefined;
    name: string;
    monster?: boolean;
}) {
    const url = portraits[`./portraits/${monster ? "monsters/" : ""}${id}.webp`];
    const Icon = monster ? Skull : User;
    return url ? (
        <img className="scene-portrait" src={url} alt="" width={256} height={256} loading="lazy" />
    ) : (
        <div className="scene-portrait scene-no-portrait" aria-label={`${name} 暂无立绘`}>
            <Icon size={50} weight="duotone" />
            <span>暂无立绘</span>
        </div>
    );
}
export function Vital({
    label,
    value,
    max,
    mana = false,
    delta,
}: {
    label: string;
    value: number;
    max: number;
    mana?: boolean;
    delta?: number;
}) {
    const ratio = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
    return (
        <div
            className={`scene-vital ${mana ? "mana" : "health"} ${!mana && ratio < 30 ? "low" : ""}`}
        >
            <div>
                <span>
                    {mana ? <Sparkle size={11} /> : <Heart size={11} />}
                    {label}
                </span>
                <ChangeValue value={`${value}/${max}`}>
                    <b className="mono">
                        {fmt(value)} <span>/ {fmt(max)}</span>
                        {delta != null && delta !== 0 && <em>{signed(delta)}</em>}
                    </b>
                </ChangeValue>
            </div>
            <div
                className="vital-track"
                {...(max > 0
                    ? {
                          role: "meter",
                          "aria-label": label,
                          "aria-valuenow": value,
                          "aria-valuemin": 0,
                          "aria-valuemax": Math.max(max, value),
                          "aria-valuetext": `${value} / ${max}`,
                      }
                    : { "aria-hidden": true as const })}
            >
                <i style={{ "--fill": ratio / 100 } as CSSProperties} />
            </div>
        </div>
    );
}
export function StatTiles({
    stats,
    previous,
    modifier = false,
}: {
    stats: Record<string, number> | undefined;
    previous?: Record<string, number>;
    modifier?: boolean;
}) {
    if (!stats) return <p className="scene-note">属性未记录</p>;
    const entries = statEntries(stats, modifier);
    if (!entries.length) return <p className="scene-note">没有额外属性加成</p>;
    const icons = {
        hp: Heart,
        mp: Sparkle,
        attack: Sword,
        defense: Shield,
        speed: Lightning,
        recovery: Heart,
        mp_recovery: Sparkle,
    } as Record<string, ComponentType<{ size?: number }>>;
    return (
        <dl className="scene-stat-tiles">
            {entries.map(([key, value]) => {
                const Icon = icons[key] || Sparkle;
                const delta = previous?.[key] == null ? null : value - previous[key];
                return (
                    <div key={key}>
                        <dt>
                            <Icon size={14} />
                            {STAT_NAMES[key] || key}
                        </dt>
                        <dd className="mono">
                            {modifier ? signed(value) : fmt(value)}
                            {delta != null && delta !== 0 && <small>{signed(delta)}</small>}
                        </dd>
                    </div>
                );
            })}
        </dl>
    );
}
export function SlotIcon({ slot, size = 20 }: { slot: string; size?: number }) {
    const Icon =
        (
            {
                main_hand: Sword,
                two_hand: Sword,
                off_hand: Shield,
                helmet: Crown,
                armor: TShirt,
                boots: Boot,
                accessory: Hand,
                ultimate: MagicWand,
            } as Record<string, typeof Sword>
        )[slot] || Shield;
    return <Icon size={size} weight="duotone" />;
}
export function SkillCards({ skills }: { skills: GameEntity[] | undefined }) {
    if (skills === undefined) return <p className="scene-note">技能未记录</p>;
    if (!skills.length) return <p className="scene-note">此时没有技能</p>;
    return (
        <div className="scene-skill-grid">
            {(skills as SceneSkill[]).map((skill) => (
                <article className={`scene-skill ${skill.kind}`} key={skill.skill_id}>
                    <header>
                        <span className="skill-glyph">
                            <MagicWand size={20} weight="duotone" />
                        </span>
                        <div>
                            <strong>{skill.name}</strong>
                            <span>
                                {skill.trigger === "action_start" ? "自动回复" : skill.kind === "active" ? "主动" : "被动"}
                                {skill.kind === "active" ? ` / ${skill.mp_cost} MP` : ""}
                            </span>
                        </div>
                        {skill.free && <b className="scene-tag">即时</b>}
                    </header>
                    <p>{skill.effects.map(effectText).join("。")}</p>
                    <footer>
                        {skill.trigger === "action_start" ? "自身行动开始，状态结算后；不占主动选择，不额外普攻 / " : ""}
                        {conditionText(skill.condition)}
                        {skill.once_per_battle ? " / 每场战斗限一次" : ""}
                    </footer>
                </article>
            ))}
        </div>
    );
}
