from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Literal

from guild_manager_bench.game.models import (
    CombatResources,
    CombatStats,
)
from guild_manager_bench.game.skills import Skill, SkillCondition, StatusDefinition


CombatSide = Literal["left", "right"]
CombatOutcome = Literal["left_win", "right_win", "draw"]
CombatActionType = Literal["basic_attack", "skill", "status"]

DEFAULT_ACTION_GAUGE_MAX = 100
DEFAULT_MAX_ACTIONS = 1_000


@dataclass(frozen=True, slots=True)
class Combatant:
    """参与自动战斗的一方。"""

    combatant_id: str
    stats: CombatStats
    resources: CombatResources | None = None
    skills: tuple[Skill, ...] = ()


@dataclass(frozen=True, slots=True)
class CombatEvent:
    """一次自动行动的结算记录。"""

    action_index: int
    time_elapsed: int
    action_type: CombatActionType
    actor_side: CombatSide
    actor_id: str
    target_side: CombatSide
    target_id: str
    damage: int
    target_hp: int
    skill_id: str | None = None
    skill_name: str | None = None
    healing: int = 0
    healing_target_side: CombatSide | None = None
    healing_target_hp: int | None = None
    status_id: str | None = None
    status_name: str | None = None


@dataclass(frozen=True, slots=True)
class CombatResult:
    """自动战斗的完整结算结果。"""

    outcome: CombatOutcome
    winner_side: CombatSide | None
    reason: str
    left_resources: CombatResources
    right_resources: CombatResources
    events: tuple[CombatEvent, ...]
    actions_taken: int
    time_elapsed: int


@dataclass(slots=True)
class _RuntimeCombatant:
    side: CombatSide
    combatant_id: str
    stats: CombatStats
    resources: CombatResources
    active_skills: tuple[Skill, ...]
    passive_skills: tuple[Skill, ...]
    used_once_skill_ids: set[str]
    statuses: list[_RuntimeStatus]
    action_gauge: int = 0


@dataclass(slots=True)
class _RuntimeStatus:
    definition: StatusDefinition
    remaining_actions: int


def run_auto_battle(
    left: Combatant,
    right: Combatant,
    *,
    action_gauge_max: int = DEFAULT_ACTION_GAUGE_MAX,
    max_actions: int = DEFAULT_MAX_ACTIONS,
) -> CombatResult:
    """执行一场 1v1 行动条自动战斗。

    双方按速度积累行动条，行动条达到阈值的一方自动进行普通攻击。
    若双方同时可行动，当前行动条更高的一方先动；仍相同则速度更高的一方先动；
    还相同则左侧先动。函数不会修改传入的 CombatResources。
    """

    if action_gauge_max <= 0:
        raise ValueError("action_gauge_max must be > 0")
    if max_actions <= 0:
        raise ValueError("max_actions must be > 0")

    left_runtime = _build_runtime_combatant("left", left)
    right_runtime = _build_runtime_combatant("right", right)
    events: list[CombatEvent] = []
    time_elapsed = 0

    immediate_result = _resolve_immediate_result(left_runtime, right_runtime)
    if immediate_result is not None:
        winner_side, reason = immediate_result
        return _finish_result(
            left_runtime=left_runtime,
            right_runtime=right_runtime,
            winner_side=winner_side,
            reason=reason,
            events=events,
            time_elapsed=time_elapsed,
        )

    for action_index in range(1, max_actions + 1):
        ready_actor = _advance_until_ready(
            left_runtime,
            right_runtime,
            action_gauge_max=action_gauge_max,
            action_index=action_index,
        )
        if ready_actor is None:
            return _finish_result(
                left_runtime=left_runtime,
                right_runtime=right_runtime,
                winner_side=None,
                reason="no_combatant_can_act",
                events=events,
                time_elapsed=time_elapsed,
            )

        actor, target, elapsed = ready_actor
        time_elapsed += elapsed
        actor.action_gauge -= action_gauge_max

        active_statuses = list(actor.statuses)
        status_event = _apply_status_ticks(action_index, time_elapsed, actor, target)
        if status_event is not None:
            events.append(status_event)

        if not actor.resources.is_alive:
            _decrement_statuses(actor, active_statuses)
            return _finish_result(
                left_runtime=left_runtime,
                right_runtime=right_runtime,
                winner_side=target.side,
                reason="status_defeated_actor",
                events=events,
                time_elapsed=time_elapsed,
            )

        events.append(_perform_action(action_index, time_elapsed, actor, target))
        _decrement_statuses(actor, active_statuses)

        if not actor.resources.is_alive and not target.resources.is_alive:
            return _finish_result(
                left_runtime=left_runtime,
                right_runtime=right_runtime,
                winner_side=None,
                reason="both_defeated",
                events=events,
                time_elapsed=time_elapsed,
            )
        if not target.resources.is_alive:
            return _finish_result(
                left_runtime=left_runtime,
                right_runtime=right_runtime,
                winner_side=actor.side,
                reason="target_defeated",
                events=events,
                time_elapsed=time_elapsed,
            )
        if not actor.resources.is_alive:
            return _finish_result(
                left_runtime=left_runtime,
                right_runtime=right_runtime,
                winner_side=target.side,
                reason="actor_defeated",
                events=events,
                time_elapsed=time_elapsed,
            )

    return _finish_result(
        left_runtime=left_runtime,
        right_runtime=right_runtime,
        winner_side=None,
        reason="max_actions_reached",
        events=events,
        time_elapsed=time_elapsed,
    )


