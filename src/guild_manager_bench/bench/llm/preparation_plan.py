"""只在不可变游戏状态上评估准备动作，不创建会话或写入事件。"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping, Sequence

from guild_manager_bench.game.engine import apply_preparation_action
from guild_manager_bench.game.state import GameDefinition, GameState
from guild_manager_bench.runtime.action_codec import decode_preparation_action


MAX_PLAN_OPERATIONS = 20
ALIAS_PATTERN = r"[A-Za-z][A-Za-z0-9_]{0,31}"
PREPARATION_TOOLS = {
    "craft_equipment": "craft",
    "purchase_upgrade": "purchase_upgrade",
    "allocate_experience": "allocate_experience",
    "recruit_adventurer": "recruit",
    "dismiss_adventurer": "dismiss",
    "equip_item": "equip",
    "unequip_item": "unequip",
    "configure_tactics": "configure_tactics",
    "refresh_monsters": "refresh_monsters",
}
_RESULT_FIELDS = {
    "recruit_adventurer": "adventurer_id",
    "craft_equipment": "equipment_instance_id",
}


def plan_operations_schema(schemas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """复用真实动作参数，仅为方案中新实体增加局部引用。"""
    variants = []
    for schema in schemas:
        name = schema["name"]
        if name not in PREPARATION_TOOLS:
            continue
        arguments = deepcopy(schema["parameters"])
        for field in ("adventurer_id", "equipment_instance_id"):
            if field in arguments["properties"]:
                original = arguments["properties"][field]
                arguments["properties"][field] = {
                    "description": "现有对象的数字 id，或前序步骤通过 as 定义的 $变量名。",
                    "anyOf": [original, {"type": "string", "pattern": rf"^\${ALIAS_PATTERN}$"}],
                }
        properties = {
            "tool": {"type": "string", "enum": [name]},
            "arguments": arguments,
        }
        if name in _RESULT_FIELDS:
            properties["as"] = {
                "type": "string", "pattern": rf"^{ALIAS_PATTERN}$",
                "description": "可选：为本步新招募角色或新制作装备命名（不含 $，如 new_hero），供本方案后续步骤以 $new_hero 引用。",
            }
        variants.append({
            "type": "object", "required": ["tool", "arguments"],
            "properties": properties, "additionalProperties": False,
        })
    return {
        "type": "array", "maxItems": MAX_PLAN_OPERATIONS,
        "items": {"anyOf": variants},
        "description": "按顺序校验并试算的准备动作列表（仅限准备工具，不可含查询、预览、write_memo 或 end_turn）；传 [] 仅评估当前状态。",
    }


def simulate_preparation_plan(
    definition: GameDefinition,
    state: GameState,
    operations: Any,
    schemas: Sequence[Mapping[str, Any]],
) -> GameState:
    """失败时抛出带步骤位置的错误，调用方不会获得部分执行结果。"""
    if not isinstance(operations, list) or len(operations) > MAX_PLAN_OPERATIONS:
        raise ValueError(f"operations 必须是最多 {MAX_PLAN_OPERATIONS} 步的列表")
    allowed = {schema["name"]: schema["parameters"] for schema in schemas
               if schema["name"] in PREPARATION_TOOLS}
    aliases: dict[str, tuple[str, str]] = {}
    for index, operation in enumerate(operations, start=1):
        try:
            if not isinstance(operation, Mapping):
                raise ValueError("操作必须是对象")
            tool = operation.get("tool")
            if not isinstance(tool, str) or tool not in allowed:
                raise ValueError("只能使用已启用的准备动作，不允许查询、预览、备忘录或结束回合")
            if set(operation) - {"tool", "arguments", "as"}:
                raise ValueError("操作包含未知字段")
            arguments = operation.get("arguments")
            if not isinstance(arguments, Mapping):
                raise ValueError("arguments 必须是对象")
            schema = allowed[tool]
            if set(arguments) - set(schema["properties"]):
                raise ValueError("arguments 包含未知参数")
            missing = set(schema.get("required", ())) - set(arguments)
            if missing:
                raise ValueError(f"缺少参数: {', '.join(sorted(missing))}")
            if "as" in operation:
                alias = operation["as"]
                if tool not in _RESULT_FIELDS:
                    raise ValueError("只有招募和制作操作支持 as")
                if not isinstance(alias, str) or re.fullmatch(ALIAS_PATTERN, alias) is None:
                    raise ValueError("as 必须是字母开头、最多32位的字母数字下划线名称")
                if alias in aliases:
                    raise ValueError(f"重复的方案名称: {alias}")
            arguments = dict(arguments)
            for field in ("adventurer_id", "equipment_instance_id"):
                value = arguments.get(field)
                if isinstance(value, str) and value.startswith("$"):
                    if value[1:] not in aliases:
                        raise ValueError(f"未定义的方案引用: {value}")
                    kind, canonical_id = aliases[value[1:]]
                    if kind != field:
                        raise ValueError(f"方案引用 {value} 不是 {field}")
                    arguments[field] = canonical_id
            action = decode_preparation_action({"type": PREPARATION_TOOLS[tool], **arguments})
            state = apply_preparation_action(definition, state, action)
            if "as" in operation:
                field = _RESULT_FIELDS[tool]
                canonical_id = (
                    state.adventurers[-1].adventurer_id if field == "adventurer_id"
                    else state.equipment_inventory[-1].instance_id
                )
                aliases[operation["as"]] = (field, canonical_id)
        except (ValueError, TypeError) as exc:
            message = str(exc)
            for alias, (_, canonical_id) in aliases.items():
                message = message.replace(canonical_id, f"${alias}")
            raise ValueError(f"方案第 {index} 步：{message}") from exc
    return state
