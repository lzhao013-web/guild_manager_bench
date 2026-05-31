import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from guild_manager_bench.bench.llm import (
    GuildManagerTools,
    LlmAgentResponse,
    LlmRunConfig,
    LlmToolCall,
    TurnToolHarness,
    build_system_prompt,
    build_turn_prompt,
    run_llm_game,
    run_llm_turn,
)
from guild_manager_bench.bench.llm.formatting import skill_summary
from guild_manager_bench.bench.llm.runner import _compute_run_stats
from guild_manager_bench.bench.llm.trace import (
    LlmGameRun,
    ModelResponseRecord,
    ToolCallRecord,
    TurnTrace,
)
from guild_manager_bench.game.state import LlmToolRules


class StaticAgent:
    def __init__(self, response: LlmAgentResponse) -> None:
        self.response = response

    def respond(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LlmAgentResponse:
        return self.response


class SequenceAgent:
    def __init__(self, responses: Sequence[LlmAgentResponse]) -> None:
        self.responses = list(responses)
        self.index = 0

    def respond(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LlmAgentResponse:
        if self.index >= len(self.responses):
            return self.responses[-1]
        response = self.responses[self.index]
        self.index += 1
        return response


class RecordingSequenceAgent(SequenceAgent):
    def __init__(self, responses: Sequence[LlmAgentResponse]) -> None:
        super().__init__(responses)
        self.prompts: list[str] = []

    def respond(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LlmAgentResponse:
        for message in messages:
            if message.get("role") == "user":
                self.prompts.append(str(message.get("content", "")))
                break
        return super().respond(messages=messages, tools=tools)


def test_run_llm_game_completes_when_agent_ends_each_turn() -> None:
    agent = StaticAgent(
        LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(max_tool_calls_per_turn=2, archive_dir=None),
    )

    assert run.status == "completed"
    assert run.final_observation["finished"] is True
    assert len(run.turns) == run.final_observation["max_turns"]
    assert all(turn.status == "completed" for turn in run.turns)


def test_illegal_action_error_is_returned_to_model_before_recovery() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("illegal-action")["session_id"]
    agent = SequenceAgent(
        (
            LlmAgentResponse(
                tool_calls=(LlmToolCall("craft_equipment", {"recipe_id": "missing"}),)
            ),
            LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),)),
        )
    )

    trace = run_llm_turn(agent, tools, session_id, config=LlmRunConfig())

    assert trace.status == "completed"
    assert trace.tool_calls[0].result["ok"] is False
    assert "未找到制作配方" in trace.tool_calls[0].result["error"]
    assert "未找到制作配方" in trace.messages[2]["content"]


def test_empty_responses_fail_after_retry_limit() -> None:
    agent = StaticAgent(LlmAgentResponse(text="I will think."))

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(max_empty_responses=2, archive_dir=None),
    )

    assert run.status == "failed"
    assert run.failure_reason == "empty_response_limit"


def test_empty_response_counter_resets_after_tool_call() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("empty-reset")["session_id"]
    agent = SequenceAgent(
        (
            LlmAgentResponse(text="thinking"),
            LlmAgentResponse(tool_calls=(LlmToolCall("get_party", {}),)),
            LlmAgentResponse(text="thinking again"),
            LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),)),
        )
    )

    trace = run_llm_turn(
        agent,
        tools,
        session_id,
        config=LlmRunConfig(max_empty_responses=2),
    )

    assert trace.status == "completed"


def test_invalid_end_turn_fails_after_attempt_limit() -> None:
    agent = StaticAgent(
        LlmAgentResponse(
            tool_calls=(
                LlmToolCall(
                    "end_turn",
                    {
                        "hunts": [
                            {
                                "adventurer_id": "vanguard",
                                "monster_id": "turn_1_monster_1",
                            },
                            {
                                "adventurer_id": "vanguard",
                                "monster_id": "turn_1_monster_2",
                            },
                        ]
                    },
                ),
            )
        )
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(max_end_turn_attempts=2, archive_dir=None),
    )

    assert run.status == "failed"
    assert run.failure_reason == "end_turn_attempt_limit"
    assert "重复派遣同一冒险者" in run.turns[0].tool_calls[-1].result["error"]


