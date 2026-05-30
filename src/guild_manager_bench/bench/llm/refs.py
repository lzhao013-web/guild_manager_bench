from __future__ import annotations

from typing import Any, Mapping, Sequence


RefMap = dict[str, dict[str, int]]


def build_numeric_refs(observation: Mapping[str, Any]) -> RefMap:
    """Build per-category numeric refs from an observation."""

    return {
        "adventurer": _refs_for(observation, "adventurers", "adventurer_id"),
        "monster": _refs_for(observation, "monsters", "monster_id"),
        "recipe": _refs_for(observation, "crafting_recipes", "recipe_id"),
        "upgrade": _refs_for(observation, "global_upgrades", "upgrade_id"),
        "recruit": _refs_for(observation, "recruit_candidates", "candidate_id"),
        "equipment": _refs_for(
            observation,
            "equipment_inventory",
            "instance_id",
        ),
    }


def update_numeric_refs(refs: RefMap, observation: Mapping[str, Any]) -> None:
    """增量更新 refs：为新增项分配新序号，保留已有项的原始序号不变。"""

    _update_category(refs, "adventurer", observation, "adventurers", "adventurer_id")
    _update_category(refs, "monster", observation, "monsters", "monster_id")
    _update_category(refs, "recipe", observation, "crafting_recipes", "recipe_id")
    _update_category(refs, "upgrade", observation, "global_upgrades", "upgrade_id")
    _update_category(refs, "recruit", observation, "recruit_candidates", "candidate_id")
    _update_category(refs, "equipment", observation, "equipment_inventory", "instance_id")


def display_ref(
    refs: Mapping[str, Mapping[str, int]],
    category: str,
    value: Any,
) -> str:
    """Return the numeric ref text for a canonical id, or the original value."""

    if value is None:
        return ""
    ref = refs.get(category, {}).get(str(value))
    return str(ref) if ref is not None else str(value)


def display_ref_with_name(
    refs: Mapping[str, Mapping[str, int]],
    category: str,
    value: Any,
    name: Any,
) -> str:
    """Return a numeric ref plus display name when available."""

    ref = display_ref(refs, category, value)
    if isinstance(name, str) and name:
        return f"{ref} {name}"
    return ref


def resolve_tool_arguments(
    observation: Mapping[str, Any],
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve LLM-facing numeric refs into canonical game ids."""

    refs = build_numeric_refs(observation)
    return resolve_tool_arguments_with_refs(refs, name, arguments)


def resolve_tool_arguments_with_refs(
    refs: Mapping[str, Mapping[str, int]],
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """使用预构建的 refs 映射解析 LLM 数字 ID 为游戏内部 ID。"""

    values = dict(arguments)
    if name == "craft_equipment":
        _resolve_field(values, "recipe_id", "recipe", refs)
    elif name == "purchase_upgrade":
        _resolve_field(values, "upgrade_id", "upgrade", refs)
    elif name == "allocate_experience":
        _resolve_field(values, "adventurer_id", "adventurer", refs)
    elif name == "recruit_adventurer":
        _resolve_field(values, "candidate_id", "recruit", refs)
    elif name == "equip_item":
        _resolve_field(values, "adventurer_id", "adventurer", refs)
        _resolve_field(values, "equipment_instance_id", "equipment", refs)
    elif name == "unequip_item":
        _resolve_field(values, "adventurer_id", "adventurer", refs)
    elif name == "end_turn":
        hunts = values.get("hunts")
        if isinstance(hunts, Sequence) and not isinstance(hunts, str):
            values["hunts"] = [
                _resolve_hunt(hunt, refs)
                for hunt in hunts
            ]
    elif name == "preview_battle":
        _resolve_field(values, "adventurer_id", "adventurer", refs)
        _resolve_field(values, "monster_id", "monster", refs)
    return values


def _refs_for(
    observation: Mapping[str, Any],
    list_key: str,
    id_key: str,
) -> dict[str, int]:
    refs: dict[str, int] = {}
    for index, item in enumerate(_sequence(observation.get(list_key)), start=1):
        if isinstance(item, Mapping):
            value = item.get(id_key)
            if isinstance(value, str) and value:
                refs[value] = index
    return refs


def _resolve_hunt(
    hunt: Any,
    refs: Mapping[str, Mapping[str, int]],
) -> Any:
    if not isinstance(hunt, Mapping):
        return hunt
    values = dict(hunt)
    _resolve_field(values, "adventurer_id", "adventurer", refs)
    _resolve_field(values, "monster_id", "monster", refs)
    return values


def _resolve_field(
    values: dict[str, Any],
    field: str,
    category: str,
    refs: Mapping[str, Mapping[str, int]],
) -> None:
    if field not in values:
        return
    values[field] = _resolve_ref(values[field], category, field, refs)


def _resolve_ref(
    value: Any,
    category: str,
    field: str,
    refs: Mapping[str, Mapping[str, int]],
) -> Any:
    numeric = _numeric_value(value)
    if numeric is None:
        return value
    if numeric <= 0:
        raise ValueError(f"{field} must be a positive numeric id")
    for canonical_id, ref in refs.get(category, {}).items():
        if ref == numeric:
            return canonical_id
    raise ValueError(f"{field} numeric id not found: {numeric}")


def _numeric_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else ()


def _update_category(
    refs: RefMap,
    category: str,
    observation: Mapping[str, Any],
    list_key: str,
    id_key: str,
) -> None:
    """增量更新单个类别的 refs：已有项保持原序号，新增项追加新序号。"""

    category_refs = refs.get(category, {})
    existing_ids = set(category_refs.keys())
    max_ref = max(category_refs.values()) if category_refs else 0

    for item in _sequence(observation.get(list_key)):
        if not isinstance(item, Mapping):
            continue
        canonical_id = item.get(id_key)
        if isinstance(canonical_id, str) and canonical_id and canonical_id not in existing_ids:
            max_ref += 1
            category_refs[canonical_id] = max_ref

    refs[category] = category_refs
