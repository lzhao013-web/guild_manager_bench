from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


def _require_at_least(name: str, value: int, minimum: int) -> None:
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")


def _require_ratio(name: str, value: float) -> None:
    if not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    if value < 0 or value > 1:
        raise ValueError(f"{name} must be between 0 and 1")


SkillKind = Literal["passive", "active"]
CombatStatName = Literal["attack", "defense", "speed", "recovery"]
SkillEffectType = Literal[
    "damage_multiplier",
    "heal",
    "stat_bonus",
    "stat_multiplier",
]
SkillTarget = Literal["self", "target"]
SkillConditionType = Literal[
    "always",
    "self_hp_pct_lte",
    "self_hp_pct_gte",
    "target_hp_pct_lte",
    "target_hp_pct_gte",
    "all",
    "any",
]


@dataclass(frozen=True, slots=True)
class SkillCondition:
    """技能触发条件。

    血量条件的 `value` 使用 0 到 1 的比例，例如 0.5 表示 50%。
    `all` 和 `any` 用 `conditions` 组合多个子条件。
    """

    condition_type: SkillConditionType
    value: float | None = None
    conditions: tuple[SkillCondition, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "conditions", tuple(self.conditions))

        if self.condition_type == "always":
            if self.value is not None:
                raise ValueError("always condition must not have value")
            if self.conditions:
                raise ValueError("always condition must not have child conditions")
            return

        if self.condition_type in {
            "self_hp_pct_lte",
            "self_hp_pct_gte",
            "target_hp_pct_lte",
            "target_hp_pct_gte",
        }:
            if self.value is None:
                raise ValueError(f"{self.condition_type} condition requires value")
            _require_ratio("value", self.value)
            if self.conditions:
                raise ValueError(f"{self.condition_type} condition must not have child conditions")
            return

        if self.condition_type in {"all", "any"}:
            if self.value is not None:
                raise ValueError(f"{self.condition_type} condition must not have value")
            if not self.conditions:
                raise ValueError(f"{self.condition_type} condition requires child conditions")
            for condition in self.conditions:
                if not isinstance(condition, SkillCondition):
                    raise TypeError("child conditions must be SkillCondition")
            return

        raise ValueError(f"unknown skill condition type: {self.condition_type}")


@dataclass(frozen=True, slots=True)
class SkillEffect:
    """技能效果定义。

    `damage_multiplier` 会基于普通攻击伤害乘倍率。
    `heal` 会治疗指定目标。
    `stat_bonus` 和 `stat_multiplier` 用于被动技能的属性修正。
    """

    effect_type: SkillEffectType
    value: int | float
    stat: CombatStatName | None = None
    target: SkillTarget = "target"

    def __post_init__(self) -> None:
        if not isinstance(self.value, int | float):
            raise TypeError("value must be a number")

        if self.effect_type == "damage_multiplier":
            if self.value <= 0:
                raise ValueError("damage_multiplier value must be > 0")
            if self.stat is not None:
                raise ValueError("damage_multiplier must not have stat")
            if self.target != "target":
                raise ValueError("damage_multiplier target must be target")
            return

        if self.effect_type == "heal":
            if not isinstance(self.value, int):
                raise TypeError("heal value must be an int")
            if self.value < 0:
                raise ValueError("heal value must be >= 0")
            if self.stat is not None:
                raise ValueError("heal must not have stat")
            if self.target not in {"self", "target"}:
                raise ValueError("heal target must be self or target")
            return

        if self.effect_type == "stat_bonus":
            if not isinstance(self.value, int):
                raise TypeError("stat_bonus value must be an int")
            if self.stat is None:
                raise ValueError("stat_bonus requires stat")
            if self.target != "self":
                raise ValueError("stat_bonus target must be self")
            return

        if self.effect_type == "stat_multiplier":
            if self.value <= 0:
                raise ValueError("stat_multiplier value must be > 0")
            if self.stat is None:
                raise ValueError("stat_multiplier requires stat")
            if self.target != "self":
                raise ValueError("stat_multiplier target must be self")
            return

        raise ValueError(f"unknown skill effect type: {self.effect_type}")


@dataclass(frozen=True, slots=True)
class Skill:
    """战斗技能定义。

    被动技能在条件满足时持续生效；主动技能在角色行动时检查，
    满足条件时可以代替普通攻击发动。
    """

    skill_id: str
    name: str
    kind: SkillKind
    condition: SkillCondition
    effects: tuple[SkillEffect, ...]
    mp_cost: int = 0
    priority: int = 0
    once_per_battle: bool = False

    def __post_init__(self) -> None:
        if not self.skill_id:
            raise ValueError("skill_id must not be empty")
        if not self.name:
            raise ValueError("name must not be empty")
        if not isinstance(self.condition, SkillCondition):
            raise TypeError("condition must be SkillCondition")
        object.__setattr__(self, "effects", tuple(self.effects))
        if not self.effects:
            raise ValueError("skill must have at least one effect")
        for effect in self.effects:
            if not isinstance(effect, SkillEffect):
                raise TypeError("effects must be SkillEffect")
        _require_at_least("mp_cost", self.mp_cost, 0)
        _require_at_least("priority", self.priority, 0)

        if self.kind == "passive":
            for effect in self.effects:
                if effect.effect_type not in {"stat_bonus", "stat_multiplier"}:
                    raise ValueError("passive skill only supports stat effects")
            if self.mp_cost != 0:
                raise ValueError("passive skill must not have mp_cost")
            if self.priority != 0:
                raise ValueError("passive skill must not have priority")
            if self.once_per_battle:
                raise ValueError("passive skill must not be once_per_battle")
            return

        if self.kind == "active":
            for effect in self.effects:
                if effect.effect_type in {"stat_bonus", "stat_multiplier"}:
                    raise ValueError("active skill does not support stat effects")
            return

        raise ValueError(f"unknown skill kind: {self.kind}")