def test_budget_exhaustion_without_end_turn_retries() -> None:
    agent = StaticAgent(
        LlmAgentResponse(tool_calls=(LlmToolCall("get_party", {}),))
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(
            max_tool_calls_per_turn=1,
            max_model_steps_per_turn=5,
            archive_dir=None,
        ),
    )

    assert run.status == "failed"
    assert run.failure_reason == "model_step_limit"


def test_run_llm_turn_emits_debug_events() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("debug-events")["session_id"]
    events: list[dict[str, Any]] = []
    agent = SequenceAgent(
        (
            LlmAgentResponse(tool_calls=(LlmToolCall("get_party", {}),)),
            LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),)),
        )
    )

    trace = run_llm_turn(agent, tools, session_id, event_sink=events.append)

    assert trace.status == "completed"
    event_types = [event["type"] for event in events]
    assert "turn_started" in event_types
    assert "model_request" in event_types
    model_response = next(event for event in events if event["type"] == "model_response")
    assert model_response["assistant_metadata"] == {}
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "turn_completed" in event_types
    model_requests = [event for event in events if event["type"] == "model_request"]
    tool_message = model_requests[1]["request"]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["content"].startswith("OK get_party")
    assert not tool_message["content"].lstrip().startswith("{")
    assert "队伍: 回合 1/" in tool_message["content"]
    assert "冒险者: 无" in tool_message["content"]
    assert "EXP 0/" not in tool_message["content"]


def test_recruitment_tool_returns_compact_model_visible_text() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("recruitment-text")["session_id"]
    agent = SequenceAgent(
        (
            LlmAgentResponse(tool_calls=(LlmToolCall("get_recruitment", {}),)),
            LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),)),
        )
    )

    trace = run_llm_turn(agent, tools, session_id)

    assert trace.status == "completed"
    tool_message = next(
        message
        for message in trace.messages
        if message.get("role") == "tool" and message.get("name") == "get_recruitment"
    )
    assert tool_message["content"].startswith("OK get_recruitment")
    assert "招募: 回合 1/" in tool_message["content"]
    assert "队伍 0/3" in tool_message["content"]
    assert "招募候选:" in tool_message["content"]
    assert tool_message["content"].count("\n- ") == 6
    assert "费用" in tool_message["content"]
    assert "成长" in tool_message["content"]
    assert "预算: 已用 1/" in tool_message["content"]


def test_turn_prompt_includes_compact_skill_summaries() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    observation = tools.start_session("skill-prompt")["observation"]

    prompt = build_turn_prompt(observation)

    assert "上一回合：无，这是本局第一个回合。" in prompt
    assert "备忘录：无。" in prompt
    assert "本回合概览：" in prompt
    assert "可制作配方" in prompt
    assert "可购买升级" in prompt
    assert "空闲装备" in prompt
    assert "招募候选 6 个" in prompt
    assert "队伍 0/3" in prompt
    assert "当前怪物" in prompt
    assert "总潜在奖励" not in prompt
    assert "怪物最高攻击" not in prompt
    assert "- 1 先锋" not in prompt
    assert "EXP 0/" not in prompt
    assert "成长 HP+18 MP+1 攻击+2 防御+4 速度+1 恢复+2" not in prompt
    assert "  技能:\n    - 强力打击 主动" not in prompt
    assert "等级技能" not in prompt
    assert "壁垒集结" not in prompt
    assert "战舞者" not in prompt
    assert "效果 伤害倍率 1.8" not in prompt

    # Static rules should NOT be in turn prompt anymore
    assert "回合流程：" not in prompt
    assert "战斗机制：" not in prompt
    assert "回复机制：" not in prompt


