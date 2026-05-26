from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_PRESET = "default"
PRESETS_DIR = "presets"
REQUIRED_DATA_FILES = (
    "game.yaml",
    "adventurers.yaml",
    "monsters.yaml",
    "equipment.yaml",
    "crafting_recipes.yaml",
    "global_upgrades.yaml",
)


@dataclass(frozen=True, slots=True)
class DataPreset:
    """Resolved game data preset metadata."""

    name: str | None
    data_dir: Path
    data_hash: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset": self.name,
            "data_dir": str(self.data_dir),
            "data_hash": self.data_hash,
            "source": self.source,
        }


def resolve_data_preset(
    data_dir: str | Path = "data",
    preset: str | None = None,
) -> DataPreset:
    """Resolve a game data directory, optionally through data/presets/<name>."""

    base_dir = Path(data_dir)
    if preset is not None:
        preset_name = _preset_name(preset)
        preset_dir = base_dir / PRESETS_DIR / preset_name
        if preset_dir.is_dir():
            return _preset(preset_name, preset_dir, "preset")
        if preset_name == DEFAULT_PRESET and _looks_like_data_dir(base_dir):
            return _preset(preset_name, base_dir, "legacy_data_dir")
        raise ValueError(f"unknown data preset: {preset_name}")

    if _looks_like_data_dir(base_dir):
        name = DEFAULT_PRESET if base_dir.name == "data" else None
        return _preset(name, base_dir, "data_dir")
    raise ValueError(f"data directory is missing required YAML files: {base_dir}")


def list_data_presets(data_dir: str | Path = "data") -> list[DataPreset]:
    """List complete presets under data/presets."""

    presets_dir = Path(data_dir) / PRESETS_DIR
    if not presets_dir.is_dir():
        return []
    presets = []
    for path in sorted(item for item in presets_dir.iterdir() if item.is_dir()):
        if _looks_like_data_dir(path):
            presets.append(_preset(path.name, path, "preset"))
    return presets


def describe_data_source(
    data_dir: str | Path,
    *,
    preset: str | None = None,
    source: str = "data_dir",
) -> dict[str, Any]:
    """Return replay-safe metadata for a resolved game data directory."""

    path = Path(data_dir)
    name = _preset_name(preset) if preset is not None else None
    return _preset(name, path, source).to_dict()


def verify_data_source(
    archived: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> None:
    """Reject resume when replay data metadata proves the data changed."""

    if not archived:
        return
    archived_hash = archived.get("data_hash")
    current_hash = current.get("data_hash")
    if (
        isinstance(archived_hash, str)
        and isinstance(current_hash, str)
        and archived_hash != current_hash
    ):
        raise ValueError(
            "replay data_hash does not match the current game data; "
            "cannot safely resume"
        )
    for key in ("game_seed", "scoring_seed"):
        archived_seed = archived.get(key)
        current_seed = current.get(key)
        if (
            isinstance(archived_seed, int)
            and isinstance(current_seed, int)
            and archived_seed != current_seed
        ):
            raise ValueError(
                f"replay {key} does not match the current run; cannot safely resume"
            )


def _preset(name: str | None, data_dir: Path, source: str) -> DataPreset:
    return DataPreset(
        name=name,
        data_dir=data_dir,
        data_hash=hash_data_dir(data_dir),
        source=source,
    )


def hash_data_dir(data_dir: str | Path) -> str:
    """Hash the files that define a game configuration."""

    path = Path(data_dir)
    digest = hashlib.sha256()
    for filename in REQUIRED_DATA_FILES:
        file_path = path / filename
        if not file_path.exists():
            raise ValueError(f"missing required data file: {file_path}")
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _looks_like_data_dir(path: Path) -> bool:
    return all((path / filename).is_file() for filename in REQUIRED_DATA_FILES)


def _preset_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("preset must be a non-empty string")
    if any(char in name for char in "\\/"):
        raise ValueError("preset must be a simple directory name")
    if name in {".", ".."}:
        raise ValueError("preset must be a simple directory name")
    return name
