from __future__ import annotations

import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from guild_manager_bench.bench.metrics import compute_rank_score, rank_score_from_final_observation
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.state import GameDefinition


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def with_rank_score_from_final_observation(
    replay: Mapping[str, Any],
    *,
    project_root: Path | None = None,
    strict: bool = False,
    save_path: Path | None = None,
) -> dict[str, Any]:
    """Return replay with missing score.rank_score filled from final_observation.

    The original replay file is not modified unless save_path is provided.
    This is best-effort by default so legacy or partial archives can still be
    listed and opened.

    If save_path is set and rank_score was computed, the updated score is
    written back to that path so subsequent calls don't need to recompute.
    """

    result = dict(replay)
    score = result.get("score")
    if isinstance(score, Mapping) and score.get("rank_score") is not None:
        return result

    observation = result.get("final_observation")
    if not isinstance(observation, Mapping):
        return result

    try:
        definition = _definition_for_replay(result, project_root=project_root)
        rank_score = rank_score_from_final_observation(definition, observation)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if strict:
            raise
        return result

    score_data = dict(score) if isinstance(score, Mapping) else {}
    score_data.setdefault("mode", definition.scoring.mode)
    score_data.setdefault("seed", definition.scoring.seed)
    score_data["rank_score"] = rank_score
    score_data["rank_score_source"] = "final_observation"
    result["score"] = score_data

    if save_path is not None:
        _write_json_atomic(save_path, result)

    return result


def _definition_for_replay(
    replay: Mapping[str, Any],
    *,
    project_root: Path | None,
) -> GameDefinition:
    root = project_root or _PROJECT_ROOT
    data_dir = _replay_data_dir(replay, project_root=root)
    definition = _load_definition(str(data_dir.resolve()))
    scoring = _scoring_overrides(replay)
    if not scoring:
        return definition
    return replace(definition, scoring=replace(definition.scoring, **scoring))


def _replay_data_dir(replay: Mapping[str, Any], *, project_root: Path) -> Path:
    data = replay.get("data")
    data = data if isinstance(data, Mapping) else {}

    archived_dir = data.get("data_dir")
    if isinstance(archived_dir, str) and archived_dir.strip():
        path = Path(archived_dir)
        return path if path.is_absolute() else _relative_path(path, project_root)

    preset = data.get("preset")
    if isinstance(preset, str) and preset.strip():
        return _relative_path(Path("data") / "presets" / preset, project_root)

    return _relative_path(Path("data") / "presets" / "default", project_root)


def _relative_path(path: Path, project_root: Path) -> Path:
    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path
    return project_root / path


@lru_cache(maxsize=8)
def _load_definition(data_dir: str) -> GameDefinition:
    return load_game_definition(Path(data_dir))


def _scoring_overrides(replay: Mapping[str, Any]) -> dict[str, Any]:
    observation = replay.get("final_observation")
    observation = observation if isinstance(observation, Mapping) else {}
    scoring = observation.get("scoring")
    scoring = scoring if isinstance(scoring, Mapping) else {}
    data = replay.get("data")
    data = data if isinstance(data, Mapping) else {}
    config = replay.get("config")
    config = config if isinstance(config, Mapping) else {}

    overrides: dict[str, Any] = {}
    seed = _first_int(scoring.get("seed"), data.get("scoring_seed"), config.get("scoring_seed"))
    if seed is not None:
        overrides["seed"] = seed

    for key in ("waves", "wave_size", "rank_min_diff", "rank_max_diff", "rank_step", "rank_waves"):
        value = scoring.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            overrides[key] = value

    difficulty_factors = scoring.get("difficulty_factors")
    if isinstance(difficulty_factors, list) and difficulty_factors:
        if all(isinstance(value, int) and not isinstance(value, bool) for value in difficulty_factors):
            overrides["difficulty_factors"] = tuple(difficulty_factors)

    for key in ("resource_mode", "aggregation"):
        value = scoring.get(key)
        if isinstance(value, str):
            overrides[key] = value

    return overrides


def _first_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temp_path.replace(path)


def with_rank_score_curve(
    replay: Mapping[str, Any],
    *,
    project_root: Path | None = None,
    save_path: Path | None = None,
) -> dict[str, Any]:
    """Return replay with missing per-turn rank_scores filled from observations.

    For each turn that lacks a ``rank_score`` field, the function finds the
    observation snapshot *after* that turn's end_turn (the next turn's
    ``observation_before``, or ``final_observation`` for the last turn) and
    computes the rank score from it.

    The original replay file is not modified unless *save_path* is provided.
    """

    result = dict(replay)
    turns = list(_sequence(result.get("turns")))
    if not turns:
        return result

    # Fast path: every turn already has rank_score
    needs_compute = False
    for turn in turns:
        if not isinstance(turn, Mapping):
            continue
        if turn.get("status") == "completed" and turn.get("rank_score") is None:
            needs_compute = True
            break
    if not needs_compute:
        return result

    try:
        definition = _definition_for_replay(result, project_root=project_root)
    except (OSError, ValueError, TypeError, KeyError):
        return result

    final_obs = result.get("final_observation")
    rebuilt_turns: list[Any] = []
    changed = False

    for i, turn in enumerate(turns):
        if not isinstance(turn, Mapping) or turn.get("status") != "completed" or turn.get("rank_score") is not None:
            rebuilt_turns.append(turn)
            continue

        # Resolve the observation after this turn's end_turn:
        #   - next turn's observation_before, or
        #   - final_observation for the last completed turn
        observation = None
        if i + 1 < len(turns):
            next_turn = turns[i + 1]
            if isinstance(next_turn, Mapping):
                observation = next_turn.get("observation_before")
        if observation is None and i == len(turns) - 1:
            observation = final_obs

        rank_score: float | None = None
        if isinstance(observation, Mapping):
            try:
                rank_score = rank_score_from_final_observation(definition, observation)
            except (ValueError, TypeError, KeyError):
                pass

        if rank_score is not None:
            turn = dict(turn)
            turn["rank_score"] = rank_score
            changed = True

        rebuilt_turns.append(turn)

    if changed:
        result["turns"] = rebuilt_turns
        if save_path is not None:
            _write_json_atomic(save_path, result)

    return result


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else ()