def test_system_prompt_includes_static_rules() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    observation = tools.start_session("system-prompt")["observation"]
    recovery = observation.get("turn_recovery_rules", {})

    prompt = build_system_prompt(
        objective="测试目标。",
        max_tool_calls=7,
        max_battle_preview_per_turn=5,
        turn_recovery_rules=recovery,
    )

    assert "你正在进行 Guild Manager Bench。" in prompt
    assert "目标：测试目标。" in prompt
    assert "回合流程：" in prompt
    assert "战斗提示：" in prompt
    assert "战斗机制：SPD 决定出手频率" in prompt
    assert "普通攻击伤害 = max(1, ATK - DEF)" in prompt
    assert "技能相关：" in prompt
    assert "回复机制：" in prompt
    assert "HP回复 = " in prompt
    assert "MP回复 = " in prompt
    assert "刷新机制：" in prompt
    assert "每回合最多允许 7 次非 end_turn 工具调用" in prompt
    assert "preview_battle 最多 5 次/回合" in prompt
    assert "调用工具使用的所有对象 id 都使用列表左侧的数字 id" in prompt
    assert "工具会返回 OK/FAIL、budget 和结果摘要" in prompt

    # Dynamic content should NOT be in system prompt
    assert "当前回合" not in prompt
    assert "本回合概览" not in prompt
    assert "备忘录" not in prompt
    assert "上一回合" not in prompt


def test_turn_prompt_shows_equipped_numeric_refs() -> None:
    tools = GuildManagerTools.from_data_dir(_data_dir())
    session_id = tools.start_session("equipped-prompt")["session_id"]
    harness = TurnToolHarness(tools, session_id, max_tool_calls=4)

    recruited = harness.call_tool("recruit_adventurer", {"candidate_id": 1})
    assert recruited["ok"] is True
    assert harness.call_tool("craft_equipment", {"recipe_id": 1})["ok"] is True
    assert (
        harness.call_tool(
            "equip_item",
            {
                "adventurer_id": 1,
                "equipment_instance_id": 1,
            },
        )["ok"]
        is True
    )

    observation = tools.get_observation(session_id)["observation"]
    prompt = build_turn_prompt(observation)
    adventurer_name = observation["adventurers"][0]["name"]

    assert f"1 {adventurer_name}" in prompt
    assert "装备 铁剑(id=1, main_hand)" in prompt
    assert "装备 无" not in prompt.split(f"1 {adventurer_name}", 1)[1].splitlines()[0]


def test_skill_summary_formats_extended_conditions_and_effects() -> None:
    text = skill_summary(
        [
            {
                "name": "血焰",
                "kind": "active",
                "condition": {
                    "type": "all",
                    "conditions": [
                        {"type": "self_mp_pct_gte", "value": 0.5},
                        {"type": "action_index_lte", "value": 1},
                    ],
                },
                "effects": [
                    {"type": "heal_percent", "value": 0.25, "target": "self"},
                    {"type": "mp_restore", "value": 3, "target": "self"},
                    {"type": "damage_bonus", "value": 4, "target": "target"},
                    {"type": "true_damage", "value": 5, "target": "target"},
                    {"type": "self_damage", "value": 2, "target": "self"},
                    {
                        "type": "apply_status",
                        "value": 0,
                        "target": "target",
                        "status": {
                            "status_id": "burn",
                            "name": "灼伤",
                            "duration": 2,
                            "polarity": "negative",
                            "effects": [
                                {"type": "true_damage", "value": 3, "target": "target"}
                            ],
                        },
                    },
                ],
            }
        ]
    )

    assert "自身MP>=50%" in text
    assert "行动序号<=1" in text
    assert "治疗 25%最大HP" in text
    assert "自身恢复MP 3" in text
    assert "伤害+4" in text
    assert "真实伤害 5" in text
    assert "自身受伤 2" in text
    assert "施加状态 灼伤 2行动 负面 真实伤害 3" in text


def test_memo_tool_content_appears_in_next_turn_prompt() -> None:
    memo_text = "下回合优先把经验给先锋。"
    agent = RecordingSequenceAgent(
        (
            LlmAgentResponse(
                tool_calls=(LlmToolCall("write_memo", {"content": memo_text}),)
            ),
            LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),)),
        )
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(max_tool_calls_per_turn=2, archive_dir=None),
    )

    assert run.status == "completed"
    assert any(
        "当前回合：2/" in prompt and memo_text in prompt
        for prompt in agent.prompts
    )
    assert all(
        memo_text not in prompt
        for prompt in agent.prompts
        if "当前回合：1/" in prompt
    )
    assert run.turns[0].tool_calls[0].name == "write_memo"
    assert run.turns[0].messages[3]["content"].startswith("OK write_memo")


