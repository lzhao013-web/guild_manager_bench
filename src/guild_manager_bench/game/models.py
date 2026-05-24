from __future__ import annotations

from dataclasses import dataclass


def _require_at_least(name: str, value: int, minimum: int) -> None:
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
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

    def __post_init__(self) -> None:
        _require_at_least("hp", self.hp, 1)
        _require_at_least("mp", self.mp, 0)
        _require_at_least("attack", self.attack, 0)
        _require_at_least("defense", self.defense, 0)
        _require_at_least("speed", self.speed, 0)
        _require_at_least("recovery", self.recovery, 0)


@dataclass(frozen=True, slots=True)
class CombatStatModifier:
    """战斗属性修正值。

    装备、升级、等级成长都可以用这个结构表示对基础属性的加成。
    """

    hp: int = 0
    mp: int = 0
    attack: int = 0
    defense: int = 0
    speed: int = 0
    recovery: int = 0

    def __post_init__(self) -> None:
        _require_at_least("hp", self.hp, 0)
        _require_at_least("mp", self.mp, 0)
        _require_at_least("attack", self.attack, 0)
        _require_at_least("defense", self.defense, 0)
        _require_at_least("speed", self.speed, 0)
        _require_at_least("recovery", self.recovery, 0)

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
        )


def apply_stat_modifier(stats: CombatStats, modifier: CombatStatModifier) -> CombatStats:
    """把属性修正应用到基础战斗属性上。"""

    return CombatStats(
        hp=stats.hp + modifier.hp,
        mp=stats.mp + modifier.mp,
        attack=stats.attack + modifier.attack,
        defense=stats.defense + modifier.defense,
        speed=stats.speed + modifier.speed,
        recovery=stats.recovery + modifier.recovery,
    )


def scale_stat_modifier(modifier: CombatStatModifier, factor: int) -> CombatStatModifier:
    """按整数倍率放大战斗属性修正。"""

    _require_at_least("factor", factor, 0)
    return CombatStatModifier(
        hp=modifier.hp * factor,
        mp=modifier.mp * factor,
        attack=modifier.attack * factor,
        defense=modifier.defense * factor,
        speed=modifier.speed * factor,
        recovery=modifier.recovery * factor,
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
