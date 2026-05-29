"""LLM benchmark 内部使用的 tool-use 适配层。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from guild_manager_bench.game.engine import GameError, preview_battle as preview_battle_result
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources
from guild_manager_bench.game.state import GameDefinition, GameState
from guild_manager_bench.runtime.action_codec import (
    ActionCodecError,
    decode_end_turn_action,
    decode_preparation_action,
)
from guild_manager_bench.runtime.session import GameSession


class ToolCallError(ValueError):
    """工具调用参数或会话状态不合法。"""


def create_toolbox(data_dir: str | Path = "data/presets/default") -> GuildManagerTools:
    """从默认数据 preset 创建一组 LLM tool-use 工具。"""

    return GuildManagerTools(load_game_definition(data_dir))


def tool_schemas(*, expose_battle_preview: bool = False) -> list[dict[str, Any]]:
    """返回可注册到 LLM agent 框架的工具 JSON Schema。"""

    schemas = list(_BASE_TOOL_SCHEMAS)
    if expose_battle_preview:
        schemas.append(_BATTLE_PREVIEW_SCHEMA)
    return [_without_session_id(schema) for schema in schemas]


class GuildManagerTools:
    """面向 LLM agent 的强类型工具层。

    工具层只暴露事实查询和游戏动作，不包含推荐、估值或自动配队逻辑。
    """

    def __init__(self, definition: GameDefinition) -> None:
        if not isinstance(definition, GameDefinition):
            raise TypeError("definition must be GameDefinition")
        self.definition = definition
        self._sessions: dict[str, GameSession] = {}
        self._lock = RLock()

    @classmethod
    def from_data_dir(
        cls,
        data_dir: str | Path = "data/presets/default",
    ) -> GuildManagerTools:
        """从默认数据 preset 加载游戏定义并创建工具层。"""

        return cls(load_game_definition(data_dir))

    def list_tool_schemas(self) -> list[dict[str, Any]]:
        """返回当前工具层支持的工具 schema。"""

        return tool_schemas(
            expose_battle_preview=self.definition.llm_tools.expose_battle_preview
        )

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """按工具名和参数调用一个工具。"""

        handler = self._handler(name)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            raise ToolCallError("tool arguments must be an object")
        try:
            return handler(**dict(arguments))
        except TypeError as exc:
            raise ToolCallError(f"invalid arguments for tool {name}: {exc}") from exc

    def start_session(self, session_id: str | None = None) -> dict[str, Any]:
        """创建新会话。"""

        if session_id is not None and not _non_empty_string(session_id):
            raise ToolCallError("session_id must be a non-empty string")

        with self._lock:
            session = GameSession(
                definition=self.definition,
                session_id=session_id or uuid4().hex,
            )
            if session.session_id in self._sessions:
                raise ToolCallError(f"duplicate session id: {session.session_id}")
            self._sessions[session.session_id] = session
            return _session_snapshot(session)

    def get_observation(self, session_id: str) -> dict[str, Any]:
        """读取当前完整可见状态。"""

        with self._lock:
            session = self._get_session(session_id)
            return {"session_id": session.session_id, "observation": session.observation()}

    def get_party(self, session_id: str) -> dict[str, Any]:
        """读取冒险者和经验相关详细信息。"""

        with self._lock:
            session = self._get_session(session_id)
            observation = session.observation()
            return {
                "session_id": session.session_id,
                "turn": observation["turn"],
                "max_turns": observation["max_turns"],
                "experience_pool": observation["experience_pool"],
                "experience_rules": observation["experience_rules"],
                "adventurers": observation["adventurers"],
            }

    def get_monsters(self, session_id: str) -> dict[str, Any]:
        """读取当前怪物详细信息。"""

        with self._lock:
            session = self._get_session(session_id)
            observation = session.observation()
            return {
                "session_id": session.session_id,
                "turn": observation["turn"],
                "max_turns": observation["max_turns"],
                "monsters": observation["monsters"],
            }

    def get_crafting(self, session_id: str) -> dict[str, Any]:
        """读取资源和制作配方详细信息。"""

        with self._lock:
            session = self._get_session(session_id)
            observation = session.observation()
            return {
                "session_id": session.session_id,
                "gold": observation["gold"],
                "materials": observation["materials"],
                "crafting_recipes": observation["crafting_recipes"],
            }

    def get_inventory(self, session_id: str) -> dict[str, Any]:
        """读取装备库存详细信息。"""

        with self._lock:
            session = self._get_session(session_id)
            observation = session.observation()
            return {
                "session_id": session.session_id,
                "equipment_inventory": observation["equipment_inventory"],
            }

    def get_upgrades(self, session_id: str) -> dict[str, Any]:
        """读取金币和全局升级详细信息。"""

        with self._lock:
            session = self._get_session(session_id)
            observation = session.observation()
            return {
                "session_id": session.session_id,
                "gold": observation["gold"],
                "global_upgrades": observation["global_upgrades"],
            }

    def get_recruitment(self, session_id: str) -> dict[str, Any]:
        """读取招募候选和队伍人数上限详细信息。"""

        with self._lock:
            session = self._get_session(session_id)
            observation = session.observation()
            return {
                "session_id": session.session_id,
                "turn": observation["turn"],
                "max_turns": observation["max_turns"],
                "gold": observation["gold"],
                "party_size": observation["party_size"],
                "party_size_limit": observation["party_size_limit"],
                "recruit_candidates": observation["recruit_candidates"],
            }

    def get_state(self, session_id: str) -> GameState:
        """读取内部状态，供 benchmark harness 做离线评估。"""

        with self._lock:
            session = self._get_session(session_id)
            assert session.state is not None
            return session.state

    def craft_equipment(self, session_id: str, recipe_id: str) -> dict[str, Any]:
        """按配方合成一件装备。"""

        return self._submit_preparation(
            session_id,
            {"type": "craft", "recipe_id": recipe_id},
        )

    def purchase_upgrade(self, session_id: str, upgrade_id: str) -> dict[str, Any]:
        """购买一个全局加成。"""

        return self._submit_preparation(
            session_id,
            {"type": "purchase_upgrade", "upgrade_id": upgrade_id},
        )

    def allocate_experience(
        self,
        session_id: str,
        adventurer_id: str,
        amount: int,
    ) -> dict[str, Any]:
        """把经验池中的经验分配给冒险者。"""

        return self._submit_preparation(
            session_id,
            {
                "type": "allocate_experience",
                "adventurer_id": adventurer_id,
                "amount": amount,
            },
        )

    def recruit_adventurer(
        self,
        session_id: str,
        candidate_id: str,
    ) -> dict[str, Any]:
        """招募一个候选冒险者。"""

        return self._submit_preparation(
            session_id,
            {
                "type": "recruit",
                "candidate_id": candidate_id,
            },
        )

    def equip_item(
        self,
        session_id: str,
        adventurer_id: str,
        equipment_instance_id: str,
    ) -> dict[str, Any]:
        """把装备实例穿戴到冒险者身上。"""

        return self._submit_preparation(
            session_id,
            {
                "type": "equip",
                "adventurer_id": adventurer_id,
                "equipment_instance_id": equipment_instance_id,
            },
        )

    def unequip_item(
        self,
        session_id: str,
        adventurer_id: str,
        slot: str,
    ) -> dict[str, Any]:
        """卸下冒险者指定槽位上的装备。"""

        return self._submit_preparation(
            session_id,
            {
                "type": "unequip",
                "adventurer_id": adventurer_id,
                "slot": slot,
            },
        )

    def end_turn(
        self,
        session_id: str,
        hunts: Sequence[Mapping[str, str]] | None = None,
    ) -> dict[str, Any]:
        """提交讨伐列表并结束当前回合。"""

        payload = {
            "type": "end_turn",
            "hunts": [] if hunts is None else list(hunts),
        }
        with self._lock:
            session = self._get_session(session_id)
            try:
                result, event = session.end_turn(decode_end_turn_action(payload))
            except (ActionCodecError, GameError, ValueError, TypeError) as exc:
                event = session.reject_action(payload, str(exc))
                return _error_response(session, event, str(exc))

            return {
                "ok": True,
                "event": _compact_event(event),
                "turn_result": {
                    "battles": [
                        _compact_battle(battle)
                        for battle in event.payload["battles"]
                    ],
                    "crafted_equipment_ids": list(result.crafted_equipment_ids),
                    "purchased_upgrade_ids": list(result.purchased_upgrade_ids),
                    "recruited_adventurer_ids": list(result.recruited_adventurer_ids),
                },
            }

    def get_events(
        self,
        session_id: str,
        since_sequence: int | None = None,
    ) -> dict[str, Any]:
        """读取事件日志，可只返回指定序号之后的新事件。"""

        if since_sequence is not None and (
            not isinstance(since_sequence, int) or isinstance(since_sequence, bool)
        ):
            raise ToolCallError("since_sequence must be an integer")
        with self._lock:
            session = self._get_session(session_id)
            events = session.events
            if since_sequence is not None:
                events = [
                    event
                    for event in events
                    if event.sequence > since_sequence
                ]
            return {
                "session_id": session.session_id,
                "events": [_compact_event(event) for event in events],
            }

    def preview_battle(
        self,
        session_id: str,
        adventurer_id: str,
        monster_id: str,
    ) -> dict[str, Any]:
        """预览一场 1v1 战斗，不推进会话状态。"""

        with self._lock:
            session = self._get_session(session_id)
            assert session.state is not None
            try:
                battle = preview_battle_result(
                    session.definition,
                    session.state,
                    adventurer_id=adventurer_id,
                    monster_id=monster_id,
                )
            except (GameError, ValueError, TypeError) as exc:
                return {"ok": False, "error": str(exc)}
            return {
                "ok": True,
                "preview": _battle_preview(
                    session.state,
                    battle,
                ),
            }

    def _submit_preparation(
        self,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            session = self._get_session(session_id)
            try:
                event = session.apply_preparation(decode_preparation_action(payload))
            except (ActionCodecError, GameError, ValueError, TypeError) as exc:
                event = session.reject_action(payload, str(exc))
                return _error_response(session, event, str(exc))
            return {
                "ok": True,
                "event": _compact_event(event),
            }

    def _get_session(self, session_id: str) -> GameSession:
        if not _non_empty_string(session_id):
            raise ToolCallError("session_id must be a non-empty string")
        session = self._sessions.get(session_id)
        if session is None:
            raise ToolCallError(f"session not found: {session_id}")
        return session

    def _handler(self, name: str) -> Callable[..., dict[str, Any]]:
        if not _non_empty_string(name):
            raise ToolCallError("tool name must be a non-empty string")
        handler_names = self._handler_names()
        if name not in handler_names:
            raise ToolCallError(f"unknown tool: {name}")
        return getattr(self, handler_names[name])

    def _handler_names(self) -> dict[str, str]:
        names = dict(_BASE_HANDLER_NAMES)
        if self.definition.llm_tools.expose_battle_preview:
            names["preview_battle"] = "preview_battle"
        return names


def _session_snapshot(session: GameSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "observation": session.observation(),
        "events": [_compact_event(event) for event in session.events],
    }


def _compact_event(event) -> dict[str, Any]:
    payload = dict(event.payload)
    data: dict[str, Any] = {
        "sequence": event.sequence,
        "turn": event.turn,
        "type": event.event_type,
        "summary": payload.get("summary", ""),
    }
    if "action" in payload:
        data["action"] = payload["action"]
    if "error" in payload:
        data["error"] = payload["error"]
    if "changes" in payload:
        data["changes"] = list(payload["changes"])
    if "battles" in payload:
        data["battles"] = [_compact_battle(battle) for battle in payload["battles"]]
    return data


def _compact_battle(battle: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "adventurer_id": battle.get("adventurer_id"),
        "adventurer_name": battle.get("adventurer_name"),
        "monster_id": battle.get("monster_id"),
        "monster_name": battle.get("monster_name"),
        "won": battle.get("won"),
        "reward": dict(battle.get("reward", {})),
    }


def _battle_preview(state, battle) -> dict[str, Any]:
    adventurer = _adventurer_by_id(state, battle.adventurer_id)
    monster = _monster_by_id(state, battle.monster_id)
    result = battle.combat_result
    return {
        "adventurer_id": battle.adventurer_id,
        "adventurer_name": adventurer.name,
        "monster_id": battle.monster_id,
        "monster_name": monster.name,
        "won": battle.won,
        "outcome": result.outcome,
        "reason": result.reason,
        "actions_taken": result.actions_taken,
        "time_elapsed": result.time_elapsed,
        "adventurer_resources": {
            "before": _resources_to_dict(adventurer.resources),
            "after": _resources_to_dict(result.left_resources),
        },
        "monster_resources": {
            "before": _resources_to_dict(CombatResources.full(monster.stats)),
            "after": _resources_to_dict(result.right_resources),
        },
        "reward": {
            "gold": battle.reward.gold,
            "experience": battle.reward.experience,
            "materials": dict(battle.reward.materials),
        },
        "events": [
            _compact_combat_event(event)
            for event in result.events
        ],
    }


def _resources_to_dict(resources: CombatResources) -> dict[str, int]:
    return {
        "current_hp": resources.current_hp,
        "current_mp": resources.current_mp,
    }


def _compact_combat_event(event) -> dict[str, Any]:
    return {
        "action_index": event.action_index,
        "time_elapsed": event.time_elapsed,
        "action_type": event.action_type,
        "actor_id": event.actor_id,
        "target_id": event.target_id,
        "damage": event.damage,
        "target_hp": event.target_hp,
        "skill_id": event.skill_id,
        "healing": event.healing,
        "healing_target_hp": event.healing_target_hp,
        "status_id": event.status_id,
        "status_name": event.status_name,
    }


def _adventurer_by_id(state, adventurer_id: str):
    for adventurer in state.adventurers:
        if adventurer.adventurer_id == adventurer_id:
            return adventurer
    raise ToolCallError(f"unknown adventurer: {adventurer_id}")


def _monster_by_id(state, monster_id: str):
    for monster in state.current_monsters:
        if monster.monster_id == monster_id:
            return monster
    raise ToolCallError(f"unknown monster: {monster_id}")


def _error_response(
    session: GameSession,
    event,
    error: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "event": _compact_event(event),
    }


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


_BASE_HANDLER_NAMES = {
    "get_party": "get_party",
    "get_monsters": "get_monsters",
    "get_crafting": "get_crafting",
    "get_inventory": "get_inventory",
    "get_upgrades": "get_upgrades",
    "get_recruitment": "get_recruitment",
    "craft_equipment": "craft_equipment",
    "purchase_upgrade": "purchase_upgrade",
    "allocate_experience": "allocate_experience",
    "recruit_adventurer": "recruit_adventurer",
    "equip_item": "equip_item",
    "unequip_item": "unequip_item",
    "end_turn": "end_turn",
    "get_events": "get_events",
}


_SESSION_ID = {
    "type": "string",
    "description": "会话 id，由 start_session 返回。",
}
_ADVENTURER_ID = {
    "type": "integer",
    "minimum": 1,
    "description": "冒险者数字 id，使用提示词或 get_party 中“冒险者”列表左侧的数字。",
}
_MONSTER_ID = {
    "type": "integer",
    "minimum": 1,
    "description": "怪物数字 id，使用提示词或 get_monsters 中“怪物”列表左侧的数字。",
}
_RECIPE_ID = {
    "type": "integer",
    "minimum": 1,
    "description": "配方数字 id，使用 get_crafting 中“制作配方”列表左侧的数字。",
}
_UPGRADE_ID = {
    "type": "integer",
    "minimum": 1,
    "description": "升级数字 id，使用 get_upgrades 中“全局升级”列表左侧的数字。",
}
_RECRUIT_CANDIDATE_ID = {
    "type": "integer",
    "minimum": 1,
    "description": "招募候选数字 id，使用 get_recruitment 中“招募候选”列表左侧的数字。",
}
_EQUIPMENT_INSTANCE_ID = {
    "type": "integer",
    "minimum": 1,
    "description": "装备数字 id，使用 get_inventory 中“装备库存”列表左侧的数字。",
}


_BASE_TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "name": "get_party",
        "description": "读取冒险者、经验池、升级成长和装备槽位等队伍详细信息。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": _SESSION_ID},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_monsters",
        "description": "读取当前回合怪物的属性、技能和奖励详细信息。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": _SESSION_ID},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_crafting",
        "description": "读取当前金币、材料和制作配方详细信息。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": _SESSION_ID},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_inventory",
        "description": "读取当前装备库存、装备属性、技能和装备者详细信息。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": _SESSION_ID},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_upgrades",
        "description": "读取当前金币和全局升级详细信息。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": _SESSION_ID},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_recruitment",
        "description": "读取当前金币、队伍人数上限和可招募候选详细信息。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": _SESSION_ID},
            "additionalProperties": False,
        },
    },
    {
        "name": "craft_equipment",
        "description": "消耗金币和材料，按配方合成装备实例。",
        "parameters": {
            "type": "object",
            "required": ["session_id", "recipe_id"],
            "properties": {
                "session_id": _SESSION_ID,
                "recipe_id": _RECIPE_ID,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "purchase_upgrade",
        "description": "消耗金币购买一个全局加成。",
        "parameters": {
            "type": "object",
            "required": ["session_id", "upgrade_id"],
            "properties": {
                "session_id": _SESSION_ID,
                "upgrade_id": _UPGRADE_ID,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "allocate_experience",
        "description": "把经验池中的指定数量经验分配给冒险者。",
        "parameters": {
            "type": "object",
            "required": ["session_id", "adventurer_id", "amount"],
            "properties": {
                "session_id": _SESSION_ID,
                "adventurer_id": _ADVENTURER_ID,
                "amount": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "要分配的经验值，不能超过当前 experience_pool。",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "recruit_adventurer",
        "description": "消耗金币招募一个候选冒险者加入队伍；队伍人数不能超过当前上限。",
        "parameters": {
            "type": "object",
            "required": ["session_id", "candidate_id"],
            "properties": {
                "session_id": _SESSION_ID,
                "candidate_id": _RECRUIT_CANDIDATE_ID,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "equip_item",
        "description": "把装备实例穿戴到冒险者身上；槽位由装备模板决定。",
        "parameters": {
            "type": "object",
            "required": ["session_id", "adventurer_id", "equipment_instance_id"],
            "properties": {
                "session_id": _SESSION_ID,
                "adventurer_id": _ADVENTURER_ID,
                "equipment_instance_id": _EQUIPMENT_INSTANCE_ID,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "unequip_item",
        "description": "卸下冒险者指定装备槽位上的装备。",
        "parameters": {
            "type": "object",
            "required": ["session_id", "adventurer_id", "slot"],
            "properties": {
                "session_id": _SESSION_ID,
                "adventurer_id": _ADVENTURER_ID,
                "slot": {
                    "type": "string",
                    "enum": [
                        "main_hand",
                        "off_hand",
                        "two_hand",
                        "boots",
                        "helmet",
                        "armor",
                        "accessory",
                    ],
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "end_turn",
        "description": "提交本回合讨伐列表并进入下一回合；hunts 可为空。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": _SESSION_ID,
                "hunts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["adventurer_id", "monster_id"],
                        "properties": {
                            "adventurer_id": _ADVENTURER_ID,
                            "monster_id": _MONSTER_ID,
                        },
                        "additionalProperties": False,
                    },
                    "description": "讨伐匹配列表；同一冒险者和同一怪物本回合只能出现一次。",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_events",
        "description": "读取会话事件日志；可只读取某个 sequence 之后的新事件。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": _SESSION_ID,
                "since_sequence": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "只返回 sequence 大于该值的事件。",
                },
            },
            "additionalProperties": False,
        },
    },
)


_BATTLE_PREVIEW_SCHEMA: dict[str, Any] = {
    "name": "preview_battle",
    "description": (
        "预览一场单独的 1v1 战斗，不改变状态；每次只能传入一个冒险者和一个怪物。只能传入已经招募成功的冒险者，传入招募候选中的冒险者会失败。"
    ),
    "parameters": {
        "type": "object",
        "required": ["session_id", "adventurer_id", "monster_id"],
        "properties": {
            "session_id": _SESSION_ID,
            "adventurer_id": _ADVENTURER_ID,
            "monster_id": _MONSTER_ID,
        },
        "additionalProperties": False,
    },
}


def _without_session_id(schema: Mapping[str, Any]) -> dict[str, Any]:
    data = deepcopy(schema)
    parameters = data.get("parameters")
    if isinstance(parameters, dict):
        required = parameters.get("required")
        if isinstance(required, list):
            kept_required = [
                item
                for item in required
                if item != "session_id"
            ]
            if kept_required:
                parameters["required"] = kept_required
            else:
                parameters.pop("required", None)
        properties = parameters.get("properties")
        if isinstance(properties, dict):
            properties.pop("session_id", None)
    return data