def test_preview_battle_tool_returns_compact_model_visible_text() -> None:
    base_tools = GuildManagerTools.from_data_dir(_data_dir())
    tools = GuildManagerTools(
        replace(
            base_tools.definition,
            llm_tools=LlmToolRules(expose_battle_preview=True),
        )
    )
    tools.start_session("preview-text")
    adventurer_id = _recruit_first(tools, "preview-text")
    observation = tools.get_observation("preview-text")["observation"]
    monster_id = observation["monsters"][0]["monster_id"]
    agent = SequenceAgent(
        (
            LlmAgentResponse(
                tool_calls=(
                    LlmToolCall(
                        "preview_battle",
                        {
                            "adventurer_id": adventurer_id,
                            "monster_id": monster_id,
                        },
                    ),
                )
            ),
            LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),)),
        )
    )

    trace = run_llm_turn(agent, tools, "preview-text")

    assert trace.status == "completed"
    tool_message = next(
        message
        for message in trace.messages
        if message.get("role") == "tool" and message.get("name") == "preview_battle"
    )
    assert tool_message["content"].startswith("OK preview_battle")
    assert "资源:" in tool_message["content"]
    assert "战斗:" in tool_message["content"]
    assert "预算: 已用 1/" in tool_message["content"]
    assert not tool_message["content"].lstrip().startswith("{")


def test_run_llm_game_archives_trace_and_replay(tmp_path) -> None:
    agent = StaticAgent(
        LlmAgentResponse(
            tool_calls=(LlmToolCall("end_turn", {"hunts": []}),),
            raw={"id": "response_1"},
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 5,
                "total_tokens": 17,
            },
        )
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(max_tool_calls_per_turn=2, archive_dir=tmp_path),
    )

    assert run.status == "completed"
    assert run.score is not None
    assert 0 <= run.score["score"] <= 100
    assert run.archive_dir is not None
    archive_dir = Path(run.archive_dir)
    trace_path = archive_dir / "trace.jsonl"
    replay_path = archive_dir / "replay.json"
    assert trace_path.exists()
    assert replay_path.exists()

    trace_lines = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    replay = json.loads(replay_path.read_text(encoding="utf-8"))

    assert trace_lines[0]["record_type"] == "archive_started"
    assert trace_lines[0]["agent"]["type"] == "StaticAgent"
    assert "secret" not in json.dumps(trace_lines, ensure_ascii=False)
    request_event = next(
        record["event"]
        for record in trace_lines
        if record.get("event", {}).get("type") == "model_request"
    )
    response_event = next(
        record["event"]
        for record in trace_lines
        if record.get("event", {}).get("type") == "model_response"
    )
    assert request_event["request"]["messages"][0]["role"] == "system"
    assert request_event["request"]["messages"][1]["role"] == "user"
    assert request_event["request"]["tools"]
    assert response_event["raw"] == {"id": "response_1"}
    assert response_event["usage"]["total_tokens"] == 17
    assert response_event["timing"]["duration_ms"] >= 0

    assert replay["kind"] == "llm_replay"
    assert replay["status"] == "completed"
    assert replay["score"]["score"] == run.score["score"]
    assert replay["score"]["mode"] == "endgame_arena"
    assert replay["data"]["data_hash"]
    assert replay["data"]["game_seed"] == 20260524
    assert replay["data"]["scoring_seed"] == 20260526
    assert replay["turns"][0]["steps"][0]["type"] == "system_prompt"
    assert replay["turns"][0]["steps"][1]["type"] == "turn_prompt"
    assert replay["turns"][0]["steps"][2]["type"] == "assistant"
    assert replay["turns"][0]["steps"][2]["usage"]["total_tokens"] == 17
    assert replay["turns"][0]["steps"][2]["timing"]["duration_ms"] >= 0
    assert replay["turns"][0]["steps"][3]["type"] == "tool_result"
    assert replay["turns"][0]["steps"][3]["name"] == "end_turn"
    assert replay["turns"][0]["steps"][3]["content"].startswith("OK end_turn")
    assert "result" not in replay["turns"][0]["steps"][3]
    assert run.turns[0].messages[-1]["content"] == replay["turns"][0]["steps"][3]["content"]


