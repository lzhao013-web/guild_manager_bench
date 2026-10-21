"""LLM benchmark 内部使用的 tool-use 适配层。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from guild_manager_bench.bench.llm.battle_log import BATTLE_LOG_SCHEMA, battle_log_id, query_battle_log
from guild_manager_bench.bench.llm.forecast import FORECAST_SCHEMA, query_forecast
from guild_manager_bench.bench.llm.monster_refresh import PREVIEW_REFRESH_SCHEMA, REFRESH_SCHEMA
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


def tool_schemas(*, expose_battle_preview: bool = False, talent_tree_enabled: bool = False,
                 expose_tactics: bool = False, expose_battle_log: bool = False,
                 expose_forecast: bool = False, expose_ultimates: bool = False,
                 expose_monster_refresh: bool = False,
                 max_battle_preview_per_turn: int = 3) -> list[dict[str, Any]]:
    """返回可注册到 LLM agent 框架的工具 JSON Schema。"""

    schemas = list(_BASE_TOOL_SCHEMAS)
    if expose_battle_preview:
        schemas.append(_battle_preview_schema(max_battle_preview_per_turn))
    if expose_tactics:
        schemas.append(_TACTICS_SCHEMA)
    if expose_battle_log:
        schemas.append(BATTLE_LOG_SCHEMA)
    if expose_forecast:
        schemas.append(FORECAST_SCHEMA)
    if expose_monster_refresh:
        schemas.extend((PREVIEW_REFRESH_SCHEMA, REFRESH_SCHEMA))
    schemas = [_without_session_id(schema) for schema in schemas]
    if expose_ultimates:
        unequip = next(s for s in schemas if s["name"] == "unequip_item")
        unequip["parameters"]["properties"]["slot"]["enum"].append("ultimate")
    if talent_tree_enabled:
        descriptions = {
            "get_upgrades": "查看全局天赋树全部节点的成本、效果、前置、互斥与解锁状态。",
            "purchase_upgrade": "消耗金币、材料和经验池经验解锁一个全局天赋节点。需满足前置且未选互斥节点，解锁后不可退回。",
        }
        for schema in schemas:
            if schema["name"] in descriptions:
                schema["description"] = descriptions[schema["name"]]
    return schemas


class GuildManagerTools:
    """面向 LLM agent 的强类型工具层。

    工具层只暴露事实查询和游戏动作，不包含推荐、估值或自动配队逻辑。
    """

    def __init__(self, definition: GameDefinition) -> None:
        if not isinstance(definition, GameDefinition):
            raise TypeError("definition must be GameDefinition")
        self.definition = definition
        self._sessions: dict[str, GameSession] = {}
        self._battle_previews: dict[str, tuple[int, list[dict[str, Any]]]] = {}
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
            expose_battle_preview=self.definition.llm_tools.expose_battle_preview,
            talent_tree_enabled=self.definition.content.has_talent_tree,
            expose_tactics=self.definition.llm_tools.expose_tactics,
            expose_battle_log=self.definition.llm_tools.expose_battle_log,
            expose_forecast=self.definition.llm_tools.expose_forecast,
            expose_monster_refresh=self.definition.rules.monster_refresh.enabled,
            expose_ultimates=any(t.slot == "ultimate" for t in self.definition.content.equipment_templates),
            max_battle_preview_per_turn=self.definition.llm_tools.max_battle_preview_per_turn,
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
            raise ToolCallError("工具参数必须是 JSON 对象")
        try:
            return handler(**dict(arguments))
        except TypeError as exc:
            raise ToolCallError(f"工具 {name} 的参数不合法: {exc}") from exc

    def start_session(self, session_id: str | None = None) -> dict[str, Any]:
        """创建新会话。"""

        if session_id is not None and not _non_empty_string(session_id):
            raise ToolCallError("session_id 必须是非空字符串")

        with self._lock:
            session = GameSession(
                definition=self.definition,
                session_id=session_id or uuid4().hex,
            )
            if session.session_id in self._sessions:
                raise ToolCallError(f"会话 id 已存在: {session.session_id}")
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
                **({"monster_refresh": observation["monster_refresh"]} if "monster_refresh" in observation else {}),
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
        """读取资源、天赋成本、前置、互斥与效果。"""

        with self._lock:
            session = self._get_session(session_id)
            observation = session.observation()
            return {
                "session_id": session.session_id,
                "gold": observation["gold"],
                **({"experience_pool": observation["experience_pool"], "materials": observation["materials"]}
                   if self.definition.content.has_talent_tree else {}),
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

    def preview_monster_refresh(
        self, session_id: str, locked_monster_ids: Sequence[str],
    ) -> dict[str, Any]:
        """只读预览下一批普通怪物与费用。"""
        with self._lock:
            session = self._get_session(session_id)
            try:
                return session.preview_monster_refresh(locked_monster_ids)
            except (GameError, ValueError, TypeError) as exc:
                return {"ok": False, "error": str(exc)}

    def refresh_monsters(
        self, session_id: str, locked_monster_ids: Sequence[str],
    ) -> dict[str, Any]:
        """付费刷新未锁定的普通怪物。"""
        with self._lock:
            result = self._submit_preparation(session_id, {
                "type": "refresh_monsters", "locked_monster_ids": locked_monster_ids,
            })
            if result["ok"]:
                obs = self._get_session(session_id).observation()
                result.update({"turn": obs["turn"], "monsters": obs["monsters"],
                               "monster_refresh": obs["monster_refresh"]})
            return result

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
        """招募一个候选冒险者，成功后返回新冒险者的详细信息。"""

        result = self._submit_preparation(
            session_id,
            {
                "type": "recruit",
                "candidate_id": candidate_id,
            },
        )
        if result.get("ok"):
            with self._lock:
                session = self._get_session(session_id)
                observation = session.observation()
                adventurers = observation.get("adventurers", [])
                if adventurers:
                    result["recruited_adventurer"] = adventurers[-1]
        return result

    def dismiss_adventurer(
        self,
        session_id: str,
        adventurer_id: str,
    ) -> dict[str, Any]:
        """解散队伍中的冒险者，装备归还库存。"""

        return self._submit_preparation(
            session_id,
            {
                "type": "dismiss",
                "adventurer_id": adventurer_id,
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

    def configure_tactics(
        self, session_id: str, adventurer_id: str,
        skill_priority: Sequence[str], disabled_skills: Sequence[str],
    ) -> dict[str, Any]:
        """替换主动技能配置，两个空列表恢复默认行为。"""
        if not self.definition.llm_tools.expose_tactics:
            raise ToolCallError("configure_tactics 未启用")
        return self._submit_preparation(session_id, {
            "type": "configure_tactics", "adventurer_id": adventurer_id,
            "skill_priority": skill_priority, "disabled_skills": disabled_skills,
        })

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

            self._battle_previews.pop(session_id, None)
            compact_event = _compact_event(
                event, include_battle_ids=self.definition.llm_tools.expose_battle_log,
            )
            return {
                "ok": True,
                "event": compact_event,
                "turn_result": {
                    "battles": compact_event["battles"],
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
            raise ToolCallError("since_sequence 必须是整数")
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
                "events": [
                    _compact_event(event, include_battle_ids=self.definition.llm_tools.expose_battle_log)
                    for event in events
                ],
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
            preview = _battle_preview(session.state, battle)
            if self.definition.llm_tools.expose_battle_log:
                turn, records = self._battle_previews.get(session_id, (session.state.turn, []))
                if turn != session.state.turn:
                    records = []
                combat = battle.combat_result
                records.append({
                    "adventurer_name": preview["adventurer_name"],
                    "monster_name": preview["monster_name"],
                    "combat": {
                        "outcome": combat.outcome, "reason": combat.reason,
                        "actions_taken": combat.actions_taken, "time_elapsed": combat.time_elapsed,
                        "events": [asdict(event) for event in combat.events],
                    },
                })
                self._battle_previews[session_id] = (session.state.turn, records)
                preview["battle_id"] = battle_log_id("preview", session.state.turn, len(records))
            return {"ok": True, "preview": preview}

    def get_battle_log(
        self, session_id: str, battle_id: str, detail: str = "summary", cursor: int = 0,
    ) -> dict[str, Any]:
        """按编号读取当轮预览或上轮实战的原始记录。"""
        if not self.definition.llm_tools.expose_battle_log:
            raise ToolCallError("get_battle_log 未启用")
        with self._lock:
            session = self._get_session(session_id)
            assert session.state is not None
            turn = session.state.turn
            records = {}
            cached_turn, cached = self._battle_previews.get(session_id, (turn, []))
            if cached_turn == turn:
                for index, record in enumerate(cached, 1):
                    records[battle_log_id("preview", turn, index)] = {
                        **record, "source": "preview", "turn": turn,
                    }
            for event in reversed(session.events):
                if event.event_type == "turn_ended" and event.turn == turn - 1:
                    for index, record in enumerate(event.payload["battles"], 1):
                        records[battle_log_id("actual", event.turn, index)] = {
                            **record, "source": "actual", "turn": event.turn,
                        }
                    break
            return query_battle_log(records, battle_id=battle_id, detail=detail, cursor=cursor)

    def get_forecast(
        self, session_id: str, kind: str, start_turn: int, end_turn: int | None = None,
        name_contains: str | None = None, material: str | None = None,
        tier: str | None = None, entry_index: int | None = None,
        detail: str = "summary", offset: int = 0, limit: int = 3,
    ) -> dict[str, Any]:
        """查询独立于玩家操作的未来经营刷新，不创建真实对象编号。"""
        if not self.definition.llm_tools.expose_forecast:
            raise ToolCallError("get_forecast 未启用")
        with self._lock:
            session = self._get_session(session_id)
            assert session.state is not None
            return query_forecast(
                self.definition, session.state, kind=kind, start_turn=start_turn, end_turn=end_turn,
                name_contains=name_contains, material=material, tier=tier, entry_index=entry_index,
                detail=detail, offset=offset, limit=limit,
            )

    def preview_team_power(self, session_id: str, operations: list[Mapping[str, Any]]) -> dict[str, Any]:
        """只返回准备方案相对当前真实状态的战力变化，不写入会话。"""
        from guild_manager_bench.bench.llm.preparation_plan import simulate_preparation_plan
        from guild_manager_bench.bench.metrics import compute_rank_score

        with self._lock:
            session = self._get_session(session_id)
            assert session.state is not None
            start = self.definition.llm_tools.endgame_start_turn
            if start is None or session.state.turn < start:
                return {"ok": False, "simulation_only": True, "error": "仅在终局阶段可预览战力"}
            try:
                projected = simulate_preparation_plan(
                    self.definition, session.state, operations, self.list_tool_schemas(),
                )
            except ValueError as exc:
                return {"ok": False, "simulation_only": True, "error": str(exc)}
            current_score = round(compute_rank_score(self.definition, session.state), 2)
            projected_score = (
                current_score if projected is session.state
                else round(compute_rank_score(self.definition, projected), 2)
            )
            return {
                "ok": True,
                "simulation_only": True,
                "current_rank_score": current_score,
                "projected_rank_score": projected_score,
                "rank_score_delta": round(projected_score - current_score, 2),
                "operation_count": len(operations),
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
            raise ToolCallError("session_id 必须是非空字符串")
        session = self._sessions.get(session_id)
        if session is None:
            raise ToolCallError(f"会话不存在: {session_id}")
        return session

    def _handler(self, name: str) -> Callable[..., dict[str, Any]]:
        if not _non_empty_string(name):
            raise ToolCallError("工具名称必须是非空字符串")
        handler_names = self._handler_names()
        if name not in handler_names:
            raise ToolCallError(f"未知工具: {name}")
        return getattr(self, handler_names[name])

    def _handler_names(self) -> dict[str, str]:
        names = dict(_BASE_HANDLER_NAMES)
        if self.definition.llm_tools.expose_battle_preview:
            names["preview_battle"] = "preview_battle"
        if self.definition.llm_tools.expose_tactics:
            names["configure_tactics"] = "configure_tactics"
        if self.definition.llm_tools.expose_battle_log:
            names["get_battle_log"] = "get_battle_log"
        if self.definition.llm_tools.expose_forecast:
            names["get_forecast"] = "get_forecast"
        if self.definition.rules.monster_refresh.enabled:
            names["preview_monster_refresh"] = "preview_monster_refresh"
            names["refresh_monsters"] = "refresh_monsters"
        if self.definition.llm_tools.endgame_start_turn is not None:
            names["preview_team_power"] = "preview_team_power"
        return names


def _session_snapshot(session: GameSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "observation": session.observation(),
        "events": [
            _compact_event(event, include_battle_ids=session.definition.llm_tools.expose_battle_log)
            for event in session.events
        ],
    }


def _compact_event(event, *, include_battle_ids: bool = False) -> dict[str, Any]:
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
        if include_battle_ids:
            for index, battle in enumerate(data["battles"], 1):
                battle["battle_id"] = battle_log_id("actual", event.turn, index)
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
    raise ToolCallError(f"未找到冒险者: {adventurer_id}")


def _monster_by_id(state, monster_id: str):
    for monster in state.current_monsters:
        if monster.monster_id == monster_id:
            return monster
    raise ToolCallError(f"未找到怪物: {monster_id}")


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
    "dismiss_adventurer": "dismiss_adventurer",
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
    "description": "冒险者数字 id（“冒险者”列表左侧数字）。",
}
_MONSTER_ID = {
    "type": "integer",
    "minimum": 1,
    "description": "怪物数字 id（“当前怪物”列表左侧数字）。",
}
_RECIPE_ID = {
    "type": "integer",
    "minimum": 1,
    "description": "配方数字 id（get_crafting“制作配方”列表左侧数字）。",
}
_UPGRADE_ID = {
    "type": "integer",
    "minimum": 1,
    "description": "升级数字 id（get_upgrades“全局升级”列表左侧数字）。",
}
_RECRUIT_CANDIDATE_ID = {
    "type": "integer",
    "minimum": 1,
    "description": "招募候选数字 id（get_recruitment“招募候选”列表左侧数字）。",
}
_EQUIPMENT_INSTANCE_ID = {
    "type": "integer",
    "minimum": 1,
    "description": "装备数字 id（get_inventory“装备库存”列表左侧数字）。",
}


_BASE_TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "name": "get_crafting",
        "description": "查看当前金币、材料及全部装备配方（产物槽位、属性、技能、职业限制、成本与缺口）。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": _SESSION_ID},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_inventory",
        "description": "查看装备库存：空闲装备展示完整槽位、属性、职业限制与技能，已穿戴装备展示槽位与装备者。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": _SESSION_ID},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_upgrades",
        "description": "查看全局升级的成本、效果与购买状态。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": _SESSION_ID},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_recruitment",
        "description": "查看本回合招募候选的职业、价格、初始属性、成长与技能（队伍人数及上限见回合概览）。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": _SESSION_ID},
            "additionalProperties": False,
        },
    },
    {
        "name": "craft_equipment",
        "description": "按配方消耗金币和材料制作一件装备，产物进入库存。",
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
        "description": "消耗金币购买一项全局升级。",
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
        "description": "从经验池向指定角色分配经验。升级提升最大HP/MP时，当前值按相同增幅增加，不会直接回满。",
        "parameters": {
            "type": "object",
            "required": ["session_id", "adventurer_id", "amount"],
            "properties": {
                "session_id": _SESSION_ID,
                "adventurer_id": _ADVENTURER_ID,
                "amount": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "分配的经验值，不超过当前经验池。",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "recruit_adventurer",
        "description": "消耗金币招募一名候选角色入队，要求队伍未满员。",
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
        "name": "dismiss_adventurer",
        "description": "解雇一名在队角色：装备归还库存，已投入经验全额返还经验池，招募金币不退。",
        "parameters": {
            "type": "object",
            "required": ["session_id", "adventurer_id"],
            "properties": {
                "session_id": _SESSION_ID,
                "adventurer_id": _ADVENTURER_ID,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "equip_item",
        "description": (
            "为角色穿戴指定装备。自动替换冲突槽位（同槽位，或 two_hand 与 main_hand/off_hand 互换）；"
            "若装备在队友身上，直接转移，无需先卸下。"
        ),
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
        "description": "卸下角色指定槽位的装备并归还库存。",
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
        "description": "提交本回合1v1讨伐配对并结束回合。hunts 可为空（如最终整备回合或全员休整）。",
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
                    "description": "讨伐配对列表；每名角色（当前HP>0）和每个怪物本回合最多出战一次。",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_events",
        "description": "查看本局历史操作与回合结算流水账（仅含简短摘要，不含战斗详情）。",
        "parameters": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": _SESSION_ID,
                "since_sequence": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "仅返回 sequence 大于该值的事件。",
                },
            },
            "additionalProperties": False,
        },
    },
)


def _battle_preview_schema(max_battle_preview_per_turn: int = 3) -> dict[str, Any]:
    limit_notice = (
        f"每回合最多 {max_battle_preview_per_turn} 次。"
        if max_battle_preview_per_turn
        else ""
    )
    return {
        "name": "preview_battle",
        "description": (
            "无副作用模拟一场已入队角色与当前怪物的1v1战斗，不适用于招募候选。"
            f"{limit_notice}"
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


_BATTLE_PREVIEW_SCHEMA: dict[str, Any] = _battle_preview_schema(3)

_TACTICS_SCHEMA: dict[str, Any] = {
    "name": "configure_tactics",
    "description": (
        "覆盖设置角色的主动技能释放顺序与停用列表（填当前拥有的主动技能字符串 ID，被动技能不受影响）。"
        "未列入的普通主动按默认顺序排在后面。自动回复只能停用，不能排序。两项均传 [] 恢复默认。"
    ),
    "parameters": {
        "type": "object",
        "required": ["adventurer_id", "skill_priority", "disabled_skills"],
        "properties": {
            "adventurer_id": _ADVENTURER_ID,
            "skill_priority": {
                "type": "array", "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
                "description": "按优先级从高到低排列的普通主动技能字符串 ID，不接受自动回复技能，不可与 disabled_skills 重叠。",
            },
            "disabled_skills": {
                "type": "array", "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
                "description": "停用的普通主动或自动回复技能字符串 ID 列表。",
            },
        },
        "additionalProperties": False,
    },
}


def team_power_preview_schema(schemas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from guild_manager_bench.bench.llm.preparation_plan import plan_operations_schema

    return {
        "name": "preview_team_power",
        "description": (
            "终局阶段只读试算准备方案（最多20步）对全队终局评分（rank_score）的影响，不改变真实状态。"
            "返回调用前真实分、方案执行后分数及差值；传 operations=[] 仅查询当前真实分。"
            "试算只占1次工具预算，若采纳方案仍需调用真实工具执行。"
        ),
        "parameters": {
            "type": "object", "required": ["operations"],
            "properties": {"operations": plan_operations_schema(schemas)},
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
