from __future__ import annotations

from typing import Any, Mapping, Sequence


def skill_summary(skills: Any) -> str:
    """Return a compact one-line skill summary for LLM-visible text."""

    values = skill_summary_lines(skills)
    if not values:
        return "无"
    return "; ".join(values)


def skill_summary_lines(skills: Any) -> tuple[str, ...]:
    """Return one formatted line per skill for LLM-visible text."""

    values = [
        skill
        for skill in _sequence(skills)
        if isinstance(skill, Mapping)
    ]
    return tuple(_skill_text(skill) for skill in values)


def _skill_text(skill: Mapping[str, Any]) -> str:
    parts = [
        str(skill.get("name") or skill.get("skill_id") or "技能"),
        _skill_kind_text(skill.get("kind")),
    ]
    mp_cost = skill.get("mp_cost")
    if isinstance(mp_cost, int | float) and mp_cost:
        parts.append(f"MP消耗 {mp_cost}")
    if skill.get("once_per_battle"):
        parts.append("每场一次")
    priority = skill.get("priority")
    if isinstance(priority, int | float) and priority:
        parts.append(f"优先级 {priority}")
    condition = _condition_text(skill.get("condition"))
    if condition:
        parts.append(f"条件 {condition}")
    effects = [
        _effect_text(effect)
        for effect in _sequence(skill.get("effects"))
        if isinstance(effect, Mapping)
    ]
    effects = [effect for effect in effects if effect]
    if effects:
        parts.append("效果 " + ",".join(effects))
    return " ".join(parts)


def _skill_kind_text(value: Any) -> str:
    labels = {
        "active": "主动",
        "passive": "被动",
    }
    return labels.get(value, str(value or "技能"))


def _condition_text(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    condition_type = value.get("type")
    if condition_type in (None, "always"):
        return "总是"
    if condition_type in {"all", "any"}:
        joiner = "且" if condition_type == "all" else "或"
        children = [
            _condition_text(child)
            for child in _sequence(value.get("conditions"))
            if isinstance(child, Mapping)
        ]
        children = [child for child in children if child and child != "总是"]
        return joiner.join(children) if children else "总是"
    raw = str(condition_type)
    if condition_type == "self_hp_pct_lte":
        return f"自身HP<={_percent(value.get('value'))}"
    if condition_type == "self_hp_pct_gte":
        return f"自身HP>={_percent(value.get('value'))}"
    if condition_type == "target_hp_pct_lte":
        return f"目标HP<={_percent(value.get('value'))}"
    if condition_type == "target_hp_pct_gte":
        return f"目标HP>={_percent(value.get('value'))}"
    if condition_type == "self_mp_pct_lte":
        return f"自身MP<={_percent(value.get('value'))}"
    if condition_type == "self_mp_pct_gte":
        return f"自身MP>={_percent(value.get('value'))}"
    if condition_type == "target_mp_pct_lte":
        return f"目标MP<={_percent(value.get('value'))}"
    if condition_type == "target_mp_pct_gte":
        return f"目标MP>={_percent(value.get('value'))}"
    if condition_type == "action_index_lte":
        return f"行动序号<={_number(value.get('value'))}"
    if condition_type == "action_index_gte":
        return f"行动序号>={_number(value.get('value'))}"
    condition_value = value.get("value")
    return raw if condition_value is None else f"{raw}:{condition_value}"


def _effect_text(effect: Mapping[str, Any]) -> str:
    effect_type = effect.get("type")
    value = effect.get("value")
    stat = effect.get("stat")
    target = effect.get("target")
    target_text = _effect_target_text(target)
    stat_text = _stat_text(stat)
    if effect_type == "damage_multiplier":
        return f"伤害倍率 {_number(value)}"
    if effect_type == "heal":
        return f"治疗 {_number(value)}"
    if effect_type == "heal_percent":
        return f"治疗 {_percent(value)}最大HP"
    if effect_type == "mp_restore":
        return f"{target_text}恢复MP {_number(value)}"
    if effect_type == "damage_bonus":
        return f"伤害+{_number(value)}"
    if effect_type == "true_damage":
        return f"真实伤害 {_number(value)}"
    if effect_type == "self_damage":
        return f"自身受伤 {_number(value)}"
    if effect_type == "apply_status":
        status = effect.get("status")
        if isinstance(status, Mapping):
            return f"施加状态 {_status_text(status)}"
        return "施加状态"
    if effect_type == "stat_bonus":
        return f"{target_text}{stat_text}+{_number(value)}"
    if effect_type == "stat_multiplier":
        return f"{target_text}{stat_text}倍率 {_number(value)}"
    if stat is not None:
        return f"{effect_type}:{stat_text}:{_number(value)}"
    return f"{effect_type}:{_number(value)}"


def _status_text(status: Mapping[str, Any]) -> str:
    name = str(status.get("name") or status.get("status_id") or "状态")
    duration = status.get("duration")
    polarity = _status_polarity_text(status.get("polarity"))
    effects = [
        _effect_text(effect)
        for effect in _sequence(status.get("effects"))
        if isinstance(effect, Mapping)
    ]
    effect_text = ",".join(effect for effect in effects if effect)
    parts = [name]
    if isinstance(duration, int | float):
        parts.append(f"{_number(duration)}行动")
    if polarity:
        parts.append(polarity)
    if effect_text:
        parts.append(effect_text)
    return " ".join(parts)


def _status_polarity_text(value: Any) -> str:
    labels = {
        "positive": "正面",
        "negative": "负面",
        "neutral": "",
    }
    return labels.get(value, str(value or ""))


def _effect_target_text(value: Any) -> str:
    labels = {
        None: "",
        "target": "",
        "self": "自身",
    }
    if value in labels:
        return labels[value]
    return f"{value}."


def _stat_text(value: Any) -> str:
    labels = {
        "hp": "HP",
        "mp": "MP",
        "attack": "攻击",
        "defense": "防御",
        "speed": "速度",
        "recovery": "恢复",
        "mp_recovery": "回魔",
    }
    return labels.get(value, str(value))


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
