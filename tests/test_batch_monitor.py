from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import time

import pytest
from rich.console import Console

from guild_manager_bench.batch_monitor import DetailMonitor, TraceDetails, dashboard
from guild_manager_bench.bench.llm.tools import GuildManagerTools

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import run_gpt61_advanced as batch_script


@pytest.fixture
def observation():
    root = Path(__file__).resolve().parents[1]
    tools = GuildManagerTools.from_data_dir(root / "data/presets/default")
    started = tools.start_session("monitor")
    candidate = started["observation"]["recruit_candidates"][0]["candidate_id"]
    assert tools.recruit_adventurer("monitor", candidate)["ok"]
    return tools.get_observation("monitor")["observation"]


def record(kind, **kwargs):
    return {"record_type": "event", "created_at": "20261016-151020-123456",
            "event": {"type": kind, **kwargs}}


def tool_event(name, *, turn=1, result=None, **kwargs):
    value = {"ok": True, "tool_budget": {"used": 2, "max_tool_calls": 20}}
    value.update(result or {})
    return record("tool_result", turn=turn, name=name, result=value, content="成功 " + name, **kwargs)


def append(path, *records):
    with path.open("a", encoding="utf-8") as stream:
        for item in records:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")


def row(**kwargs):
    return {
        "effort": "high", "status": "running", "phase": "正在等待模型响应",
        "current_turn": 1, "completed_turns": 0, "total_turns": 35,
        "requests": 2, "input_tokens": 1000, "output_tokens": 200,
        "rank": None, "budget_used": 2, "budget_limit": 20,
        "updated_at": time.time() - 1, "started_at": time.time() - 60,
        **kwargs,
    }


def render(rows, states, *, width=160, effort=None):
    out = io.StringIO()
    Console(file=out, width=width, force_terminal=False).print(dashboard(Path("batch"), rows, states, effort=effort))
    return out.getvalue()


def test_incremental_reader_waits_for_partial_utf8_line_and_never_recounts(tmp_path, observation):
    path = tmp_path / "trace.jsonl"
    append(path, record("turn_started", observation=observation), tool_event("get_crafting"))
    details = TraceDetails(path)
    details.poll(batch_script.open_shared_read)
    assert details.tool_counts == {"get_crafting": 1}
    first_offset = details.offset
    partial = (json.dumps(tool_event("recruit_adventurer", result={
        "event": {"summary": "招募火药炮手"}, "_observation_after": observation,
    }), ensure_ascii=False) + "\n").encode()
    split = partial.index("炮".encode()) + 1
    with path.open("ab") as stream:
        stream.write(partial[:split])
    details.poll(batch_script.open_shared_read)
    assert details.offset == first_offset
    assert not details.caught_up and details.warning is None
    with path.open("ab") as stream:
        stream.write(partial[split:])
    details.poll(batch_script.open_shared_read)
    assert details.caught_up and details.tool_counts["recruit_adventurer"] == 1
    assert details.recent[-1]["summary"] == "招募火药炮手"
    details.poll(batch_script.open_shared_read)
    assert details.tool_counts["recruit_adventurer"] == 1
    assert details.offset == path.stat().st_size


