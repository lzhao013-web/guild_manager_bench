"""按人工收录清单发布排行榜，不以协议状态决定收录资格。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from guild_manager_bench.bench.leaderboard import (
    _aggregate_model,
    _extract_run_info,
    _file_fingerprint,
    _load_cache,
    _previous_runs,
    _run_key,
    _save_cache,
    _sort_key,
)


def _required_text(value: dict, key: str) -> str:
    text = value[key]
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return text


def _model_label(model: str, effort: str | None) -> str:
    if effort is None or model.endswith((f":{effort}", f"-{effort}")):
        return model
    return f"{model} / {effort}"


def _models(runs: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str | None], list[dict]] = {}
    for run in runs:
        groups.setdefault((run["model"], run.get("reasoning_effort")), []).append(run)
    models = []
    labels = set()
    for (name, effort), members in groups.items():
        label = _model_label(name, effort)
        if label in labels:
            raise ValueError(f"duplicate model label: {label}")
        labels.add(label)
        models.append({
            **_aggregate_model(members),
            "model": label,
            "model_name": name,
            "reasoning_effort": effort,
        })
    models.sort(key=lambda model: (-_sort_key(model), model["model"]))
    for rank, model in enumerate(models, 1):
        model["rank"] = rank
    return models


def build_selected_leaderboard(selection: Path, output: Path, *, incremental: bool = True) -> None:
    """清单是完整输入；不从上次输出补回已经移除的运行，不重新评分。"""
    selection = selection.resolve()
    output = output.resolve()
    cache_path = output.parent / ".selection_cache.json"
    temporary = output.with_suffix(output.suffix + ".tmp")
    write_paths = {output, cache_path, temporary}
    if len(write_paths) != 3 or selection in write_paths:
        raise ValueError("output or cache would overwrite selection")
    spec = json.loads(selection.read_text(encoding="utf-8"))
    if spec["schema_version"] != 1:
        raise ValueError("unsupported leaderboard selection schema")
    definitions = spec["boards"]
    if not isinstance(definitions, list) or not definitions:
        raise ValueError("selection requires at least one board")
    cache = _load_cache(cache_path) if incremental else {}
    next_cache = {}
    boards = []
    identities = set()
    for definition in definitions:
        preset = _required_text(definition, "preset")
        version = _required_text(definition, "version")
        label = _required_text(definition, "label")
        identity = (preset, version)
        if identity in identities:
            raise ValueError(f"duplicate board: {preset}/{version}")
        identities.add(identity)
        sources = definition["sources"]
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"board {preset}/{version} requires sources")
        runs = {}
        for source in sources:
            if not isinstance(source, dict) or len(source) != 1 or next(iter(source)) not in {"replay", "published"}:
                raise ValueError("source must contain exactly one replay or published path")
            kind = next(iter(source))
            path = (selection.parent / _required_text(source, kind)).resolve(strict=True)
            if path in write_paths:
                raise ValueError(f"output or cache would overwrite source: {path}")
            key = f"{kind}:{path}"
            fingerprint = _file_fingerprint(path)
            cached = cache.get(key)
            if cached is not None and cached["fingerprint"] == fingerprint:
                entries = cached["runs"]
            elif kind == "published":
                entries = list(_previous_runs(path).values())
            else:
                replay = json.loads(path.read_text(encoding="utf-8"))
                info = _extract_run_info(replay, source_path=path)
                if info is None:
                    raise ValueError(f"selected replay has no completed result: {path}")
                entries = [info]
            next_cache[key] = {"fingerprint": fingerprint, "runs": entries}
            if kind == "replay" and entries[0].get("preset") != preset:
                raise ValueError(f"selected replay preset differs from {preset}: {path}")
            members = [entry for entry in entries if entry.get("preset") == preset]
            if not members:
                raise ValueError(f"source has no {preset} runs: {path}")
            for entry in members:
                runs[_run_key(entry)] = entry
        boards.append({
            "preset": preset,
            "version": version,
            "label": label,
            "selection_mode": "manual",
            "total_runs": len(runs),
            "models": _models(list(runs.values())),
        })
    default = spec["default_board"]
    if (default["preset"], default["version"]) not in identities:
        raise ValueError("default_board is not in selection")
    result = {
        "schema_version": 3,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "default_board": default,
        "total_runs": sum(board["total_runs"] for board in boards),
        "boards": boards,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output)
    _save_cache(cache_path, next_cache)
    print(f"OK {len(boards)} selected board(s), {result['total_runs']} run(s) -> {output}")
    for board in boards:
        print(f"  {board['preset']} / {board['label']}: {board['total_runs']} runs")