def calculate_basic_attack_damage(attacker: CombatStats, defender: CombatStats) -> int:
    """计算普通攻击伤害。"""

    return max(1, attacker.attack - defender.defense)


def _perform_action(
    action_index: int,
    time_elapsed: int,
    actor: _RuntimeCombatant,
    target: _RuntimeCombatant,
) -> CombatEvent:
    active_skill = _select_active_skill(actor, target, action_index=action_index)
    if active_skill is None:
        damage = _apply_basic_attack(actor, target, action_index=action_index)
        return CombatEvent(
            action_index=action_index,
            time_elapsed=time_elapsed,
            action_type="basic_attack",
            actor_side=actor.side,
            actor_id=actor.combatant_id,
            target_side=target.side,
            target_id=target.combatant_id,
            damage=damage,
            target_hp=target.resources.current_hp,
        )

    actor.resources.current_mp -= active_skill.mp_cost
    if active_skill.once_per_battle:
        actor.used_once_skill_ids.add(active_skill.skill_id)

    damage, healing, healing_target = _apply_active_skill_effects(
        active_skill,
        actor,
        target,
        action_index=action_index,
    )

    # free 技能在效果结算后额外执行一次普通攻击。
    if active_skill.free:
        bonus_damage = _apply_basic_attack(actor, target, action_index=action_index)
        damage += bonus_damage

    return CombatEvent(
        action_index=action_index,
        time_elapsed=time_elapsed,
        action_type="skill",
        actor_side=actor.side,
        actor_id=actor.combatant_id,
        target_side=target.side,
        target_id=target.combatant_id,
        damage=damage,
        target_hp=target.resources.current_hp,
        skill_id=active_skill.skill_id,
        skill_name=active_skill.name,
        healing=healing,
        healing_target_side=healing_target.side if healing_target is not None else None,
        healing_target_hp=(
            healing_target.resources.current_hp if healing_target is not None else None
        ),
    )


def _apply_basic_attack(
    actor: _RuntimeCombatant,
    target: _RuntimeCombatant,
    *,
    action_index: int,
) -> int:
    damage = calculate_basic_attack_damage(
        _effective_stats(actor, target, action_index=action_index),
        _effective_stats(target, actor, action_index=action_index),
    )
    _apply_damage(target, damage)
    return damage


