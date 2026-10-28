import json
from pathlib import Path

import pytest

from guild_manager_bench.bench.llm import GuildManagerTools, LlmAgentResponse, LlmRunConfig, LlmToolCall
from guild_manager_bench.bench.llm import runner
from guild_manager_bench.bench.llm.archive import build_llm_run_replay
from guild_manager_bench.bench.llm.trace import TurnTrace
from guild_manager_bench.api.workbench_watch import WatchJournal
from test_llm_runner import RecordingSequenceAgent, _data_dir
from test_workbench_watch import append


def test_turn_scoring_records_contributions_without_extra_scoring_or_model_input(tmp_path, monkeypatch):
    tools = GuildManagerTools.from_data_dir(_data_dir())
    initial = tools.start_session()["observation"]
    candidate = next(item for item in initial["recruit_candidates"] if item["can_recruit"])
    agent = RecordingSequenceAgent([
        LlmAgentResponse(tool_calls=(LlmToolCall("recruit_adventurer", {"candidate_id": candidate["candidate_id"]}),
                                     LlmToolCall("end_turn", {"hunts": []}))),
        LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),)),
    ])
    evaluations = []
    original = runner.compute_rank_score_breakdown

    def score(*args, **kwargs):
        result = original(*args, **kwargs)
        evaluations.append(result)
        return result

    monkeypatch.setattr(runner, "compute_rank_score_breakdown", score)
    events = []
    run = runner.run_llm_game(agent, data_dir=_data_dir(), config=LlmRunConfig(archive_dir=tmp_path), event_sink=events.append)
    assert run.status == "completed"
    assert len(evaluations) == len(run.turns)
    scored = [event for event in events if event["type"] == "turn_scored"]
    assert len(scored) == len(run.turns)
    for trace, event, result in zip(run.turns, scored, evaluations, strict=True):
        assert trace.adventurer_rank_scores == result.per_adventurer_score
        assert event["adventurer_rank_scores"] == trace.adventurer_rank_scores
        assert sum(trace.adventurer_rank_scores.values()) == pytest.approx(trace.rank_score, abs=.001)
        assert set(trace.adventurer_rank_scores) == {member["adventurer_id"] for member in run.final_observation["adventurers"]}
    replay = json.loads((Path(run.archive_dir) / "replay.json").read_text(encoding="utf-8"))
    restored = runner._traces_from_replay(replay)
    assert [trace.adventurer_rank_scores for trace in restored] == [trace.adventurer_rank_scores for trace in run.turns]
    assert all("adventurer_rank_scores" not in prompt for prompt in agent.prompts)
    assert all("adventurer_rank_scores" not in json.dumps(trace.observation_before) for trace in run.turns)


def test_resume_retains_contributions_for_the_same_adventurer_ids(tmp_path):
    from guild_manager_bench.bench.llm.recovery import inspect_recovery

    tools = GuildManagerTools.from_data_dir(_data_dir())
    initial = tools.start_session()["observation"]
    candidate = next(item for item in initial["recruit_candidates"] if item["can_recruit"])

    class StopAfterRecruit(RecordingSequenceAgent):
        def respond(self, **kwargs):
            if self.index:
                raise RuntimeError("fixture pause")
            return super().respond(**kwargs)

    agent = StopAfterRecruit([LlmAgentResponse(tool_calls=(
        LlmToolCall("recruit_adventurer", {"candidate_id": candidate["candidate_id"]}),
        LlmToolCall("end_turn", {"hunts": []}),
    ))])
    with pytest.raises(RuntimeError, match="fixture pause"):
        runner.run_llm_game(agent, data_dir=_data_dir(), config=LlmRunConfig(archive_dir=tmp_path))
    source = next(tmp_path.iterdir())
    prefix = json.loads((source / "replay.json").read_text(encoding="utf-8"))["turns"][0]
    plan = inspect_recovery(source, _data_dir())
    plan.authorize(None)
    events = []
    resumed = runner.run_llm_game(
        RecordingSequenceAgent([LlmAgentResponse(tool_calls=(LlmToolCall("end_turn", {"hunts": []}),))]),
        data_dir=_data_dir(), config=LlmRunConfig(archive_dir=tmp_path), resume_archive_dir=source,
        recovery=plan, event_sink=events.append,
    )
    event = next(event for event in events if event["type"] == "run_resumed")
    assert event["last_adventurer_rank_scores"] == {"turn": 1, "scores": prefix["adventurer_rank_scores"]}
    assert set(prefix["adventurer_rank_scores"]) == {member["adventurer_id"] for member in resumed.final_observation["adventurers"]}


