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
def _extract_run_info(replay: dict) -> dict | None:
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

    return {
        "model": model,
        "score": score_data.get("score"),
        "rank_score": score_data.get("rank_score"),
        "win_rate": score_data.get("chosen_win_rate"),
        "created_at": replay.get("created_at", ""),
        "data_hash": replay.get("data", {}).get("data_hash"),
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
        info = _extract_run_info(data)
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
