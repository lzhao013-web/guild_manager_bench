import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from guild_manager_bench.api.routes_llm_archive import llm_archive_router
from guild_manager_bench.game.actions import RecruitAction
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.state import ScoringRules
from guild_manager_bench.runtime.session import GameSession


def test_get_replay_is_read_only(tmp_path: Path) -> None:
    replay_path = _write_replay(tmp_path, "run-a", _legacy_replay())
    before = replay_path.read_text(encoding="utf-8")

    response = _call_route(
        tmp_path,
        "/api/llm/runs/{run_id}/replay",
        "GET",
        run_id="run-a",
    )

    assert response["score"] == {"score": 12.34}
    assert replay_path.read_text(encoding="utf-8") == before


def test_rescore_replay_fills_rank_score_and_writes_file(tmp_path: Path) -> None:
    replay_path = _write_replay(tmp_path, "run-b", _legacy_replay())

    response = _call_route(
        tmp_path,
        "/api/llm/runs/{run_id}/rescore",
        "POST",
        run_id="run-b",
    )

    stored = json.loads(replay_path.read_text(encoding="utf-8"))
    assert response["score"]["score"] == 12.34
    assert response["score"]["rank_score"] >= 0
    assert response["score"]["rank_score_source"] == "final_observation"
    assert stored["score"]["rank_score"] == response["score"]["rank_score"]


def test_list_runs_infers_preset_from_archived_data_dir(tmp_path: Path) -> None:
    replay = _legacy_replay()
    replay["data"] = {"data_dir": str(Path("data") / "presets" / "full")}
    _write_replay(tmp_path, "run-c", replay)

    response = _call_route(
        tmp_path,
        "/api/llm/runs",
        "GET",
    )

    assert response["runs"][0]["preset"] == "full"


def _call_route(
    base_dir: Path,
    path: str,
    method: str,
    **kwargs: Any,
) -> dict[str, Any]:
    endpoint = _route_endpoint(base_dir, path, method)
    return asyncio.run(endpoint(**kwargs))


def _route_endpoint(base_dir: Path, path: str, method: str) -> Callable[..., Any]:
    router = llm_archive_router(base_dir)
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def _write_replay(base_dir: Path, run_id: str, replay: dict[str, Any]) -> Path:
    run_dir = base_dir / run_id
    run_dir.mkdir()
    replay_path = run_dir / "replay.json"
    replay_path.write_text(json.dumps(replay, ensure_ascii=False), encoding="utf-8")
    return replay_path


def _legacy_replay() -> dict[str, Any]:
    definition = _small_scoring_definition()
    session = GameSession(definition)
    candidate_id = session.observation()["recruit_candidates"][0]["candidate_id"]
    session.apply_preparation(RecruitAction(candidate_id=candidate_id))
    observation = session.observation()
    observation["scoring"].update(
        {
            "rank_min_diff": definition.scoring.rank_min_diff,
            "rank_max_diff": definition.scoring.rank_max_diff,
            "rank_step": definition.scoring.rank_step,
            "rank_waves": definition.scoring.rank_waves,
        }
    )
    return {
        "kind": "llm_replay",
        "status": "completed",
        "created_at": "2026-05-31T00:00:00",
        "agent": {"config": {"model": "test-model"}},
        "data": {"data_dir": str(_data_dir())},
        "turns": [],
        "final_observation": observation,
        "score": {"score": 12.34},
    }


def _small_scoring_definition():
    definition = load_game_definition(_data_dir())
    return replace(
        definition,
        scoring=ScoringRules(
            seed=123,
            waves=4,
            wave_size=2,
            difficulty_factors=(0, 2),
            rank_min_diff=5,
            rank_max_diff=15,
            rank_step=5,
            rank_waves=2,
        ),
    )


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "presets" / "default"
