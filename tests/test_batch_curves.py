from copy import deepcopy
import io
import json
from pathlib import Path
import sys
from xml.etree import ElementTree as ET

import pytest
from rich.console import Console

from guild_manager_bench.batch_curves import (
    CurveMonitor, extract_curves, render_svg, segments, terminal_curves,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import run_gpt61_advanced as batch_script


def completed_turn(number, *, score=100, gold=50, xp=60, rewards=((10, 20),)):
    battles = [{"reward": {"gold": g, "experience": e}} for g, e in rewards]
    return {
        "turn": number, "status": "completed", "rank_score": score,
        "observation_before": {"gold": 9999, "experience_pool": 9999},
        "steps": [{"type": "tool_result", "name": "end_turn", "content": "成功 end_turn",
                   "observation_after": {"gold": gold, "experience_pool": xp, "turn": number + 1},
                   "result": {"ok": True, "turn_result": {"battles": battles}}}],
    }


def replay(*turns):
    return {"kind": "llm_replay", "status": "running", "turns": list(turns),
            "final_observation": {"max_turns": 35, "gold": 8888, "experience_pool": 8888}}


def row(archive, effort="high", **kwargs):
    return {"effort": effort, "archive_dir": str(archive), "status": "running", **kwargs}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    batch_script.write_json(path, data)


def text_chart(rows, monitor, **kwargs):
    out = io.StringIO()
    Console(file=out, width=120).print(terminal_curves(Path("batch"), rows, monitor,
                                                   width=120, resource_mode="balance", **kwargs))
    return out.getvalue()


def test_curves_use_completed_settlements_not_partial_actions_or_final_observation():
    failed = {"turn": 2, "status": "failed", "rank_score": 999999, "steps": []}
    partial = {"turn": 3, "status": "running", "rank_score": 999999, "steps": []}
    data = replay(completed_turn(1, rewards=((10, 20), (5, 8))), failed,
                  completed_turn(2, score=200, gold=5, xp=7, rewards=()), partial)
    before = deepcopy(data)
    result = extract_curves(data, "high")
    assert [p.turn for p in result.points] == [1, 2]
    first, second = result.points
    assert (first.rank_score, first.gold, first.experience_pool) == (100, 50, 60)
    assert (first.gold_income, first.experience_income) == (15, 28)
    assert (second.rank_score, second.gold, second.experience_pool) == (200, 5, 7)
    assert (second.gold_income, second.experience_income) == (15, 28)
    assert data == before


def test_missing_old_snapshots_rewards_and_scores_stay_unknown():
    first = completed_turn(1)
    del first["rank_score"]
    del first["steps"][0]["observation_after"]
    del first["steps"][0]["result"]
    second = completed_turn(2)
    del second["observation_before"]
    run = extract_curves(replay(first, second), "medium")
    assert run.points[0].rank_score is None and run.points[0].gold is None
    assert run.points[0].experience_pool is None
    assert all(p.gold_income is None and p.experience_income is None for p in run.points)
    assert run.points[1].gold == 50
    assert segments(run, "rank_score") == [[(2, 100)]]


def test_resumed_history_uses_next_opening_snapshot_but_never_mid_turn_final_state():
    first = completed_turn(1, gold=80, xp=163)
    del first["steps"][0]["observation_after"]
    second = completed_turn(2)
    second["observation_before"] = {"turn": 2, "gold": 80, "experience_pool": 163}
    del second["steps"][0]["observation_after"]
    data = replay(first, second)
    run = extract_curves(data, "xhigh")
    assert run.points[0].gold == 80 and run.points[0].experience_pool == 163
    # The latest checkpoint can already include the next turn's purchases. It is not a settlement snapshot.
    assert run.points[1].gold is None and run.points[1].experience_pool is None
    assert data["final_observation"]["gold"] == 8888
    # An explicitly recorded next-turn opening is safe even if that turn has not completed yet.
    data["turns"].append({"turn": 3, "status": "running", "steps": [],
                          "observation_before": {"turn": 3, "gold": 125, "experience_pool": 230}})
    run = extract_curves(data, "xhigh")
    assert run.points[1].gold == 125 and run.points[1].experience_pool == 230
    assert len(run.points) == 2


def test_gaps_break_lines_and_make_cumulative_income_unknown():
    second = completed_turn(2, score=None)
    run = extract_curves(replay(completed_turn(1), second, completed_turn(3), completed_turn(5)), "xhigh")
    assert segments(run, "rank_score") == [[(1, 100)], [(3, 100)], [(5, 100)]]
    assert segments(run, "gold") == [[(1, 50), (2, 50), (3, 50)], [(5, 50)]]
    assert run.points[-1].gold_income is None and run.points[-1].experience_income is None


@pytest.mark.parametrize("score", [None, True, float("nan"), float("inf"), "100"])
def test_non_numeric_scores_are_not_drawn(score):
    run = extract_curves(replay(completed_turn(1, score=score)), "high")
    assert run.points[0].rank_score is None
    assert segments(run, "rank_score") == []


@pytest.mark.parametrize("data", [
    {"kind": "manual_replay"},
    replay(completed_turn(2), completed_turn(1)),
    replay(completed_turn(1), completed_turn(1)),
])
def test_unsupported_or_ambiguous_histories_are_rejected(data):
    with pytest.raises(ValueError):
        extract_curves(data, "high")


def test_failed_end_turn_attempt_does_not_replace_successful_settlement():
    turn = completed_turn(1)
    failed = deepcopy(turn["steps"][0])
    failed["result"]["ok"] = False
    failed["content"] = "失败 end_turn"
    failed["observation_after"]["gold"] = 999
    turn["steps"].insert(0, failed)
    assert extract_curves(replay(turn), "high").points[0].gold == 50
    turn["steps"].append(deepcopy(turn["steps"][1]))
    with pytest.raises(ValueError, match="多条成功结算"):
        extract_curves(replay(turn), "high")


def test_monitor_caches_unchanged_replay_and_uses_resumed_authoritative_history(tmp_path):
    path = tmp_path / "replay.json"
    save(path, replay(completed_turn(1), {"turn": 2, "status": "running", "steps": []}))
    reads = []
    def read(path):
        reads.append(path)
        return batch_script.read_monitor_json(path)
    monitor = CurveMonitor(read)
    monitor.refresh([row(tmp_path)])
    monitor.refresh([row(tmp_path)])
    assert len(reads) == 1
    assert len(monitor.runs["high"].points) == 1
    # The resumed replay omits discarded actions and only includes the new settlement.
    save(path, replay(completed_turn(1), completed_turn(2, rewards=((20, 40),))))
    monitor.refresh([row(tmp_path)])
    assert len(reads) == 2
    assert monitor.runs["high"].points[-1].gold_income == 30
    assert monitor.runs["high"].points[-1].experience_income == 60


def test_transient_read_failure_preserves_last_chart_and_then_recovers(tmp_path):
    path = tmp_path / "replay.json"
    save(path, replay(completed_turn(1)))
    monitor = CurveMonitor(batch_script.read_monitor_json)
    monitor.refresh([row(tmp_path)])
    expected = monitor.runs["high"]
    path.write_text('{"incomplete":', encoding="utf-8")
    monitor.refresh([row(tmp_path)])
    assert monitor.runs["high"] is expected
    assert "保留上次曲线" in monitor.warnings["high"]
    save(path, replay(completed_turn(1), completed_turn(2)))
    monitor.refresh([row(tmp_path)])
    assert monitor.warnings == {} and len(monitor.runs["high"].points) == 2


def test_missing_or_new_archive_never_borrows_old_chart(tmp_path):
    monitor = CurveMonitor(batch_script.read_monitor_json)
    monitor.refresh([{"effort": "medium", "status": "starting"}])
    assert monitor.runs == {} and "等待" in monitor.warnings["medium"]
    save(tmp_path / "first/replay.json", replay(completed_turn(1)))
    monitor.refresh([row(tmp_path / "first")])
    monitor.refresh([row(tmp_path / "second")])
    assert "high" not in monitor.runs
    assert "暂无曲线" in monitor.warnings["high"]


def test_terminal_plots_three_aligned_runs_and_labels_balance_vs_income(tmp_path):
    monitor = CurveMonitor(batch_script.read_monitor_json)
    rows = []
    for index, effort in enumerate(("medium", "high", "xhigh")):
        directory = tmp_path / effort
        save(directory / "replay.json", replay(completed_turn(1, score=100 + index * 50),
                                               completed_turn(2, score=200 + index * 50)))
        rows.append(row(directory, effort=effort))
    monitor.refresh(rows)
    text = text_chart(rows, monitor)
    for expected in ("medium", "high", "xhigh", "战力 Rank Score", "金币余额", "未分配经验池", "不补零", "已结算2回合", "35"):
        assert expected in text
    income = io.StringIO()
    Console(file=income, width=120).print(terminal_curves(Path("batch"), rows, monitor, resource_mode="income"))
    assert "累计讨伐金币收入" in income.getvalue() and "累计讨伐经验收入" in income.getvalue()
    assert "金币余额" not in income.getvalue()
    single = text_chart(rows, monitor, metric="power")
    assert "战力 Rank Score" in single and "金币余额" not in single
    empty = text_chart([], monitor)
    assert "暂无已记录数据" in empty


def test_svg_is_valid_escaped_contains_tooltips_and_does_not_connect_gaps(tmp_path):
    monitor = CurveMonitor(batch_script.read_monitor_json)
    monitor.runs["high"] = extract_curves(replay(completed_turn(1), completed_turn(2),
                                                completed_turn(3, score=None), completed_turn(4)), "high")
    rows = [row(tmp_path)]
    source = render_svg(Path('<batch&name>'), rows, monitor, resource_mode="balance", metric="power")
    root = ET.fromstring(source)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert len(root.findall("svg:polyline", ns)) == 1
    circles = root.findall("svg:circle", ns)
    assert len(circles) == 3
    assert "T4" in circles[-1].find("svg:title", ns).text
    assert "&lt;batch&amp;name&gt;" in source
    assert "nan" not in source.lower() and "inf" not in source.lower()
    assert "金币余额" not in source
    assert "不重新评分" in source


def test_cli_routes_curve_options_and_rejects_non_svg_output(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(batch_script, "curves", lambda batch, **kwargs: calls.append((batch, kwargs)) or 0)
    monkeypatch.setattr(sys, "argv", ["script", "curves", str(tmp_path), "--once", "--resource-mode", "income", "--metric", "gold"])
    assert batch_script.main() == 0
    assert calls == [(tmp_path.resolve(), {"once": True, "resource_mode": "income", "metric": "gold", "output": None})]
    monkeypatch.setattr(sys, "argv", ["script", "curves", str(tmp_path), "--output", "replay.json"])
    with pytest.raises(SystemExit) as error:
        batch_script.main()
    assert error.value.code == 2


@pytest.mark.parametrize("efforts", [
    ("high",),
    ("medium", "high", "xhigh"),
    ("gemini4-medium", "gemini4-high", "astra-xhigh"),
])
def test_curve_command_reads_active_batch_and_exports_without_changing_run_files(tmp_path, capsys, efforts):
    save(tmp_path / "batch.json", {"efforts": list(efforts), "max_turns": 35})
    for index, effort in enumerate(efforts):
        archive = tmp_path / effort / "archive"
        save(archive / "replay.json", replay(completed_turn(1, score=100 + index * 50),
                                               completed_turn(2, score=200 + index * 50)))
        save(tmp_path / effort / "progress.json", row(archive, effort=effort))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert batch_script.curves(tmp_path, once=True) == 0
    terminal = capsys.readouterr().out
    assert "金币余额" in terminal
    assert all(effort in terminal for effort in efforts)
    output = tmp_path / "report/income.svg"
    assert batch_script.curves(tmp_path, resource_mode="income", output=output) == 0
    assert "已导出曲线" in capsys.readouterr().out
    root = ET.parse(output).getroot()
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert root.tag.endswith("svg")
    colors = {}
    for circle in root.findall("svg:circle", ns):
        name = circle.find("svg:title", ns).text.split(" · ")[0]
        colors.setdefault(name, set()).add(circle.attrib["fill"])
    assert set(colors) == set(efforts)
    assert all(len(values) == 1 for values in colors.values())
    assert len(set.union(*colors.values())) == len(efforts)
    assert "累计讨伐经验收入" in output.read_text(encoding="utf-8")
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}


def test_live_curves_keep_refreshing_after_unreadable_progress(monkeypatch, tmp_path):
    initial = [{"effort": "high", "status": "running"}]
    stale = [{"effort": "high", "status": "running", "monitor_warning": "进度暂不可读"}]
    final = [{"effort": "high", "status": "completed"}]
    calls = []
    def read(batch, previous=None):
        calls.append(previous)
        return [initial, stale, final][len(calls) - 1]
    monkeypatch.setattr(batch_script, "read_progress", read)
    monkeypatch.setattr(batch_script.time, "sleep", lambda _: None)
    assert batch_script.curves(tmp_path) == 1  # No archive yet, but the monitor finishes normally.
    assert calls == [None, initial, stale]