def test_run_llm_game_can_override_seeds_and_archives_effective_values(tmp_path) -> None:
    agent = StaticAgent(
        LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))
    )

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(
            max_tool_calls_per_turn=2,
            archive_dir=tmp_path,
            game_seed=11,
            scoring_seed=22,
        ),
    )

    archive_dir = Path(run.archive_dir)
    replay = _read_json(archive_dir / "replay.json")

    assert run.final_observation["seed"] == 11
    assert run.final_observation["scoring"]["seed"] == 22
    assert run.score["seed"] == 22
    assert replay["data"]["game_seed"] == 11
    assert replay["data"]["scoring_seed"] == 22
    assert replay["config"]["game_seed"] == 11
    assert replay["config"]["scoring_seed"] == 22

    with pytest.raises(ValueError, match="game_seed"):
        run_llm_game(
            agent,
            data_dir=_data_dir(),
            config=LlmRunConfig(archive_dir=tmp_path, game_seed=12, scoring_seed=22),
            resume_archive_dir=archive_dir,
        )


def test_run_llm_game_archives_stream_final_without_delta_chunks(tmp_path) -> None:
    class StreamingAgent:
        def respond_stream(self, *, messages, tools, event_sink):
            event_sink({"type": "model_reasoning_delta", "text": "thinking"})
            event_sink({"type": "model_delta", "text": "done"})
            event_sink(
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "call_id": "call_stream",
                    "name": "end_turn",
                    "arguments_delta": "{}",
                }
            )
            event_sink(
                {
                    "type": "model_stream_completed",
                    "text": "done",
                    "tool_calls": [
                        {
                            "id": "call_stream",
                            "name": "end_turn",
                            "arguments": {"hunts": []},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 3,
                        "total_tokens": 10,
                    },
                    "chunk_count": 3,
                }
            )
            return LlmAgentResponse(
                text="done",
                tool_calls=(LlmToolCall("end_turn", {"hunts": []}, call_id="call_stream"),),
                raw={"stream": True, "chunk_count": 3},
                usage={
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                },
            )

    run = run_llm_game(
        StreamingAgent(),
        data_dir=_data_dir(),
        config=LlmRunConfig(max_tool_calls_per_turn=2, archive_dir=tmp_path),
    )

    assert run.status == "completed"
    archive_dir = Path(run.archive_dir)
    trace_lines = [
        _read_json_line(line)
        for line in (archive_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_types = [
        record["event"]["type"]
        for record in trace_lines
        if record.get("event", {}).get("type")
    ]
    assert "model_delta" not in event_types
    assert "model_reasoning_delta" not in event_types
    assert "tool_call_delta" not in event_types
    assert "model_stream_completed" in event_types
    replay = _read_json(archive_dir / "replay.json")
    assert replay["turns"][0]["steps"][2]["usage"]["total_tokens"] == 10


def test_run_llm_game_archives_interrupted_run_incrementally(tmp_path) -> None:
    class ExplodingAgent:
        def respond(self, *, messages, tools):
            raise RuntimeError("model unavailable")

    with pytest.raises(RuntimeError, match="model unavailable"):
        run_llm_game(
            ExplodingAgent(),
            data_dir=_data_dir(),
            config=LlmRunConfig(archive_dir=tmp_path),
        )

    archive_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(archive_dirs) == 1
    replay = _read_json(archive_dirs[0] / "replay.json")
    trace_lines = [
        _read_json_line(line)
        for line in (archive_dirs[0] / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert replay["status"] == "interrupted"
    assert replay["failure_reason"] == "model unavailable"
    assert replay["turns"][0]["steps"][0]["type"] == "system_prompt"
    assert replay["turns"][0]["steps"][1]["type"] == "turn_prompt"
    assert any(
        record.get("event", {}).get("type") == "model_request"
        for record in trace_lines
    )
    assert trace_lines[-1]["record_type"] == "run_exception"


def test_run_llm_game_resumes_interrupted_archive_in_place(tmp_path) -> None:
    class EndOnceThenExplode:
        def __init__(self) -> None:
            self.calls = 0

        def respond(self, *, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))
            raise RuntimeError("connection dropped")

    with pytest.raises(RuntimeError, match="connection dropped"):
        run_llm_game(
            EndOnceThenExplode(),
            data_dir=_data_dir(),
            config=LlmRunConfig(archive_dir=tmp_path),
        )

    archive_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
    trace_path = archive_dir / "trace.jsonl"
    replay_path = archive_dir / "replay.json"
    before_trace_lines = trace_path.read_text(encoding="utf-8").splitlines()
    interrupted_replay = _read_json(replay_path)

    assert interrupted_replay["status"] == "interrupted"
    assert interrupted_replay["final_observation"]["turn"] == 2

    run = run_llm_game(
        StaticAgent(LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))),
        data_dir=_data_dir(),
        config=LlmRunConfig(archive_dir=tmp_path),
        resume_archive_dir=archive_dir,
    )

    after_trace_lines = [
        _read_json_line(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    replay = _read_json(replay_path)

    assert run.status == "completed"
    assert run.archive_dir == str(archive_dir)
    assert len(after_trace_lines) > len(before_trace_lines)
    assert any(record["record_type"] == "archive_resumed" for record in after_trace_lines)
    assert any(
        record.get("event", {}).get("type") == "run_resumed"
        for record in after_trace_lines
    )
    assert replay["status"] == "completed"
    assert replay["score"]["score"] == run.score["score"]
    assert replay["session_id"] == interrupted_replay["session_id"]
    assert replay["resume_history"]
    assert replay["final_observation"]["finished"] is True


def test_run_llm_game_refuses_resume_when_data_hash_changed(tmp_path) -> None:
    class ExplodingAgent:
        def respond(self, *, messages, tools):
            raise RuntimeError("model unavailable")

    with pytest.raises(RuntimeError, match="model unavailable"):
        run_llm_game(
            ExplodingAgent(),
            data_dir=_data_dir(),
            config=LlmRunConfig(archive_dir=tmp_path),
        )

    archive_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
    replay_path = archive_dir / "replay.json"
    replay = _read_json(replay_path)
    replay["data"]["data_hash"] = "0" * 64
    replay_path.write_text(json.dumps(replay), encoding="utf-8")

    with pytest.raises(ValueError, match="data_hash"):
        run_llm_game(
            StaticAgent(LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))),
            data_dir=_data_dir(),
            config=LlmRunConfig(archive_dir=tmp_path),
            resume_archive_dir=archive_dir,
        )


def test_run_summary_includes_comprehensive_stats_in_event() -> None:
    agent = StaticAgent(
        LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))
    )
    events: list[dict[str, Any]] = []

    run = run_llm_game(
        agent,
        data_dir=_data_dir(),
        config=LlmRunConfig(max_tool_calls_per_turn=2, archive_dir=None),
        event_sink=events.append,
    )

    assert run.status == "completed"
    completed_event = next(e for e in events if e["type"] == "run_completed")
    stats = completed_event["run"]["stats"]

    assert "timing" in stats
    assert "total_duration_ms" in stats["timing"]
    assert "total_duration_seconds" in stats["timing"]

    assert "tool_calls" in stats
    assert stats["tool_calls"]["total"] > 0
    assert stats["tool_calls"]["successful"] > 0
    assert stats["tool_calls"]["failed"] == 0
    assert "by_name" in stats["tool_calls"]

    assert "token_usage" in stats
    assert "input_tokens" in stats["token_usage"]
    assert "output_tokens" in stats["token_usage"]

    assert "game_actions" in stats
    assert stats["game_actions"]["battles_total"] == 0  # no hunts submitted

    assert "model_interaction" in stats
    assert stats["model_interaction"]["total_model_steps"] > 0
    assert stats["model_interaction"]["total_turns_completed"] == run.final_observation["max_turns"]
    assert stats["model_interaction"]["total_turns_failed"] == 0