def _apply_active_skill_effects(
    skill: Skill,
    actor: _RuntimeCombatant,
    target: _RuntimeCombatant,
    *,
    action_index: int,
) -> tuple[int, int, _RuntimeCombatant | None]:
    base_damage = calculate_basic_attack_damage(
        _effective_stats(actor, target, action_index=action_index),
        _effective_stats(target, actor, action_index=action_index),
    )
    total_damage = 0
    total_healing = 0
    healing_target: _RuntimeCombatant | None = None

    for effect in skill.effects:
        if effect.effect_type == "damage_multiplier":
            damage = max(1, int(base_damage * effect.value))
            _apply_damage(target, damage)
            total_damage += damage
            continue

        if effect.effect_type == "damage_bonus":
            damage = max(1, base_damage + int(effect.value))
            _apply_damage(target, damage)
            total_damage += damage
            continue

        if effect.effect_type == "true_damage":
            damage = int(effect.value)
            _apply_damage(target, damage)
            total_damage += damage
            continue

        if effect.effect_type == "atk_ratio_damage":
            actor_attack = _effective_stats(actor, target, action_index=action_index).attack
            damage = int(actor_attack * effect.value)
            _apply_damage(target, damage)
            total_damage += damage
            continue

        if effect.effect_type == "self_damage":
            _apply_damage(actor, int(effect.value))
            continue

        if effect.effect_type == "apply_status" and effect.status is not None:
            recipient = actor if effect.target == "self" else target
            _apply_status(recipient, effect.status)
            continue

        if effect.effect_type == "heal":
            recipient = actor if effect.target == "self" else target
            healing = _apply_heal(recipient, int(effect.value))
            total_healing, healing_target = _record_healing(
                total_healing,
                healing_target,
                healing,
                recipient,
            )
            continue

        if effect.effect_type == "heal_percent":
            recipient = actor if effect.target == "self" else target
            healing = _apply_heal(recipient, int(recipient.stats.hp * float(effect.value)))
            total_healing, healing_target = _record_healing(
                total_healing,
                healing_target,
                healing,
                recipient,
            )
            continue

        if effect.effect_type == "mp_restore":
            recipient = actor if effect.target == "self" else target
            _apply_mp_restore(recipient, int(effect.value))

    return total_damage, total_healing, healing_target


def _apply_damage(target: _RuntimeCombatant, damage: int) -> None:
    target.resources.current_hp = max(0, target.resources.current_hp - damage)


def _apply_heal(target: _RuntimeCombatant, healing: int) -> int:
    old_hp = target.resources.current_hp
    target.resources.current_hp = min(target.stats.hp, target.resources.current_hp + healing)
    return target.resources.current_hp - old_hp


def _apply_mp_restore(target: _RuntimeCombatant, amount: int) -> int:
    old_mp = target.resources.current_mp
    target.resources.current_mp = min(target.stats.mp, target.resources.current_mp + amount)
    return target.resources.current_mp - old_mp


def _record_healing(
    total_healing: int,
    healing_target: _RuntimeCombatant | None,
    healing: int,
    recipient: _RuntimeCombatant,
) -> tuple[int, _RuntimeCombatant | None]:
    if healing_target is None or healing_target is recipient:
        return total_healing + healing, recipient
    return total_healing + healing, None


def _apply_status(combatant: _RuntimeCombatant, status: StatusDefinition) -> None:
    if status.stack_mode == "stack":
        combatant.statuses.append(
            _RuntimeStatus(definition=status, remaining_actions=status.duration)
        )
        return
    for runtime_status in combatant.statuses:
        if runtime_status.definition.status_id != status.status_id:
            continue
        if status.stack_mode == "add_duration":
            runtime_status.remaining_actions += status.duration
        else:
            runtime_status.definition = status
            runtime_status.remaining_actions = status.duration
        return
    combatant.statuses.append(
        _RuntimeStatus(definition=status, remaining_actions=status.duration)
    )


def _decrement_statuses(
    combatant: _RuntimeCombatant,
    statuses: list[_RuntimeStatus],
) -> None:
    for status in statuses:
        status.remaining_actions -= 1
    combatant.statuses = [
        status
        for status in combatant.statuses
        if status.remaining_actions > 0
    ]


