"""怪物分组仅用于界面，不依赖当前回合，不改写事件或游戏数据。"""
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guild_manager_bench.api.monster_display import monster_stages
from guild_manager_bench.api.routes_workbench import workbench_router
from guild_manager_bench.game.presets import DataPreset, resolve_data_source
from test_workbench import manager

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("preset", ["default", "full", "advanced"])
def test_groups_come_from_archetype_unlock_stage(preset):
    stages = monster_stages(resolve_data_source(ROOT / "data", preset))
    if preset == "default":
        assert stages == {name: "t1" for name in ("forest_wolf", "cave_goblin", "stone_guardian", "wild_shaman")}
        return
    assert stages["goblin"] == "t1"
    assert stages["orc_captain"] == "t2"
    assert stages["lich"] == "t3"
    if preset == "advanced":
        for name in ("mana_hound", "crystal_guardian", "blood_fanatic"):
            assert stages[name] == "t3"
        for name in ("iron_colossus", "blade_tyrant", "doom_oracle"):
            assert stages[name] == "challenge"


def test_missing_preset_or_edited_invalid_file_is_only_unknown_display_metadata(tmp_path):
    preset = DataPreset("fixture", tmp_path, "fixture-hash", "preset")
    assert monster_stages(None) == {}
    assert monster_stages(preset) == {}
    path = tmp_path / "monsters.yaml"
    path.write_text("monsters: [", encoding="utf-8")
    assert monster_stages(preset) == {}
    path.write_text("monsters: [{id: old-default}, {id: bad, min_turn: late}]", encoding="utf-8")
    assert monster_stages(preset) == {}
    path.write_text("monsters: [{id: old-default}]", encoding="utf-8")
    assert monster_stages(preset) == {"old-default": "t1"}


def test_catalog_cache_updates_after_source_file_changes(tmp_path):
    preset = DataPreset("fixture", tmp_path, "fixture-hash", "preset")
    path = tmp_path / "monsters.yaml"
    path.write_text("monsters: [{id: changed, min_turn: 1}]", encoding="utf-8")
    assert monster_stages(preset) == {"changed": "t1"}
    path.write_text("monsters: [{id: changed, min_turn: 18}]\n# updated", encoding="utf-8")
    assert monster_stages(preset) == {"changed": "t3"}


def test_live_and_trajectory_pages_share_display_metadata_without_rewriting_journal(tmp_path):
    m = manager(tmp_path)
    job_id = "monster-display"
    m.presets["full"] = resolve_data_source(ROOT / "data", "full")
    m.jobs[job_id] = {"id": job_id, "config": {"preset": "full"}, "status": "interrupted", "archive_id": None}
    directory = m.root / job_id
    directory.mkdir(parents=True)
    observation = {"turn": 29, "monsters": [{"monster_id": "late-slime", "archetype_id": "slime", "name": "首领·史莱姆", "tier": "boss"}]}
    raw = json.dumps({"at": "2026-10-20T12:00:00Z", "event": {"type": "turn_started", "turn": 29, "observation": observation}}) + "\n"
    path = directory / "events.jsonl"
    path.write_text(raw, encoding="utf-8")
    before = path.read_bytes()
    app = FastAPI()
    app.include_router(workbench_router(m))
    with TestClient(app) as client:
        live = client.get(f"/api/workbench/jobs/{job_id}/watch").json()
        assert live["monster_stages"]["slime"] == "t1"
        event = live["records"][0]["event"]
        assert event["observation"]["monsters"][0]["tier"] == "boss"
        assert "display_stage" not in event["observation"]["monsters"][0]
        historical = client.get(f"/api/workbench/jobs/{job_id}/trajectories", params={"attempt": live["selected"], "origin": "execution"})
        assert historical.status_code == 200
        assert historical.json()["monster_stages"] == live["monster_stages"]
        m.presets.clear()
        assert client.get(f"/api/workbench/jobs/{job_id}/watch").json()["monster_stages"] == {}
    assert path.read_bytes() == before