def test_compute_run_stats_empty_run() -> None:
    run = LlmGameRun(
        status="completed",
        session_id="test-empty",
        final_observation={"finished": True},
        turns=[],
    )

    stats = _compute_run_stats(run)

    assert stats["timing"]["total_duration_ms"] == 0
    assert stats["timing"]["total_duration_seconds"] == 0.0
    assert stats["tool_calls"]["total"] == 0
    assert stats["tool_calls"]["successful"] == 0
    assert stats["tool_calls"]["failed"] == 0
    assert stats["tool_calls"]["by_name"] == {}
    assert stats["token_usage"]["input_tokens"] == 0
    assert stats["token_usage"]["output_tokens"] == 0
    assert "cache_read_input_tokens" not in stats["token_usage"]
    assert "cache_creation_input_tokens" not in stats["token_usage"]
    assert stats["game_actions"]["battles_total"] == 0
    assert stats["model_interaction"]["total_model_steps"] == 0
    assert stats["model_interaction"]["total_turns_completed"] == 0
    assert stats["model_interaction"]["total_turns_failed"] == 0


def test_compute_run_stats_timing_and_token_aggregation() -> None:
    turn = TurnTrace(turn=1, prompt="test")
    turn.model_responses.append(
        ModelResponseRecord(
            text="",
            timing={"duration_ms": 500.5},
            usage={"input_tokens": 100, "output_tokens": 50},
        )
    )
    turn.model_responses.append(
        ModelResponseRecord(
            text="",
            timing={"duration_ms": 300.0},
            usage={
                "prompt_tokens": 80,
                "completion_tokens": 40,
                "cache_read_input_tokens": 60,
                "cache_creation_input_tokens": 20,
            },
        )
    )
    turn.complete()

    run = LlmGameRun(
        status="completed",
        session_id="test-timing",
        final_observation={"finished": True},
        turns=[turn],
    )

    stats = _compute_run_stats(run)

    assert stats["timing"]["total_duration_ms"] == round(500.5 + 300.0)
    assert stats["timing"]["total_duration_seconds"] == round(800.5 / 1000, 3)
    # input_tokens from first (100) + prompt_tokens from second (80) = 180
    assert stats["token_usage"]["input_tokens"] == 180
    # output_tokens from first (50) + completion_tokens from second (40) = 90
    assert stats["token_usage"]["output_tokens"] == 90
    assert stats["token_usage"]["cache_read_input_tokens"] == 60
    assert stats["token_usage"]["cache_creation_input_tokens"] == 20