def _apply_status_ticks(
    action_index: int,
    time_elapsed: int,
    actor: _RuntimeCombatant,
    target: _RuntimeCombatant,
) -> CombatEvent | None:
    total_damage = 0
    total_healing = 0
    healing_target: _RuntimeCombatant | None = None
    triggered: list[str] = []

    for status in actor.statuses:
        for effect in status.definition.effects:
            if effect.effect_type == "true_damage":
                damage = int(effect.value)
                _apply_damage(actor, damage)
                total_damage += damage
                triggered.append(status.definition.name)
                continue

            if effect.effect_type == "heal":
                healing = _apply_heal(actor, int(effect.value))
                total_healing, healing_target = _record_healing(
                    total_healing,
                    healing_target,
                    healing,
                    actor,
                )
                triggered.append(status.definition.name)
                continue

            if effect.effect_type == "heal_percent":
                healing = _apply_heal(actor, int(actor.stats.hp * float(effect.value)))
                total_healing, healing_target = _record_healing(
                    total_healing,
                    healing_target,
                    healing,
                    actor,
                )
                triggered.append(status.definition.name)
                continue

            if effect.effect_type == "mp_restore":
                _apply_mp_restore(actor, int(effect.value))
                triggered.append(status.definition.name)

    if not triggered:
        return None

    first_status = actor.statuses[0].definition
    status_name = "，".join(dict.fromkeys(triggered))
    return CombatEvent(
        action_index=action_index,
        time_elapsed=time_elapsed,
        action_type="status",
        actor_side=actor.side,
        actor_id=actor.combatant_id,
        target_side=target.side,
        target_id=target.combatant_id,
        damage=total_damage,
        target_hp=actor.resources.current_hp,
        healing=total_healing,
        healing_target_side=healing_target.side if healing_target is not None else None,
        healing_target_hp=(
            healing_target.resources.current_hp if healing_target is not None else None
        ),
        status_id=first_status.status_id,
        status_name=status_name,
    )


def _select_active_skill(
    actor: _RuntimeCombatant,
    target: _RuntimeCombatant,
    *,
    action_index: int,
) -> Skill | None:
    for skill in actor.active_skills:
        if _can_use_active_skill(skill, actor, target, action_index=action_index):
            return skill
    return None


def _can_use_active_skill(
    skill: Skill,
    actor: _RuntimeCombatant,
    target: _RuntimeCombatant,
    *,
    action_index: int,
) -> bool:
    if actor.resources.current_mp < skill.mp_cost:
        return False
    if skill.once_per_battle and skill.skill_id in actor.used_once_skill_ids:
        return False
    return _is_condition_met(skill.condition, actor, target, action_index=action_index)


def _build_runtime_combatant(side: CombatSide, combatant: Combatant) -> _RuntimeCombatant:
    resources = _copy_or_create_resources(combatant)
    _validate_resources(combatant.combatant_id, combatant.stats, resources)
    return _RuntimeCombatant(
        side=side,
        combatant_id=combatant.combatant_id,
        stats=combatant.stats,
        resources=resources,
        active_skills=_active_skills(combatant.skills),
        passive_skills=_passive_skills(combatant.skills),
        used_once_skill_ids=set(),
        statuses=[],
    )


def _active_skills(skills: tuple[Skill, ...]) -> tuple[Skill, ...]:
    return tuple(
        sorted(
            (skill for skill in skills if skill.kind == "active"),
            key=lambda item: (-item.priority, item.skill_id),
        )
    )


def _passive_skills(skills: tuple[Skill, ...]) -> tuple[Skill, ...]:
    return tuple(skill for skill in skills if skill.kind == "passive")


def _copy_or_create_resources(combatant: Combatant) -> CombatResources:
    if combatant.resources is None:
        return CombatResources.full(combatant.stats)
    return CombatResources(
        current_hp=combatant.resources.current_hp,
        current_mp=combatant.resources.current_mp,
    )


