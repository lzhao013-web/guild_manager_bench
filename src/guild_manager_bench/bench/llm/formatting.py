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
    name = str(skill.get("name") or skill.get("skill_id") or "技能")
    kind = skill.get("kind")
    condition = _condition_text(skill.get("condition")) or "总是"
    effects = [
        _effect_text(effect)
        for effect in _sequence(skill.get("effects"))
        if isinstance(effect, Mapping)
    ]
    effect_text = "，".join(effect for effect in effects if effect) or "无"

    if kind == "passive":
        return f"{name}：被动技能。生效条件：{condition}。效果：{effect_text}。"

    sentences = [
        f"{name}：{'即时主动技能' if skill.get('free') else '主动技能'}。"
    ]
    limits: list[str] = []
    mp_cost = skill.get("mp_cost")
    if isinstance(mp_cost, int | float) and mp_cost:
        limits.append(f"需要{_number(mp_cost)}MP")
    if skill.get("once_per_battle"):
        limits.append("每场战斗最多触发一次")
    if limits:
        sentences.append("限制：" + "，".join(limits) + "。")
    sentences.append(f"触发条件：{condition}。")
    replacement = "触发后不替代普通攻击" if skill.get("free") else "触发后替代普通攻击"
    sentences.append(f"效果：{replacement}，{effect_text}。")
    return "".join(sentences)


def _condition_text(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    condition_type = value.get("type")
    if condition_type in (None, "always"):
        return "总是"
    if condition_type in {"all", "any"}:
        children = [
            _condition_text(child)
            for child in _sequence(value.get("conditions"))
            if isinstance(child, Mapping)
        ]
        children = [child for child in children if child and child != "总是"]
        if not children:
            return "总是"
        prefix = "同时满足" if condition_type == "all" else "满足任一条件"
        return f"{prefix}（{'；'.join(children)}）"
    raw = str(condition_type)
    if condition_type == "self_hp_pct_lte":
        return f"自身HP不高于{_percent(value.get('value'))}"
    if condition_type == "self_hp_pct_gte":
        return f"自身HP不低于{_percent(value.get('value'))}"
    if condition_type == "target_hp_pct_lte":
        return f"目标HP不高于{_percent(value.get('value'))}"
    if condition_type == "target_hp_pct_gte":
        return f"目标HP不低于{_percent(value.get('value'))}"
    if condition_type == "self_mp_pct_lte":
        return f"自身MP不高于{_percent(value.get('value'))}"
    if condition_type == "self_mp_pct_gte":
        return f"自身MP不低于{_percent(value.get('value'))}"
    if condition_type == "target_mp_pct_lte":
        return f"目标MP不高于{_percent(value.get('value'))}"
    if condition_type == "target_mp_pct_gte":
        return f"目标MP不低于{_percent(value.get('value'))}"
    if condition_type == "action_index_lte":
        return f"行动序号不超过{_number(value.get('value'))}"
    if condition_type == "action_index_gte":
        return f"行动序号至少为{_number(value.get('value'))}"
    condition_value = value.get("value")
    return raw if condition_value is None else f"{raw}:{condition_value}"


def _effect_text(effect: Mapping[str, Any], *, status_context: bool = False) -> str:
    effect_type = effect.get("type")
    value = effect.get("value")
    stat = effect.get("stat")
    target = effect.get("target")
    target_text = _effect_target_text(target, status_context=status_context)
    stat_text = _stat_text(stat)
    if status_context:
        if effect_type == "true_damage":
            return f"{target_text}每次行动开始受到{_number(value)}点无视防御伤害"
        if effect_type == "heal":
            return f"{target_text}每次行动开始恢复{_number(value)}点HP"
        if effect_type == "heal_percent":
            return f"{target_text}每次行动开始恢复{_percent(value)}最大HP"
        if effect_type == "mp_restore":
            return f"{target_text}每次行动开始恢复{_number(value)}点MP"
        if effect_type == "stat_bonus":
            return f"{target_text}{stat_text}+{_number(value)}"
        if effect_type == "stat_multiplier":
            return f"{target_text}{stat_text}×{_number(value)}"
    if effect_type == "damage_multiplier":
        return f"造成普通攻击伤害的{_number(value)}倍"
    if effect_type == "heal":
        return f"为{target_text}恢复{_number(value)}点HP"
    if effect_type == "heal_percent":
        return f"为{target_text}恢复{_percent(value)}最大HP"
    if effect_type == "mp_restore":
        return f"为{target_text}恢复{_number(value)}点MP"
    if effect_type == "damage_bonus":
        return f"在普通攻击伤害上额外+{_number(value)}"
    if effect_type == "true_damage":
        return f"造成{_number(value)}点无视防御伤害"
    if effect_type == "atk_ratio_damage":
        return f"造成攻击力的{_percent(value)}作为无视防御伤害"
    if effect_type == "self_damage":
        return f"自身受到{_number(value)}点伤害"
    if effect_type == "apply_status":
        status = effect.get("status")
        if isinstance(status, Mapping):
            return f"对{target_text}施加状态：{_status_text(status)}"
        return f"对{target_text}施加状态"
    if effect_type == "stat_bonus":
        return f"{target_text}{stat_text}+{_number(value)}"
    if effect_type == "stat_multiplier":
        return f"{target_text}{stat_text}×{_number(value)}"
    if stat is not None:
        return f"{effect_type}:{stat_text}:{_number(value)}"
    return f"{effect_type}:{_number(value)}"


def _status_text(status: Mapping[str, Any]) -> str:
    name = str(status.get("name") or status.get("status_id") or "状态")
    duration = status.get("duration")
    polarity = _status_polarity_text(status.get("polarity"))
    stack = _status_stack_text(status.get("stack_mode"))
    effects = [
        _effect_text(effect, status_context=True)
        for effect in _sequence(status.get("effects"))
        if isinstance(effect, Mapping)
    ]
    effect_text = "；".join(effect for effect in effects if effect)
    parts = []
    if polarity:
        parts.append(f"{polarity}状态")
    if isinstance(duration, int | float):
        parts.append(f"持续{_number(duration)}次行动")
    if stack:
        parts.append(stack)
    if effect_text:
        parts.append(f"效果：{effect_text}")
    detail = "，".join(parts)
    return f"{name}（{detail}）" if detail else name


def _status_polarity_text(value: Any) -> str:
    labels = {
        "positive": "正面",
        "negative": "负面",
        "neutral": "",
    }
    return labels.get(value, str(value or ""))


def _status_stack_text(value: Any) -> str:
    labels = {
        "refresh": "重复施加会刷新持续时间",
        "replace": "重复施加会替换旧状态",
        "add_duration": "重复施加会延长持续时间",
        "stack": "可叠加",
    }
    return labels.get(value, str(value or ""))


def _effect_target_text(value: Any, *, status_context: bool = False) -> str:
    if status_context:
        return "状态持有者"
    labels = {
        None: "目标",
        "target": "目标",
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