def test_details_aggregate_budgets_failures_scores_and_real_battles_without_model_text(tmp_path, observation):
    path = tmp_path / "trace.jsonl"
    battle = {"adventurer_name": "神官", "monster_name": "哥布林", "won": True,
              "reward": {"gold": 12, "experience": 30, "materials": {}}}
    append(path,
           record("turn_started", observation=observation),
           record("model_request", turn=1, step=1, request={"messages": "private-prompt"}),
           record("model_response", timing={"duration_ms": 1200}, text="private-answer",
                  assistant_metadata={"reasoning_content": "private-thought"}, raw={"secret": "private-key"}),
           tool_event("get_forecast", result={"kind": "recruits", "start_turn": 3, "end_turn": 9,
                                             "total_matches": 4, "items": [{"name": "炮手"}]}),
           tool_event("configure_tactics", result={"ok": False, "error": "技能不属于该角色"}),
           record("retry", turn=1, reason="empty_response"),
           tool_event("end_turn", result={"turn_result": {"battles": [battle]}}),
           record("turn_scored", turn=1, rank_score=100),
           record("turn_started", observation={**observation, "turn": 2}),
           record("model_request", turn=2, step=1),
           tool_event("preview_team_power", turn=2, result={
               "current_rank_score": 100, "projected_rank_score": 130, "rank_score_delta": 30,
           }),
           record("turn_scored", turn=2, rank_score=125))
    before = path.read_bytes()
    details = TraceDetails(path)
    details.poll(batch_script.open_shared_read)
    assert details.turn_requests == 1 and details.turn_tools == 1 and details.turn_failures == 0
    assert details.tool_counts["get_forecast"] == 1 and details.failed_tools["configure_tactics"] == 1
    assert details.retries == {"empty_response": 1}
    assert details.battle_turn == 1 and details.last_battles == [battle]
    assert details.last_response_ms == 1200
    assert list(details.scores) == [(1, 100), (2, 125)]
    text = render([row(current_turn=2, completed_turns=2, rank=125)], {"high": details}, effort="high")
    for expected in ("未来查询 1", "技能配置 1", "方案试算 1", "+25.0", "未执行", "技能不属于该角色",
                     "1胜/0未胜", "金币+12", "经验+30", "装备：", "HP", "MP", "材料", "1.2s"):
        assert expected in text
    assert all(secret not in text + repr(details) for secret in (
        "private-prompt", "private-answer", "private-thought", "private-key"))
    assert path.read_bytes() == before


def test_read_errors_keep_last_known_details_and_recover(tmp_path, observation):
    path = tmp_path / "trace.jsonl"
    append(path, record("turn_started", observation=observation), tool_event("get_inventory"))
    details = TraceDetails(path)
    details.poll(batch_script.open_shared_read)
    offset = details.offset
    def denied(path):
        raise PermissionError("sharing conflict")
    details.poll(denied)
    assert details.offset == offset and details.tool_counts["get_inventory"] == 1
    assert "PermissionError" in details.warning
    assert not details.caught_up
    details.poll(batch_script.open_shared_read)
    assert details.warning is None and details.caught_up


def test_complete_corrupt_line_is_reported_without_discarding_or_double_counting(tmp_path):
    path = tmp_path / "trace.jsonl"
    append(path, tool_event("get_inventory"))
    with path.open("ab") as stream:
        stream.write(b'{broken json}\n')
    details = TraceDetails(path)
    details.poll(batch_script.open_shared_read)
    offset = details.offset
    assert "JSONDecodeError" in details.warning
    details.poll(batch_script.open_shared_read)
    assert details.offset == offset and details.tool_counts["get_inventory"] == 1


def test_read_budget_marks_partial_statistics_and_new_archive_does_not_reuse_old_counts(tmp_path):
    path = tmp_path / "first" / "trace.jsonl"
    path.parent.mkdir()
    append(path, tool_event("get_inventory"), tool_event("get_inventory"))
    details = TraceDetails(path)
    details.poll(batch_script.open_shared_read, max_bytes=1)
    assert not details.caught_up and details.tool_counts["get_inventory"] == 1
    assert "以下为已读部分" in render([row()], {"high": details}, effort="high")
    monitor = DetailMonitor(batch_script.open_shared_read)
    monitor.states["high"] = details
    monitor.refresh([row(archive_dir=str(path.parent))], catch_up=True)
    assert details.caught_up and details.tool_counts["get_inventory"] == 2
    second = tmp_path / "second" / "trace.jsonl"
    second.parent.mkdir()
    append(second, tool_event("get_crafting"))
    monitor.refresh([row(archive_dir=str(second.parent))])
    assert monitor.states["high"].tool_counts == {"get_crafting": 1}