def _validate_resources(
    combatant_id: str,
    stats: CombatStats,
    resources: CombatResources,
) -> None:
    if resources.current_hp > stats.hp:
        raise ValueError(f"{combatant_id} current_hp must be <= stats.hp")
    if resources.current_mp > stats.mp:
        raise ValueError(f"{combatant_id} current_mp must be <= stats.mp")


def _effective_stats(
    combatant: _RuntimeCombatant,
    target: _RuntimeCombatant,
    *,
    action_index: int,
) -> CombatStats:
    if not combatant.passive_skills and not combatant.statuses:
        return combatant.stats

    bonuses = {
        "attack": 0,
        "defense": 0,
        "speed": 0,
        "recovery": 0,
        "mp_recovery": 0,
    }
    multipliers = {
        "attack": 1.0,
        "defense": 1.0,
        "speed": 1.0,
        "recovery": 1.0,
        "mp_recovery": 1.0,
    }

    for skill in combatant.passive_skills:
        if not _is_condition_met(
            skill.condition,
            combatant,
            target,
            action_index=action_index,
        ):
            continue
        for effect in skill.effects:
            if effect.effect_type == "stat_bonus" and effect.stat is not None:
                bonuses[effect.stat] += int(effect.value)
            elif effect.effect_type == "stat_multiplier" and effect.stat is not None:
                multipliers[effect.stat] *= float(effect.value)

    for status in combatant.statuses:
        for effect in status.definition.effects:
            if effect.effect_type == "stat_bonus" and effect.stat is not None:
                bonuses[effect.stat] += int(effect.value)
            elif effect.effect_type == "stat_multiplier" and effect.stat is not None:
                multipliers[effect.stat] *= float(effect.value)

    return CombatStats(
        hp=combatant.stats.hp,
        mp=combatant.stats.mp,
        attack=_effective_stat_value(combatant.stats.attack, bonuses["attack"], multipliers["attack"]),
        defense=_effective_stat_value(
            combatant.stats.defense,
            bonuses["defense"],
            multipliers["defense"],
        ),
        speed=_effective_stat_value(combatant.stats.speed, bonuses["speed"], multipliers["speed"]),
        recovery=_effective_stat_value(
            combatant.stats.recovery,
            bonuses["recovery"],
            multipliers["recovery"],
        ),
        mp_recovery=_effective_stat_value(
            combatant.stats.mp_recovery,
            bonuses["mp_recovery"],
            multipliers["mp_recovery"],
        ),
    )


def _effective_stat_value(base: int, bonus: int, multiplier: float) -> int:
    return max(0, int((base + bonus) * multiplier))


def _is_condition_met(
    condition: SkillCondition,
    actor: _RuntimeCombatant,
    target: _RuntimeCombatant,
    *,
    action_index: int,
) -> bool:
    if condition.condition_type == "always":
        return True
    if condition.condition_type == "self_hp_pct_lte":
        return _hp_pct(actor) <= condition.value
    if condition.condition_type == "self_hp_pct_gte":
        return _hp_pct(actor) >= condition.value
    if condition.condition_type == "target_hp_pct_lte":
        return _hp_pct(target) <= condition.value
    if condition.condition_type == "target_hp_pct_gte":
        return _hp_pct(target) >= condition.value
    if condition.condition_type == "self_mp_pct_lte":
        return _mp_pct(actor) <= condition.value
    if condition.condition_type == "self_mp_pct_gte":
        return _mp_pct(actor) >= condition.value
    if condition.condition_type == "target_mp_pct_lte":
        return _mp_pct(target) <= condition.value
    if condition.condition_type == "target_mp_pct_gte":
        return _mp_pct(target) >= condition.value
    if condition.condition_type == "action_index_lte":
        return action_index <= condition.value
    if condition.condition_type == "action_index_gte":
        return action_index >= condition.value
    if condition.condition_type == "all":
        return all(
            _is_condition_met(child, actor, target, action_index=action_index)
            for child in condition.conditions
        )
    if condition.condition_type == "any":
        return any(
            _is_condition_met(child, actor, target, action_index=action_index)
            for child in condition.conditions
        )
    raise ValueError(f"unknown skill condition type: {condition.condition_type}")


