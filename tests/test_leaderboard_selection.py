import json
from pathlib import Path

import pytest

from guild_manager_bench.bench.leaderboard_selection import build_selected_leaderboard


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def replay(session, score, *, preset="advanced", status="recorded", effort=None, model="model"):
    return {
        "kind": "llm_replay", "status": "completed", "session_id": session,
        "agent": {"config": {"model": model, "reasoning_effort": effort}},
        "created_at": "20261021-120000", "turns": [], "final_observation": {},
        "data": {"preset": preset, "benchmark_version": "snapshot-same", "data_hash": session,
                 "protocol_hash": session, "settings_hash": "settings", "protocol_status": status},
        "score": {"rank_score": score, "score": 50},
    }


def selection(tmp_path, sources, *, extra_boards=()):
    path = tmp_path / "selection.json"
    dump(path, {
        "schema_version": 1,
        "default_board": {"preset": "advanced", "version": "current"},
        "boards": [{"preset": "advanced", "version": "current", "label": "当前",
                    "sources": sources}, *extra_boards],
    })
    return path


def result(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_selection_accepts_mixed_runs_and_different_fingerprints(tmp_path, monkeypatch):
    from guild_manager_bench.bench import replay_scoring
    monkeypatch.setattr(replay_scoring, "with_rank_score_from_final_observation",
                        lambda *a, **k: pytest.fail("publishing must not score replays"))
    dump(tmp_path / "a.json", replay("a", 100))
    dump(tmp_path / "b.json", replay("b", 300, status="mixed"))
    spec = selection(tmp_path, [{"replay": "a.json"}, {"replay": "b.json"}])
    output = tmp_path / "out/board.json"
    build_selected_leaderboard(spec, output)
    board = result(output)["boards"][0]
    assert board["total_runs"] == 2
    assert board["models"][0]["rank_score"]["mean"] == 200
    assert "protocol_status" not in board
    assert {r["protocol_status"] for r in board["models"][0]["run_details"]} == {"recorded", "mixed"}


@pytest.mark.parametrize("incremental", [True, False])
def test_removing_a_selected_run_does_not_restore_it_from_output_or_cache(tmp_path, incremental):
    dump(tmp_path / "a.json", replay("a", 100))
    dump(tmp_path / "b.json", replay("b", 300))
    output = tmp_path / "board.json"
    spec = selection(tmp_path, [{"replay": "a.json"}, {"replay": "b.json"}])
    build_selected_leaderboard(spec, output)
    selection(tmp_path, [{"replay": "a.json"}])
    build_selected_leaderboard(spec, output, incremental=incremental)
    assert result(output)["total_runs"] == 1
    assert result(output)["boards"][0]["models"][0]["rank_score"]["mean"] == 100


def test_historical_full_preserves_labels_scores_and_missing_values(tmp_path):
    detail = {"session_id": "old", "run_id": "published", "preset": "full", "rank_score": 123.45,
              "score": None, "win_rate": None, "created_at": "20261001-120000"}
    dump(tmp_path / "history.json", {"schema_version": 2, "models": [
        {"model": "published-high", "run_details": [detail]},
        {"model": "manual", "run_details": [{**detail, "session_id": "manual", "preset": "manual"}]},
    ]})
    dump(tmp_path / "a.json", replay("a", 200))
    spec = selection(tmp_path, [{"replay": "a.json"}], extra_boards=[{
        "preset": "full", "version": "history", "label": "历史数据",
        "sources": [{"published": "history.json"}],
    }])
    output = tmp_path / "board.json"
    build_selected_leaderboard(spec, output)
    boards = result(output)["boards"]
    assert result(output)["total_runs"] == 2
    assert boards[1]["models"][0]["model"] == "published-high"
    actual = boards[1]["models"][0]["run_details"][0]
    assert actual["rank_score"] == 123.45
    assert actual["token_usage"] is None
    assert actual["reasoning_effort"] is None


def test_models_are_grouped_by_recorded_effort_without_guessing_defaults(tmp_path):
    sources = []
    for i, effort in enumerate(("high", "max", None, "high")):
        filename = f"{i}.json"
        dump(tmp_path / filename, replay(str(i), 100 + i, effort=effort))
        sources.append({"replay": filename})
    output = tmp_path / "board.json"
    build_selected_leaderboard(selection(tmp_path, sources), output)
    models = {m["model"]: m for m in result(output)["boards"][0]["models"]}
    assert set(models) == {"model", "model / high", "model / max"}
    assert models["model / high"]["runs"] == 2
    assert models["model / high"]["rank_score"]["mean"] == 101.5
    assert models["model"]["reasoning_effort"] is None
    assert models["model / max"]["run_details"][0]["model_name"] == "model"


def test_anthropic_effort_is_recorded_and_existing_suffix_is_not_duplicated(tmp_path):
    value = replay("a", 100, model="model:max")
    value["agent"]["config"].pop("reasoning_effort")
    value["agent"]["config"]["effort"] = "max"
    dump(tmp_path / "a.json", value)
    output = tmp_path / "board.json"
    build_selected_leaderboard(selection(tmp_path, [{"replay": "a.json"}]), output)
    model = result(output)["boards"][0]["models"][0]
    assert model["model"] == "model:max"
    assert model["reasoning_effort"] == "max"


def test_duplicate_sessions_count_once_and_last_selected_source_wins(tmp_path):
    dump(tmp_path / "a.json", replay("same", 100))
    dump(tmp_path / "b.json", replay("same", 200))
    output = tmp_path / "board.json"
    build_selected_leaderboard(selection(tmp_path, [{"replay": "a.json"}, {"replay": "b.json"}]), output)
    assert result(output)["total_runs"] == 1
    assert result(output)["boards"][0]["models"][0]["rank_score"]["mean"] == 200


def test_cache_skips_parsing_unchanged_replay_and_invalidates_changed_source(tmp_path, monkeypatch):
    from guild_manager_bench.bench import leaderboard_selection as module
    dump(tmp_path / "a.json", replay("a", 100))
    spec = selection(tmp_path, [{"replay": "a.json"}])
    output = tmp_path / "out/board.json"
    build_selected_leaderboard(spec, output)
    original = module._extract_run_info
    calls = []
    def extract(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "_extract_run_info", extract)
    build_selected_leaderboard(spec, output)
    assert not calls
    dump(tmp_path / "a.json", replay("a", 10000))
    build_selected_leaderboard(spec, output)
    assert calls == [1]
    assert result(output)["boards"][0]["models"][0]["rank_score"]["mean"] == 10000


@pytest.mark.parametrize("change,match", [
    ("unfinished", "no completed result"),
    ("wrong-preset", "preset differs"),
    ("missing-file", None),
    ("invalid-source", "exactly one"),
    ("duplicate-board", "duplicate board"),
    ("wrong-default", "default_board"),
])
def test_invalid_selection_does_not_replace_published_output(tmp_path, change, match):
    value = replay("a", 100)
    if change == "unfinished":
        value["status"] = "interrupted"
    if change == "wrong-preset":
        value["data"]["preset"] = "full"
    dump(tmp_path / "a.json", value)
    spec = selection(tmp_path, [{"replay": "a.json"}])
    data = result(spec)
    if change == "missing-file":
        data["boards"][0]["sources"] = [{"replay": "missing.json"}]
    if change == "invalid-source":
        data["boards"][0]["sources"] = [{"replay": "a.json", "published": "a.json"}]
    if change == "duplicate-board":
        data["boards"].append(data["boards"][0])
    if change == "wrong-default":
        data["default_board"]["preset"] = "missing"
    dump(spec, data)
    output = tmp_path / "board.json"
    output.write_text("original", encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError), match=match):
        build_selected_leaderboard(spec, output)
    assert output.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("collision", ["selection", "source", "cache-output", "temporary-source", "cache-source"])
