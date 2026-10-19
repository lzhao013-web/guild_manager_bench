import json
from pathlib import Path

import pytest

from guild_manager_bench.bench.leaderboard import build_leaderboard


def replay(sid, preset="full", version="v1", score=100, model="same-model"):
    return {
        "kind": "llm_replay", "status": "completed", "session_id": sid,
        "agent": {"config": {"model": model}}, "created_at": "20261004-120000",
        "data": {"preset": preset, "data_hash": preset + "-data", **({
            "benchmark_version": version, "protocol_hash": preset + "-" + version,
            "settings_hash": "settings", "protocol_status": "recorded",
        } if version else {})},
        "score": {"score": 50, "rank_score": score, "chosen_win_rate": .5},
        "turns": [], "final_observation": {},
    }


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_models_are_aggregated_only_within_preset_and_version(tmp_path):
    source, output = tmp_path / "data", tmp_path / "out/board.json"
    for i, (preset, version, score) in enumerate([
        ("full", "v1", 100), ("full", "v1", 300), ("full", "v2", 900),
        ("advanced", "v1", 50), ("full", None, 500),
    ]):
        dump(source / f"{i}.json", replay(str(i), preset, version, score))
    build_leaderboard(source, output)
    board = json.loads(output.read_text())
    assert "models" not in board
    groups = {(b["preset"], b["version"]): b for b in board["boards"]}
    assert len(groups) == 4
    assert groups["full", "v1"]["models"][0]["rank_score"]["mean"] == 200
    assert groups["full", "v2"]["total_runs"] == 1
    assert groups["advanced", "v1"]["models"][0]["rank"] == 1
    # 缓存重建和强制重新解析都不丢运行或再次计数。
    for incremental in (True, False):
        build_leaderboard(source, output, incremental=incremental)
        updated = json.loads(output.read_text())
        assert updated["boards"] == board["boards"]


def test_same_version_with_changed_fingerprint_is_rejected(tmp_path):
    source = tmp_path / "data"
    a, b = replay("a"), replay("b")
    b["data"]["protocol_hash"] = "changed"
    dump(source / "a.json", a)
    dump(source / "b.json", b)
    with pytest.raises(ValueError, match="conflicting protocol"):
        build_leaderboard(source, tmp_path / "board.json")
    assert not (tmp_path / "board.json").exists()


def test_migration_preserves_unavailable_historical_runs_and_published_scores(tmp_path):
    output = tmp_path / "out/board.json"
    historical = {
        "run_id": "old-file", "session_id": "old", "preset": "full", "data_hash": "old-data",
        "created_at": "20260101", "score": 40, "rank_score": 123.4, "win_rate": .5,
    }
    dump(output, {"schema_version": 2, "models": [{"model": "old-model", "run_details": [historical]}]})
    source = tmp_path / "data"
    dump(source / "new.json", replay("new"))
    build_leaderboard(source, output, incremental=False)
    board = json.loads(output.read_text())
    assert board["total_runs"] == 2
    old = next(b for b in board["boards"] if b["version"].startswith("legacy-"))
    assert old["protocol_status"] == "unverified"
    assert old["models"][0]["run_details"][0]["rank_score"] == 123.4


def test_recursive_archives_skip_failed_attempt_and_deduplicate_session(tmp_path):
    source = tmp_path / "published"
    runs = tmp_path / "runs"
    dump(source / "same.json", replay("a"))
    dump(runs / "first/replay.json", {**replay("b"), "status": "failed"})
    dump(runs / "resumed/replay.json", replay("b"))
    dump(runs / "copy/replay.json", replay("a"))
    output = tmp_path / "out/board.json"
    build_leaderboard(source, output, run_dirs=(runs,))
    assert json.loads(output.read_text())["total_runs"] == 2


def test_legacy_missing_score_is_not_recomputed_with_current_engine(tmp_path, monkeypatch):
    from guild_manager_bench.bench import replay_scoring
    def forbidden(*args, **kwargs):
        raise AssertionError("leaderboard must not score legacy runs")
    monkeypatch.setattr(replay_scoring, "with_rank_score_from_final_observation", forbidden)
    r = replay("legacy", version=None)
    r["score"].pop("rank_score")
    source = tmp_path / "data"
    dump(source / "r.json", r)
    output = tmp_path / "out/board.json"
    build_leaderboard(source, output)
    assert json.loads(output.read_text())["boards"][0]["models"][0]["rank_score"] is None