def _hp_pct(combatant: _RuntimeCombatant) -> float:
    return combatant.resources.current_hp / combatant.stats.hp


def _mp_pct(combatant: _RuntimeCombatant) -> float:
    if combatant.stats.mp == 0:
        return 0.0
    return combatant.resources.current_mp / combatant.stats.mp


def _opponent(
    combatant: _RuntimeCombatant,
    left: _RuntimeCombatant,
    right: _RuntimeCombatant,
) -> _RuntimeCombatant:
    return right if combatant.side == "left" else left


def _resolve_immediate_result(
    left: _RuntimeCombatant,
    right: _RuntimeCombatant,
) -> tuple[CombatSide | None, str] | None:
    if left.resources.is_alive and right.resources.is_alive:
        return None
    if left.resources.is_alive:
        return "left", "right_already_defeated"
    if right.resources.is_alive:
        return "right", "left_already_defeated"
    return None, "both_already_defeated"


def _advance_until_ready(
    left: _RuntimeCombatant,
    right: _RuntimeCombatant,
    *,
    action_gauge_max: int,
    action_index: int,
) -> tuple[_RuntimeCombatant, _RuntimeCombatant, int] | None:
    candidate_speeds = [
        (
            combatant,
            _effective_stats(
                combatant,
                _opponent(combatant, left, right),
                action_index=action_index,
            ).speed,
        )
        for combatant in (left, right)
    ]
    candidates = [
        (combatant, speed)
        for combatant, speed in candidate_speeds
        if speed > 0
    ]
    if not candidates:
        return None

    elapsed = min(
        _ticks_until_ready(combatant, speed=speed, action_gauge_max=action_gauge_max)
        for combatant, speed in candidates
    )
    for combatant, speed in candidates:
        combatant.action_gauge += speed * elapsed

    actor = _select_ready_actor(
        left,
        right,
        action_gauge_max=action_gauge_max,
        action_index=action_index,
    )
    if actor is None:
        return None
    target = right if actor.side == "left" else left
    return actor, target, elapsed


def _select_ready_actor(
    left: _RuntimeCombatant,
    right: _RuntimeCombatant,
    *,
    action_gauge_max: int,
    action_index: int,
) -> _RuntimeCombatant | None:
    ready = [
        combatant
        for combatant in (left, right)
        if combatant.action_gauge >= action_gauge_max
    ]
    if not ready:
        return None
    return max(
        ready,
        key=lambda combatant: _action_priority(
            combatant,
            left,
            right,
            action_index=action_index,
        ),
    )


def _ticks_until_ready(
    combatant: _RuntimeCombatant,
    *,
    speed: int,
    action_gauge_max: int,
) -> int:
    if combatant.action_gauge >= action_gauge_max:
        return 0
    return ceil((action_gauge_max - combatant.action_gauge) / speed)


def _action_priority(
    combatant: _RuntimeCombatant,
    left: _RuntimeCombatant,
    right: _RuntimeCombatant,
    *,
    action_index: int,
) -> tuple[int, int, int]:
    side_priority = 1 if combatant.side == "left" else 0
    return (
        combatant.action_gauge,
        _effective_stats(
            combatant,
            _opponent(combatant, left, right),
            action_index=action_index,
        ).speed,
        side_priority,
    )


def _finish_result(
    *,
    left_runtime: _RuntimeCombatant,
    right_runtime: _RuntimeCombatant,
    winner_side: CombatSide | None,
    reason: str,
    events: list[CombatEvent],
    time_elapsed: int,
) -> CombatResult:
    if winner_side == "left":
        outcome: CombatOutcome = "left_win"
    elif winner_side == "right":
        outcome = "right_win"
    else:
        outcome = "draw"

    return CombatResult(
        outcome=outcome,
        winner_side=winner_side,
        reason=reason,
        left_resources=left_runtime.resources,
        right_resources=right_runtime.resources,
        events=tuple(events),
        actions_taken=sum(1 for event in events if event.action_type != "status"),
        time_elapsed=time_elapsed,
    )
