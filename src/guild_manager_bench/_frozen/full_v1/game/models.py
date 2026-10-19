from __future__ import annotations

from dataclasses import dataclass


def _require_at_least(name: str, value: int, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")


def _require_number_at_least(name: str, value: float, minimum: float) -> None:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number, not bool")
    if not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")


@dataclass(frozen=True, slots=True)
class CombatStats:
    """冒险者和怪物共用的战斗属性。

    `hp` 和 `mp` 是最大值，当前 HP/MP 由 CombatResources 保存。
    """

    hp: int
    mp: int
    attack: int
    defense: int
    speed: int
    recovery: int
    mp_recovery: int = 0

    def __post_init__(self) -> None:
        _require_at_least("hp", self.hp, 1)
        _require_at_least("mp", self.mp, 0)
        _require_at_least("attack", self.attack, 0)
        _require_at_least("defense", self.defense, 0)
        _require_at_least("speed", self.speed, 0)
        _require_at_least("recovery", self.recovery, 0)
        _require_at_least("mp_recovery", self.mp_recovery, 0)


@dataclass(frozen=True, slots=True)
class CombatStatModifier:
    """战斗属性修正值。

    装备、升级、等级成长都可以用这个结构表示对基础属性的加成。
    支持浮点数，最终应用到 CombatStats 时取整。
    """

    hp: float = 0.0
    mp: float = 0.0
    attack: float = 0.0
    defense: float = 0.0
    speed: float = 0.0
    recovery: float = 0.0
    mp_recovery: float = 0.0

    def __post_init__(self) -> None:
        _require_number_at_least("hp", self.hp, 0.0)
        _require_number_at_least("mp", self.mp, 0.0)
        _require_number_at_least("attack", self.attack, 0.0)
        _require_number_at_least("defense", self.defense, 0.0)
        _require_number_at_least("speed", self.speed, 0.0)
        _require_number_at_least("recovery", self.recovery, 0.0)
        _require_number_at_least("mp_recovery", self.mp_recovery, 0.0)

    def __add__(self, other: CombatStatModifier) -> CombatStatModifier:
        if not isinstance(other, CombatStatModifier):
            return NotImplemented
        return CombatStatModifier(
            hp=self.hp + other.hp,
            mp=self.mp + other.mp,
            attack=self.attack + other.attack,
            defense=self.defense + other.defense,
            speed=self.speed + other.speed,
            recovery=self.recovery + other.recovery,
            mp_recovery=self.mp_recovery + other.mp_recovery,
        )


def apply_stat_modifier(stats: CombatStats, modifier: CombatStatModifier) -> CombatStats:
    """把属性修正应用到基础战斗属性上，浮点修正取整。"""

    return CombatStats(
        hp=int(stats.hp + modifier.hp),
        mp=int(stats.mp + modifier.mp),
        attack=int(stats.attack + modifier.attack),
        defense=int(stats.defense + modifier.defense),
        speed=int(stats.speed + modifier.speed),
        recovery=int(stats.recovery + modifier.recovery),
        mp_recovery=int(stats.mp_recovery + modifier.mp_recovery),
    )


def scale_stat_modifier(modifier: CombatStatModifier, factor: float) -> CombatStatModifier:
    """按倍率放大战斗属性修正，支持浮点倍率和浮点成长。"""

    _require_number_at_least("factor", factor, 0.0)
    return CombatStatModifier(
        hp=modifier.hp * factor,
        mp=modifier.mp * factor,
        attack=modifier.attack * factor,
        defense=modifier.defense * factor,
        speed=modifier.speed * factor,
        recovery=modifier.recovery * factor,
        mp_recovery=modifier.mp_recovery * factor,
    )


def scale_combat_stats(stats: CombatStats, multiplier: float) -> CombatStats:
    """按浮点倍率缩放战斗属性，结果取整。"""

    return CombatStats(
        hp=max(1, int(stats.hp * multiplier)),
        mp=max(0, int(stats.mp * multiplier)),
        attack=max(0, int(stats.attack * multiplier)),
        defense=max(0, int(stats.defense * multiplier)),
        speed=max(0, int(stats.speed * multiplier)),
        recovery=max(0, int(stats.recovery * multiplier)),
        mp_recovery=max(0, int(stats.mp_recovery * multiplier)),
    )


@dataclass(slots=True)
class CombatResources:
    """战斗单位的可变当前资源。"""

    current_hp: int
    current_mp: int

    @classmethod
    def full(cls, stats: CombatStats) -> CombatResources:
        return cls(current_hp=stats.hp, current_mp=stats.mp)

    def __post_init__(self) -> None:
        _require_at_least("current_hp", self.current_hp, 0)
        _require_at_least("current_mp", self.current_mp, 0)

    @property
    def is_alive(self) -> bool:
        return self.current_hp > 0