def test_compute_run_stats_tool_call_success_and_failure() -> None:
    turn = TurnTrace(turn=1, prompt="test")
    turn.tool_calls.append(
        ToolCallRecord(
            name="craft_equipment",
            arguments={"recipe_id": 1},
            result={"ok": True, "event": {}},
        )
    )
    turn.tool_calls.append(
        ToolCallRecord(
            name="craft_equipment",
            arguments={"recipe_id": 999},
            result={"ok": False, "error": "not found"},
        )
    )
    turn.tool_calls.append(
        ToolCallRecord(
            name="end_turn",
            arguments={"hunts": []},
            result={"ok": True, "turn_result": {"battles": []}},
        )
    )
    turn.complete()

    run = LlmGameRun(
        status="completed",
        session_id="test-tools",
        final_observation={"finished": True},
        turns=[turn],
    )

    stats = _compute_run_stats(run)

    assert stats["tool_calls"]["total"] == 3
    assert stats["tool_calls"]["successful"] == 2
    assert stats["tool_calls"]["failed"] == 1
    assert stats["tool_calls"]["by_name"]["craft_equipment"] == 2
    assert stats["tool_calls"]["by_name"]["end_turn"] == 1
    # Game actions: only successful craft_equipment counted
    assert stats["game_actions"]["total_equipment_crafted"] == 1


