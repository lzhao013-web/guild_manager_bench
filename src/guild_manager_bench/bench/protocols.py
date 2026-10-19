"""评测协议标识及 full v1 的不可变运行入口。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FULL_ROOT = PACKAGE_ROOT / "_frozen" / "full_v1"
FULL_PROTOCOL_HASH = "2556ad1fe1825f1d7cd38237b7f5bd26feaf07c3f811bd86df34f9999c71a7fe"
CONFIG_FIELDS = (
    "objective", "max_tool_calls_per_turn", "max_empty_responses",
    "max_end_turn_attempts", "max_model_steps_per_turn", "max_turn_retries",
)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def verify_full_release() -> dict[str, Any]:
    """启动前检查冻结包，包括技能文件，不依赖旧版的六文件 data_hash。"""
    manifest = json.loads((FULL_ROOT / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    if _digest(files) != FULL_PROTOCOL_HASH:
        raise ValueError("full v1 manifest changed; create a new protocol version instead")
    for name, expected in files.items():
        if hashlib.sha256((FULL_ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"full v1 frozen file changed: {name}")
    return manifest


def full_runtime():
    verify_full_release()
    from guild_manager_bench._frozen.full_v1.bench.llm import runner
    return runner


def _yaml_hashes(data_dir: str | Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(data_dir).glob("*.yaml"))}


def protocol_metadata(data_dir: str | Path, definition, config, *, frozen: bool = False) -> dict[str, str]:
    settings = {name: getattr(config, name) for name in CONFIG_FIELDS}
    settings.update(game_seed=definition.rules.seed, scoring_seed=definition.scoring.seed)
    if frozen:
        runtime = full_runtime()
        defaults = runtime.LlmRunConfig()
        canonical = {name: getattr(defaults, name) for name in CONFIG_FIELDS}
        original = runtime.load_game_definition(FULL_ROOT / "data/presets/full")
        canonical.update(game_seed=original.rules.seed, scoring_seed=original.scoring.seed)
        version = "v1" if settings == canonical else f"v1-config-{_digest(settings)[:12]}"
        source_hash = FULL_PROTOCOL_HASH
    else:
        # 整个运行目录纳入指纹，新增辅助模块的后续修改也会形成新版本。
        paths = {PACKAGE_ROOT / "bench/metrics.py", PACKAGE_ROOT / "bench/protocols.py"}
        for directory in ("game", "runtime", "bench/llm"):
            paths.update((PACKAGE_ROOT / directory).rglob("*.py"))
        sources = {path.relative_to(PACKAGE_ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted(paths)}
        source_hash = _digest({"sources": sources, "data": _yaml_hashes(data_dir)})
        version = f"snapshot-{_digest({'source': source_hash, 'settings': settings})[:12]}"
    return {"benchmark_version": version, "protocol_hash": source_hash,
            "settings_hash": _digest(settings), "protocol_status": "frozen" if frozen else "recorded"}


def verify_resume_protocol(archive_dir: str | Path, current: Mapping[str, Any]) -> None:
    replay = json.loads((Path(archive_dir) / "replay.json").read_text(encoding="utf-8"))
    archived = replay.get("data", {})
    from guild_manager_bench.game.presets import verify_data_source
    verify_data_source(archived, current)
    for key in ("benchmark_version", "protocol_hash", "settings_hash"):
        if archived.get(key) != current.get(key):
            raise ValueError(f"replay {key} differs or is unverified; resume with its original protocol")


def run_frozen_full(agent, *, data_dir, config, session_id, event_sink, resume_archive_dir):
    runtime = full_runtime()
    frozen_data = FULL_ROOT / "data/presets/full"
    if _yaml_hashes(data_dir) != _yaml_hashes(frozen_data):
        raise ValueError("full data differs from frozen v1; use a new preset/version for changed rules")
    from guild_manager_bench.bench.llm.prompts import DEFAULT_OBJECTIVE as LIVE_DEFAULT_OBJECTIVE
    frozen_kwargs = {
        name: getattr(config, name)
        for name in (*CONFIG_FIELDS, "archive_dir", "game_seed", "scoring_seed")
    }
    if frozen_kwargs["objective"] == LIVE_DEFAULT_OBJECTIVE:
        frozen_kwargs["objective"] = runtime.DEFAULT_OBJECTIVE
    frozen_config = runtime.LlmRunConfig(**frozen_kwargs)
    definition = runtime._definition_for_config(frozen_data, frozen_config)
    source = runtime.describe_data_source(frozen_data)
    source.update(protocol_metadata(frozen_data, definition, frozen_config, frozen=True))
    source.update(game_seed=definition.rules.seed, scoring_seed=definition.scoring.seed)
    if resume_archive_dir is not None:
        verify_resume_protocol(resume_archive_dir, source)
    # 适配器继续使用已有响应接口，冻结 runner 自行构造记录，不改模型请求。
    return runtime.run_llm_game(
        agent, data_dir=frozen_data, config=frozen_config, session_id=session_id,
        event_sink=event_sink, resume_archive_dir=resume_archive_dir, data_source=source,
    )


def run_version(data: Mapping[str, Any]) -> dict[str, Any]:
    """旧记录不根据相同数据哈希冒认冻结协议。"""
    version = data.get("benchmark_version")
    if version and not str(version).startswith("legacy-"):
        if not data.get("protocol_hash") or not data.get("settings_hash"):
            raise ValueError("versioned run requires protocol_hash and settings_hash")
        return {key: data.get(key) for key in (
            "benchmark_version", "protocol_hash", "settings_hash", "protocol_status",
        )}
    data_hash = data.get("data_hash")
    return {
        "benchmark_version": f"legacy-{data_hash[:12]}" if isinstance(data_hash, str) and data_hash else "legacy-unknown",
        "protocol_hash": None, "settings_hash": None, "protocol_status": "unverified",
    }