def test_no_archive_and_missing_trace_display_unknown_not_fake_statistics(tmp_path):
    monitor = DetailMonitor(batch_script.open_shared_read)
    rows = [row(archive_dir=None)]
    monitor.refresh(rows)
    assert monitor.states == {}
    text = render(rows, monitor.states)
    assert "等待日志" in text and "未来查询 0" not in text
    monitor.refresh([row(archive_dir=str(tmp_path))])
    details = monitor.states["high"]
    assert "FileNotFoundError" in details.warning
    assert "未来查询 0" not in render(rows, monitor.states)


def test_dashboard_focus_preserves_overview_and_sanitizes_untrusted_labels(tmp_path, observation):
    details = TraceDetails(tmp_path / "trace.jsonl")
    observation["adventurers"][0]["name"] = "[bold]名字\x1b[2J\x07"
    details.consume(record("turn_started", observation=observation))
    rows = [row(effort=effort) for effort in ("medium", "high", "xhigh")]
    text = render(rows, {"high": details}, width=80, effort="high")
    assert all(effort in text for effort in ("medium", "high", "xhigh"))
    assert "[bold]名字" in text and "\x1b" not in text and "\x07" not in text
    assert text.count("装备：") == len(observation["adventurers"])
    unknown = render([row(status="unknown", completed_turns=None, current_turn=None,
                          requests=None, input_tokens=None, output_tokens=None, budget_used=None,
                          monitor_warning="保留上次数据")], {})
    assert "状态待确认" in unknown and "保留上次数据" in unknown
    assert "—/35" in unknown


def test_stale_score_does_not_display_delta_for_a_different_turn(tmp_path):
    details = TraceDetails(tmp_path / "trace.jsonl")
    details.scores.extend([(1, 100), (2, 120)])
    text = render([row(completed_turns=3, rank=200)], {"high": details})
    assert "+20.0" not in text


def test_monitor_reader_does_not_block_appending_or_replacing_trace(tmp_path):
    path = tmp_path / "trace.jsonl"
    append(path, tool_event("get_crafting"))
    with batch_script.open_shared_read(path) as reader:
        append(path, tool_event("get_inventory"))
        replacement = tmp_path / "replacement.tmp"
        replacement.write_text("new", encoding="utf-8")
        batch_script.replace_json_file(replacement, path)
        assert "get_crafting" in reader.read()
    assert path.read_text(encoding="utf-8") == "new"


def test_watch_cli_passes_focus_and_compact_options(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(batch_script, "watch", lambda batch, **kwargs: calls.append((batch, kwargs)) or 0)
    for option in ([], ["--effort", "medium"], ["--compact"]):
        monkeypatch.setattr(sys, "argv", ["run_gpt61_advanced.py", "watch", str(tmp_path), "--once", *option])
        assert batch_script.main() == 0
    assert calls[0][1] == {"once": True, "effort": None, "compact": False}
    assert calls[1][1]["effort"] == "medium"
    assert calls[2][1]["compact"] is True
    monkeypatch.setattr(sys, "argv", ["run_gpt61_advanced.py", "watch", str(tmp_path), "--effort", "high", "--compact"])
    with pytest.raises(SystemExit) as caught:
        batch_script.main()
    assert caught.value.code == 2


def test_detailed_watch_once_reads_existing_files_without_writing_them(tmp_path, observation, capsys):
    batch_script.write_json(tmp_path / "batch.json", {"efforts": ["high"], "max_tool_calls_per_turn": 20})
    job = tmp_path / "high"
    archive = job / "archive"
    archive.mkdir(parents=True)
    append(archive / "trace.jsonl", record("turn_started", observation=observation), tool_event("get_inventory"))
    batch_script.write_json(job / "progress.json", row(archive_dir=str(archive)))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert batch_script.watch(tmp_path, once=True, effort="high") == 0
    text = capsys.readouterr().out
    assert "最近操作" in text and "get_inventory" in text and "装备：" in text
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