def test_new_and_legacy_turn_contributions_round_trip():
    traces = [TurnTrace(turn=1, prompt="", rank_score=0, adventurer_rank_scores={"hero": 0}),
              TurnTrace(turn=2, prompt="", rank_score=1)]
    for trace in traces:
        trace.complete()
    replay = build_llm_run_replay(status="running", session_id="fixture", turns=traces,
                                 created_at="fixture", updated_at="fixture", config={}, agent={})
    assert traces[0].to_dict()["adventurer_rank_scores"] == {"hero": 0}
    assert "adventurer_rank_scores" not in traces[1].to_dict()
    assert "adventurer_rank_scores" not in replay["turns"][1]
    restored = runner._traces_from_replay(replay)
    assert restored[0].adventurer_rank_scores == {"hero": 0}
    assert restored[1].adventurer_rank_scores is None


def test_watch_keeps_score_snapshot_by_turn_without_rescoring_or_writing(tmp_path):
    path = tmp_path / "events.jsonl"
    append(path, {"type": "turn_started", "turn": 1, "observation": {"turn": 1}},
           {"type": "turn_scored", "turn": 1, "rank_score": 10, "adventurer_rank_scores": {"hero": 10, "other": 0}},
           {"type": "turn_started", "turn": 2, "observation": {"turn": 2}})
    original = path.read_bytes()
    journal = WatchJournal(path)
    current = journal.read()
    assert current["attempts"][0]["rank_contributions"] == {"turn": 1, "scores": {"hero": 10, "other": 0}}
    assert current["attempts"][1]["rank_contributions"] is None
    first = journal.read(current["attempts"][0]["id"])
    assert first["records"][1]["event"]["adventurer_rank_scores"] == {"hero": 10, "other": 0}
    append(path, {"type": "turn_scored", "turn": 2, "rank_score": 20, "adventurer_rank_scores": {"hero": 20}})
    updated = journal.read()
    assert updated["attempts"][0]["rank_contributions"]["scores"]["hero"] == 10
    assert updated["attempts"][1]["rank_contributions"] == {"turn": 2, "scores": {"hero": 20}}
    assert path.read_bytes().startswith(original)


def test_watch_resume_uses_recorded_checkpoint_contributions_and_legacy_missing_stays_unknown(tmp_path):
    path = tmp_path / "events.jsonl"
    snapshot = {"turn": 7, "scores": {"hero": 70}}
    append(path, {"type": "run_resumed", "restored_observation": {"turn": 8}, "last_rank_score": 70,
                  "last_adventurer_rank_scores": snapshot},
           {"type": "turn_started", "turn": 8, "observation": {"turn": 8}},
           {"type": "turn_scored", "turn": 8, "rank_score": 80})
    result = WatchJournal(path).read()
    assert result["attempts"][0]["rank_contributions"] == snapshot
    assert result["attempts"][1]["rank_contributions"] is None


def test_archived_trace_exposes_the_same_recorded_contributions(tmp_path):
    path = tmp_path / "trace.jsonl"
    events = [{"type": "turn_started", "turn": 1, "observation": {"turn": 1}},
              {"type": "turn_scored", "turn": 1, "rank_score": 10, "adventurer_rank_scores": {"hero": 10}}]
    path.write_text("".join(json.dumps({"record_type": "event", "created_at": "fixture", "event": event}) + "\n" for event in events), encoding="utf-8")
    original = path.read_bytes()
    result = WatchJournal(path, archived=True).read()
    assert result["attempts"][0]["rank_contributions"] == {"turn": 1, "scores": {"hero": 10}}
    assert path.read_bytes() == original
