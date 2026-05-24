from __future__ import annotations

from dataclasses import dataclass, field

from guild_manager_bench.game.models import CombatStatModifier, scale_stat_modifier


@dataclass(frozen=True, slots=True)
class ExperienceRules:
    """等级和经验规则。"""

    base_required_experience: int = 100
    required_experience_growth: int = 50
    max_level: int = 99
    stat_growth_per_level: CombatStatModifier = field(default_factory=CombatStatModifier)

    def __post_init__(self) -> None:
        _require_at_least("base_required_experience", self.base_required_experience, 1)
        _require_at_least("required_experience_growth", self.required_experience_growth, 0)
        _require_at_least("max_level", self.max_level, 1)
        if not isinstance(self.stat_growth_per_level, CombatStatModifier):
            raise TypeError("stat_growth_per_level must be CombatStatModifier")


def required_experience_for_next_level(level: int, rules: ExperienceRules) -> int:
    """返回当前等级升到下一级所需经验。"""

    _require_at_least("level", level, 1)
    _validate_rules(rules)
    return rules.base_required_experience + (level - 1) * rules.required_experience_growth


def add_experience(
    *,
    level: int,
    experience: int,
    amount: int,
    rules: ExperienceRules,
) -> tuple[int, int]:
    """添加经验并返回新的等级和当前等级内经验。"""

    _require_at_least("level", level, 1)
    _require_at_least("experience", experience, 0)
    _require_at_least("amount", amount, 0)
    _validate_rules(rules)

    level = min(level, rules.max_level)
    experience += amount
    while level < rules.max_level:
        required = required_experience_for_next_level(level, rules)
        if experience < required:
            break
        experience -= required
        level += 1

    if level >= rules.max_level:
        level = rules.max_level
        experience = 0
    return level, experience


def level_stat_modifier(level: int, rules: ExperienceRules) -> CombatStatModifier:
    """返回指定等级相对 1 级的属性成长。"""

    _require_at_least("level", level, 1)
    _validate_rules(rules)
    return scale_stat_modifier(rules.stat_growth_per_level, level - 1)


def _validate_rules(rules: ExperienceRules) -> None:
    if not isinstance(rules, ExperienceRules):
        raise TypeError("rules must be ExperienceRules")


def _require_at_least(name: str, value: int, minimum: int) -> None:
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