def test_compute_run_stats_battle_results() -> None:
    turn = TurnTrace(turn=1, prompt="test")
    turn.tool_calls.append(
        ToolCallRecord(
            name="end_turn",
            arguments={"hunts": []},
            result={
                "ok": True,
                "turn_result": {
                    "battles": [
                        {
                            "won": True,
                            "reward": {"gold": 50, "experience": 30, "materials": {}},
                        },
                        {
                            "won": False,
                            "reward": {"gold": 0, "experience": 0, "materials": {}},
                        },
                        {
                            "won": True,
                            "reward": {"gold": 25, "experience": 15, "materials": {}},
                        },
                    ],
                },
            },
        )
    )
    turn.complete()

    run = LlmGameRun(
        status="completed",
        session_id="test-battles",
        final_observation={"finished": True},
        turns=[turn],
    )

    stats = _compute_run_stats(run)

    ga = stats["game_actions"]
    assert ga["battles_total"] == 3
    assert ga["battles_won"] == 2
    assert ga["battles_lost"] == 1
    assert ga["total_gold_earned"] == 75
    assert ga["total_experience_earned"] == 45


def test_compute_run_stats_mixed_game_actions() -> None:
    turn = TurnTrace(turn=1, prompt="test")
    for name, ok in [
        ("craft_equipment", True),
        ("craft_equipment", True),
        ("purchase_upgrade", True),
        ("allocate_experience", True),
        ("allocate_experience", True),
        ("allocate_experience", True),
        ("recruit_adventurer", True),
        ("dismiss_adventurer", True),
        ("equip_item", True),
        ("equip_item", True),
        ("unequip_item", True),
        ("get_party", True),  # read-only, not a game action
        ("end_turn", True),
    ]:
        result: dict[str, Any] = {"ok": ok}
        if name == "end_turn":
            result["turn_result"] = {"battles": []}
        turn.tool_calls.append(
            ToolCallRecord(name=name, arguments={}, result=result)
        )
    turn.complete()

    run = LlmGameRun(
        status="completed",
        session_id="test-actions",
        final_observation={"finished": True},
        turns=[turn],
    )

    stats = _compute_run_stats(run)

    ga = stats["game_actions"]
    assert ga["total_equipment_crafted"] == 2
    assert ga["total_upgrades_purchased"] == 1
    assert ga["total_experience_allocated"] == 3
    assert ga["total_recruits"] == 1
    assert ga["total_dismissals"] == 1
    assert ga["total_equips"] == 2
    assert ga["total_unequips"] == 1
    assert stats["tool_calls"]["total"] == 13
    assert stats["tool_calls"]["successful"] == 13
    assert stats["tool_calls"]["by_name"]["get_party"] == 1


def test_compute_run_stats_turn_status_tracking() -> None:
    completed_turn = TurnTrace(turn=1, prompt="test")
    completed_turn.complete()

    failed_turn = TurnTrace(turn=2, prompt="test")
    failed_turn.fail("empty_response_limit")

    run = LlmGameRun(
        status="failed",
        session_id="test-status",
        final_observation={"finished": False},
        turns=[completed_turn, failed_turn],
        failure_reason="empty_response_limit",
    )

    stats = _compute_run_stats(run)

    assert stats["model_interaction"]["total_turns_completed"] == 1
    assert stats["model_interaction"]["total_turns_failed"] == 1


def test_compute_run_stats_cache_tokens_absent_when_zero() -> None:
    turn = TurnTrace(turn=1, prompt="test")
    turn.model_responses.append(
        ModelResponseRecord(
            text="",
            usage={"input_tokens": 100, "output_tokens": 50},
        )
    )
    turn.complete()

    run = LlmGameRun(
        status="completed",
        session_id="test-cache",
        final_observation={"finished": True},
        turns=[turn],
    )

    stats = _compute_run_stats(run)

    assert "cache_read_input_tokens" not in stats["token_usage"]
    assert "cache_creation_input_tokens" not in stats["token_usage"]


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "presets" / "default"


def _recruit_first(tools: GuildManagerTools, session_id: str) -> str:
    recruitment = tools.get_recruitment(session_id)
    candidate_id = recruitment["recruit_candidates"][0]["candidate_id"]
    result = tools.recruit_adventurer(session_id, candidate_id)
    assert result["ok"] is True
    observation = tools.get_observation(session_id)["observation"]
    return observation["adventurers"][0]["adventurer_id"]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_line(line: str) -> dict[str, Any]:
    return json.loads(line)
