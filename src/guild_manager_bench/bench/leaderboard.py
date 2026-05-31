"""Leaderboard data builder.

Aggregates LLM replay files by model and produces a leaderboard JSON
consumed by the static leaderboard frontend.

Usage via CLI::

    uv run guild-manager build-leaderboard
    uv run guild-manager build-leaderboard --data-dir path/to/replays --output path/to/out.json

Usage programmatically::

    from guild_manager_bench.bench.leaderboard import build_leaderboard
    from pathlib import Path
    build_leaderboard(Path("web/leaderboard/data"), Path("web/leaderboard/leaderboard_data.json"))
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, median

from guild_manager_bench.bench.replay_scoring import with_rank_score_from_final_observation


# ── Helpers ───────────────────────────────────────────────────────────────────
def _extract_run_info(replay: dict, *, source_path: Path | None = None) -> dict | None:
    """Extract leaderboard-relevant fields from a replay dict."""
    if replay.get("kind") != "llm_replay":
        return None
    if replay.get("status") != "completed":
        return None

    score_data = replay.get("score")
    if not score_data:
        return None

    model = (replay.get("agent", {}).get("config", {}).get("model") or "").strip()
    if not model:
        return None

    turns = replay.get("turns")
    turns_count = len(turns) if isinstance(turns, list) else None
    data = replay.get("data")
    data = data if isinstance(data, dict) else {}
    final_observation = replay.get("final_observation")
    final_observation = final_observation if isinstance(final_observation, dict) else {}

    return {
        "run_id": source_path.stem if source_path is not None else replay.get("session_id", ""),
        "session_id": replay.get("session_id"),
        "model": model,
        "score": score_data.get("score"),
        "rank_score": score_data.get("rank_score"),
        "rank_score_source": score_data.get("rank_score_source"),
        "win_rate": score_data.get("chosen_win_rate"),
        "score_mode": score_data.get("mode"),
        "score_seed": score_data.get("seed"),
        "score_waves": score_data.get("waves"),
        "score_wave_size": score_data.get("wave_size"),
        "created_at": replay.get("created_at", ""),
        "updated_at": replay.get("updated_at", ""),
        "turns": turns_count,
        "preset": data.get("preset") or _preset_from_data_dir(data.get("data_dir")),
        "data_hash": data.get("data_hash"),
        "game_seed": data.get("game_seed"),
        "scoring_seed": data.get("scoring_seed"),
        "final_turn": final_observation.get("turn"),
        "max_turns": final_observation.get("max_turns"),
        "final_gold": final_observation.get("gold"),
        "final_experience_pool": final_observation.get("experience_pool"),
        "party_size": final_observation.get("party_size"),
        "party_size_limit": final_observation.get("party_size_limit"),
        "best_adventurer": _best_adventurer(score_data),
    }


def _aggregate_model(runs: list[dict]) -> dict:
    """Aggregate a list of per-run info dicts into a model summary."""
    scores = [r["score"] for r in runs if r["score"] is not None]
    rank_scores = [r["rank_score"] for r in runs if r["rank_score"] is not None]
    win_rates = [r["win_rate"] for r in runs if r["win_rate"] is not None]
    timestamps = [r["created_at"] for r in runs if r["created_at"]]

    result: dict = {"runs": len(runs)}

    if scores:
        result["score"] = {
            "best": max(scores),
            "mean": round(mean(scores), 2),
            "median": round(median(scores), 2),
        }
    else:
        result["score"] = None

    if rank_scores:
        result["rank_score"] = {
            "best": max(rank_scores),
            "mean": round(mean(rank_scores), 2),
            "median": round(median(rank_scores), 2),
        }
    else:
        result["rank_score"] = None

    if win_rates:
        result["win_rate"] = {
            "best": round(max(win_rates), 4),
            "mean": round(mean(win_rates), 4),
        }
    else:
        result["win_rate"] = None

    result["last_run"] = max(timestamps) if timestamps else ""
    result["run_details"] = [
        _run_detail(run)
        for run in sorted(runs, key=lambda item: item.get("created_at") or "", reverse=True)
    ]

    return result


def _sort_key(entry: dict) -> float:
    """Sort key: rank_score.best descending, then score.best descending."""
    rank = entry.get("rank_score")
    if rank and rank.get("best") is not None:
        return rank["best"]
    score = entry.get("score")
    if score and score.get("best") is not None:
        return score["best"]
    return -1.0


# ── Public API ────────────────────────────────────────────────────────────────
def build_leaderboard(data_dir: Path, output: Path) -> None:
    """Scan *data_dir* for replay JSON files and write aggregated leaderboard to *output*."""
    json_files = sorted(data_dir.glob("*.json"))
    if not json_files:
        print(f"No JSON files found in {data_dir}")
        sys.exit(1)

    print(f"Scanning {len(json_files)} file(s) in {data_dir} ...")

    # Parse and group by model
    model_runs: dict[str, list[dict]] = {}
    skipped = 0

    for path in json_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  ⚠ Skipping {path.name}: {e}")
            skipped += 1
            continue

        data = with_rank_score_from_final_observation(data)
        info = _extract_run_info(data, source_path=path)
        if info is None:
            skipped += 1
            continue

        model_runs.setdefault(info["model"], []).append(info)

    if not model_runs:
        print("No valid completed replays found.")
        sys.exit(1)

    # Aggregate
    models: list[dict] = []
    for model_name, runs in model_runs.items():
        agg = _aggregate_model(runs)
        agg["model"] = model_name
        models.append(agg)

    # Sort and assign ranks
    models.sort(key=_sort_key, reverse=True)
    for i, entry in enumerate(models):
        entry["rank"] = i + 1

    # Build output
    result = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_runs": sum(m["runs"] for m in models),
        "models": models,
    }

    # Write
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"✓ {len(models)} model(s), {result['total_runs']} run(s)"
          + (f", {skipped} skipped" if skipped else ""))
    print(f"  → {output}")
    for m in models:
        rs = m.get("rank_score")
        rs_str = f"{rs['best']:,.1f}" if rs else "—"
        sc = m.get("score")
        sc_str = f"{sc['best']:.2f}" if sc else "—"
        print(f"  #{m['rank']}  {m['model']:<40s}  rank={rs_str}  score={sc_str}  ({m['runs']} runs)")


def _run_detail(run: dict) -> dict:
    return {
        "run_id": run.get("run_id"),
        "session_id": run.get("session_id"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "score": run.get("score"),
        "rank_score": run.get("rank_score"),
        "rank_score_source": run.get("rank_score_source"),
        "win_rate": run.get("win_rate"),
        "turns": run.get("turns"),
        "preset": run.get("preset"),
        "data_hash": run.get("data_hash"),
        "game_seed": run.get("game_seed"),
        "scoring_seed": run.get("scoring_seed"),
        "score_mode": run.get("score_mode"),
        "score_seed": run.get("score_seed"),
        "score_waves": run.get("score_waves"),
        "score_wave_size": run.get("score_wave_size"),
        "final_turn": run.get("final_turn"),
        "max_turns": run.get("max_turns"),
        "final_gold": run.get("final_gold"),
        "final_experience_pool": run.get("final_experience_pool"),
        "party_size": run.get("party_size"),
        "party_size_limit": run.get("party_size_limit"),
        "best_adventurer": run.get("best_adventurer"),
    }


def _best_adventurer(score_data: dict) -> dict | None:
    per_adventurer = score_data.get("per_adventurer")
    if not isinstance(per_adventurer, list):
        return None
    candidates = [item for item in per_adventurer if isinstance(item, dict)]
    if not candidates:
        return None
    best = max(candidates, key=lambda item: item.get("average_score") or -1)
    return {
        "name": best.get("name"),
        "average_score": best.get("average_score"),
        "win_rate": best.get("win_rate"),
        "assignments": best.get("assignments"),
    }


def _preset_from_data_dir(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.parent.name != "presets":
        return None
    return path.name or None