def test_publishing_cannot_overwrite_its_inputs(tmp_path, collision):
    source = tmp_path / {"temporary-source": "board.json.tmp", "cache-source": ".selection_cache.json"}.get(collision, "a.json")
    dump(source, replay("a", 100))
    spec = selection(tmp_path, [{"replay": source.name}])
    output = {"selection": spec, "source": source, "cache-output": tmp_path / ".selection_cache.json"}.get(collision, tmp_path / "board.json")
    source_before, spec_before = source.read_bytes(), spec.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        build_selected_leaderboard(spec, output)
    assert source.read_bytes() == source_before
    assert spec.read_bytes() == spec_before


def test_standalone_leaderboard_serves_shared_assets_without_exposing_parent(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from guild_manager_bench import cli
    directory = tmp_path / "leaderboard"
    directory.mkdir()
    (tmp_path / "assets").mkdir()
    (directory / "index.html").write_text("<h1>Leaderboard</h1>", encoding="utf-8")
    (tmp_path / "assets/table-resize.mjs").write_text("export const resize = true;", encoding="utf-8")
    (tmp_path / "private.txt").write_text("not public", encoding="utf-8")
    captured = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: captured.update(app=app, **kwargs))
    cli._serve_leaderboard("127.0.0.1", 8080, directory)
    assert captured["port"] == 8080
    with TestClient(captured["app"]) as client:
        assert "Leaderboard" in client.get("/").text
        assert client.get("/assets/table-resize.mjs").text == "export const resize = true;"
        assert client.get("/private.txt").status_code == 404
        assert client.get("/assets/%2e%2e/private.txt").status_code == 404


def test_cli_accepts_selection_and_rejects_conflicting_sources(monkeypatch, tmp_path):
    import sys
    from guild_manager_bench import cli
    captured = {}
    monkeypatch.setattr(cli, "_build_leaderboard", lambda *args, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(sys, "argv", ["guild-manager", "build-leaderboard", "--selection", "selected.json"])
    cli.main()
    assert captured["selection"] == Path("selected.json")
    for other in (["--data-dir", "data"], ["--run-dir", "runs"]):
        monkeypatch.setattr(sys, "argv", ["guild-manager", "build-leaderboard", "--selection", "selected.json", *other])
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 2
