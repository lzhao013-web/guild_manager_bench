from __future__ import annotations

from typing import Any, Mapping, Sequence


def skill_summary(skills: Any) -> str:
    """Return a compact one-line skill summary for LLM-visible text."""

    values = [
        skill
        for skill in _sequence(skills)
        if isinstance(skill, Mapping)
    ]
    if not values:
        return "none"
    return "; ".join(_skill_text(skill) for skill in values)


def _skill_text(skill: Mapping[str, Any]) -> str:
    parts = [
        str(skill.get("skill_id") or skill.get("name") or "skill"),
        str(skill.get("kind") or "skill"),
    ]
    mp_cost = skill.get("mp_cost")
    if isinstance(mp_cost, int | float) and mp_cost:
        parts.append(f"mp {mp_cost}")
    if skill.get("once_per_battle"):
        parts.append("once")
    priority = skill.get("priority")
    if isinstance(priority, int | float) and priority:
        parts.append(f"prio {priority}")
    condition = _condition_text(skill.get("condition"))
    if condition:
        parts.append(f"if {condition}")
    effects = [
        _effect_text(effect)
        for effect in _sequence(skill.get("effects"))
        if isinstance(effect, Mapping)
    ]
    effects = [effect for effect in effects if effect]
    if effects:
        parts.append("effects " + ",".join(effects))
    return " ".join(parts)


def _condition_text(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    condition_type = value.get("type")
    if condition_type in (None, "always"):
        return "always"
    if condition_type in {"all", "any"}:
        joiner = "&" if condition_type == "all" else "|"
        children = [
            _condition_text(child)
            for child in _sequence(value.get("conditions"))
            if isinstance(child, Mapping)
        ]
        children = [child for child in children if child and child != "always"]
        return joiner.join(children) if children else "always"
    raw = str(condition_type)
    if condition_type == "self_hp_pct_lte":
        return f"self_hp<={_percent(value.get('value'))}"
    if condition_type == "self_hp_pct_gte":
        return f"self_hp>={_percent(value.get('value'))}"
    if condition_type == "target_hp_pct_lte":
        return f"target_hp<={_percent(value.get('value'))}"
    if condition_type == "target_hp_pct_gte":
        return f"target_hp>={_percent(value.get('value'))}"
    condition_value = value.get("value")
    return raw if condition_value is None else f"{raw}:{condition_value}"


def _effect_text(effect: Mapping[str, Any]) -> str:
    effect_type = effect.get("type")
    value = effect.get("value")
    stat = effect.get("stat")
    target = effect.get("target")
    target_prefix = "" if target in (None, "target") else f"{target}."
    if effect_type == "damage_multiplier":
        return f"dmgx{_number(value)}"
    if effect_type == "heal":
        return f"heal{_number(value)}"
    if effect_type == "stat_bonus":
        return f"{target_prefix}{stat}+{_number(value)}"
    if effect_type == "stat_multiplier":
        return f"{target_prefix}{stat}x{_number(value)}"
    if stat is not None:
        return f"{effect_type}:{stat}:{_number(value)}"
    return f"{effect_type}:{_number(value)}"


def _percent(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{round(value * 100)}%"
    return str(value)


def _number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else ()
