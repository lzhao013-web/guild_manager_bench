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
CombatStatName = Literal["attack", "defense", "speed", "recovery", "mp_recovery"]
SkillEffectType = Literal[
    "damage_multiplier",
    "heal",
    "heal_percent",
    "mp_restore",
    "damage_bonus",
    "true_damage",
    "atk_ratio_damage",
    "self_damage",
    "apply_status",
    "stat_bonus",
    "stat_multiplier",
]
SkillTarget = Literal["self", "target"]
StatusPolarity = Literal["positive", "negative", "neutral"]
StatusStackMode = Literal["refresh", "replace", "add_duration"]
SkillConditionType = Literal[
    "always",
    "self_hp_pct_lte",
    "self_hp_pct_gte",
    "target_hp_pct_lte",
    "target_hp_pct_gte",
    "self_mp_pct_lte",
    "self_mp_pct_gte",
    "target_mp_pct_lte",
    "target_mp_pct_gte",
    "action_index_lte",
    "action_index_gte",
    "all",
    "any",
]

_DAMAGE_EFFECT_TYPES: frozenset[str] = frozenset({
    "damage_multiplier",
    "damage_bonus",
    "true_damage",
    "atk_ratio_damage",
})

# free 技能禁止这些效果类型：它们基于 base_damage 计算，与免费普攻叠加会过强。
_BLOCKED_FREE_EFFECT_TYPES: frozenset[str] = frozenset({
    "damage_multiplier",
    "damage_bonus",
})


@dataclass(frozen=True, slots=True)
class StatusDefinition:
    """战斗内状态定义。

    状态只在单场战斗内生效。`duration` 表示状态持有者接下来多少次行动会
    受到状态影响；tick 类效果在持有者行动开始时结算，属性类效果在持续期间
    修改持有者属性。
    """

    status_id: str
    name: str
    duration: int
    effects: tuple[SkillEffect, ...]
    polarity: StatusPolarity = "neutral"
    stack_mode: StatusStackMode = "refresh"

    def __post_init__(self) -> None:
        if not self.status_id:
            raise ValueError("status_id must not be empty")
        if not self.name:
            raise ValueError("status name must not be empty")
        _require_at_least("duration", self.duration, 1)
        if self.polarity not in {"positive", "negative", "neutral"}:
            raise ValueError(f"unknown status polarity: {self.polarity}")
        if self.stack_mode not in {"refresh", "replace", "add_duration", "stack"}:
            raise ValueError(f"unknown status stack_mode: {self.stack_mode}")
        object.__setattr__(self, "effects", tuple(self.effects))
        if not self.effects:
            raise ValueError("status must have at least one effect")
        for effect in self.effects:
            if not isinstance(effect, SkillEffect):
                raise TypeError("status effects must be SkillEffect")
            if effect.effect_type == "apply_status":
                raise ValueError("status effects must not apply another status")
            if effect.effect_type in {"damage_multiplier", "damage_bonus", "self_damage"}:
                raise ValueError(f"{effect.effect_type} is not supported in status effects")


@dataclass(frozen=True, slots=True)
class SkillCondition:
    """技能触发条件。

    HP/MP 条件的 `value` 使用 0 到 1 的比例，例如 0.5 表示 50%。
    行动序号条件的 `value` 使用从 1 开始的整数。
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
            "self_mp_pct_lte",
            "self_mp_pct_gte",
            "target_mp_pct_lte",
            "target_mp_pct_gte",
        }:
            if self.value is None:
                raise ValueError(f"{self.condition_type} condition requires value")
            _require_ratio("value", self.value)
            if self.conditions:
                raise ValueError(f"{self.condition_type} condition must not have child conditions")
            return

        if self.condition_type in {"action_index_lte", "action_index_gte"}:
            if self.value is None:
                raise ValueError(f"{self.condition_type} condition requires value")
            _require_at_least("value", self.value, 1)
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
    `heal` 和 `heal_percent` 会治疗指定目标。
    `mp_restore` 会恢复指定目标 MP。
    `damage_bonus` 会在普通攻击伤害基础上增加固定伤害。
    `true_damage` 会造成不受防御影响的固定伤害。
    `atk_ratio_damage` 会造成 int(攻击力 × value) 的无视防御伤害。
    `self_damage` 会对技能使用者造成固定伤害。
    `apply_status` 会施加一个单场战斗内状态。
    `stat_bonus` 和 `stat_multiplier` 用于被动技能的属性修正。
    """

    effect_type: SkillEffectType
    value: int | float = 0
    stat: CombatStatName | None = None
    target: SkillTarget = "target"
    status: StatusDefinition | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.value, int | float):
            raise TypeError("value must be a number")

        if self.effect_type != "apply_status" and self.status is not None:
            raise ValueError(f"{self.effect_type} must not have status")

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

        if self.effect_type == "heal_percent":
            _require_ratio("value", self.value)
            if self.stat is not None:
                raise ValueError("heal_percent must not have stat")
            if self.target not in {"self", "target"}:
                raise ValueError("heal_percent target must be self or target")
            return

        if self.effect_type == "mp_restore":
            if not isinstance(self.value, int):
                raise TypeError("mp_restore value must be an int")
            if self.value < 0:
                raise ValueError("mp_restore value must be >= 0")
            if self.stat is not None:
                raise ValueError("mp_restore must not have stat")
            if self.target not in {"self", "target"}:
                raise ValueError("mp_restore target must be self or target")
            return

        if self.effect_type == "damage_bonus":
            if not isinstance(self.value, int):
                raise TypeError("damage_bonus value must be an int")
            if self.value < 0:
                raise ValueError("damage_bonus value must be >= 0")
            if self.stat is not None:
                raise ValueError("damage_bonus must not have stat")
            if self.target != "target":
                raise ValueError("damage_bonus target must be target")
            return

        if self.effect_type == "true_damage":
            if not isinstance(self.value, int):
                raise TypeError("true_damage value must be an int")
            if self.value < 0:
                raise ValueError("true_damage value must be >= 0")
            if self.stat is not None:
                raise ValueError("true_damage must not have stat")
            if self.target != "target":
                raise ValueError("true_damage target must be target")
            return

        if self.effect_type == "atk_ratio_damage":
            if not isinstance(self.value, int | float):
                raise TypeError("atk_ratio_damage value must be a number")
            if self.value <= 0:
                raise ValueError("atk_ratio_damage value must be > 0")
            if self.stat is not None:
                raise ValueError("atk_ratio_damage must not have stat")
            if self.target != "target":
                raise ValueError("atk_ratio_damage target must be target")
            return

        if self.effect_type == "self_damage":
            if not isinstance(self.value, int):
                raise TypeError("self_damage value must be an int")
            if self.value < 0:
                raise ValueError("self_damage value must be >= 0")
            if self.stat is not None:
                raise ValueError("self_damage must not have stat")
            object.__setattr__(self, "target", "self")
            return

        if self.effect_type == "apply_status":
            if self.status is None:
                raise ValueError("apply_status requires status")
            if not isinstance(self.status, StatusDefinition):
                raise TypeError("status must be StatusDefinition")
            if self.value != 0:
                raise ValueError("apply_status must not have value")
            if self.stat is not None:
                raise ValueError("apply_status must not have stat")
            if self.target not in {"self", "target"}:
                raise ValueError("apply_status target must be self or target")
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
    free: bool = False

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
            if self.free:
                raise ValueError("passive skill must not be free")
            return

        if self.kind == "active":
            for effect in self.effects:
                if effect.effect_type in {"stat_bonus", "stat_multiplier"}:
                    raise ValueError("active skill does not support stat effects")
            if self.free:
                for effect in self.effects:
                    if effect.effect_type in _BLOCKED_FREE_EFFECT_TYPES:
                        raise ValueError(
                            "free skill must not have damage_multiplier or damage_bonus effects"
                        )
            return

        raise ValueError(f"unknown skill kind: {self.kind}")
