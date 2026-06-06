"""多种子评估运行器：运行操作者跨多个种子并生成统计报告。"""
from __future__ import annotations

import json
import re
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from math import sqrt
from pathlib import Path
from statistics import mean, quantiles, stdev
from time import perf_counter
from typing import Any, Callable

from guild_manager_bench.bench.metrics import score_final_state
from guild_manager_bench.bench.operators.base import Operator
from guild_manager_bench.bench.runner import run_operator
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.presets import describe_data_source
from guild_manager_bench.game.state import GameDefinition


@dataclass(frozen=True, slots=True)
class EvalConfig:
    """评估运行配置。"""

    data_dir: str = "data/presets/default"
    seeds: tuple[int, ...] = tuple(range(50))
    max_steps: int = 1_000
    max_workers: int | None = None  # None = 自动（CPU 核数）
    score_waves: int | None = None  # None = 使用规则默认波次


@dataclass(frozen=True, slots=True)
class OperatorResult:
    """单个种子的评估结果。"""

    operator_name: str
    seed: int
    score: float
    rank_score: float
    duration_seconds: float
    status: str  # "completed" | "failed"
    error: str | None = None
    session_id: str | None = None
    score_data: dict[str, Any] | None = None
    final_observation: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class EvalReport:
    """多种子统计汇总。"""

    operator_name: str
    seed_count: int
    scores: tuple[float, ...]
    rank_scores: tuple[float, ...]
    mean: float
    std: float
    rank_mean: float
    rank_std: float
    min: float
    max: float
    rank_min: float
    rank_max: float
    p25: float
    p50: float
    p75: float
    p95: float
    rank_p50: float
    duration_seconds: float
    per_seed: tuple[OperatorResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator_name": self.operator_name,
            "seed_count": self.seed_count,
            "scores": list(self.scores),
            "rank_scores": list(self.rank_scores),
            "mean": round(self.mean, 4),
            "std": round(self.std, 4),
            "rank_mean": round(self.rank_mean, 4),
            "rank_std": round(self.rank_std, 4),
            "min": round(self.min, 4),
            "max": round(self.max, 4),
            "rank_min": round(self.rank_min, 4),
            "rank_max": round(self.rank_max, 4),
            "p25": round(self.p25, 4),
            "p50": round(self.p50, 4),
            "p75": round(self.p75, 4),
            "p95": round(self.p95, 4),
            "rank_p50": round(self.rank_p50, 4),
            "duration_seconds": round(self.duration_seconds, 4),
            "per_seed": [_result_to_dict(r) for r in self.per_seed],
        }


def _result_to_dict(result: OperatorResult) -> dict[str, Any]:
    return {
        "operator_name": result.operator_name,
        "seed": result.seed,
        "score": round(result.score, 4),
        "rank_score": round(result.rank_score, 4),
        "duration_seconds": round(result.duration_seconds, 4),
        "status": result.status,
        "error": result.error,
    }


def run_single_eval(
    operator_factory: Callable[[int], Operator],
    definition: GameDefinition,
    seed: int,
    *,
    max_steps: int = 1_000,
    score_waves: int | None = None,
) -> OperatorResult:
    """运行单个种子的评估。"""

    from guild_manager_bench.game.state import GameRules

    # 用指定种子创建新的游戏定义
    seeded_definition = replace(
        definition,
        rules=replace(definition.rules, seed=seed),
    )
    operator = operator_factory(seed)
    started = perf_counter()
    try:
        session = run_operator(seeded_definition, operator, max_steps=max_steps)
        assert session.state is not None
        report = score_final_state(
            seeded_definition, session.state,
            waves=score_waves,
        )
        duration = perf_counter() - started
        return OperatorResult(
            operator_name=type(operator).__name__,
            seed=seed,
            score=report.score,
            rank_score=report.rank_score,
            duration_seconds=round(duration, 4),
            status="completed",
            session_id=session.session_id,
            score_data=report.to_dict(),
            final_observation=session.observation(),
        )
    except Exception as exc:
        duration = perf_counter() - started
        return OperatorResult(
            operator_name=type(operator).__name__,
            seed=seed,
            score=0.0,
            rank_score=0.0,
            duration_seconds=round(duration, 4),
            status="failed",
            error=str(exc),
        )


def _run_single_eval_worker(args: tuple[Any, ...]) -> OperatorResult:
    """ProcessPoolExecutor 的工作函数。"""

    factory, definition, seed, max_steps, score_waves = args
    return run_single_eval(factory, definition, seed, max_steps=max_steps, score_waves=score_waves)


def run_eval_suite(
    operators: dict[str, Callable[[int], Operator]],
    config: EvalConfig | None = None,
) -> dict[str, EvalReport]:
    """对多个操作者运行完整评估套件。"""

    if config is None:
        config = EvalConfig()

    definition = load_game_definition(config.data_dir)
    results: dict[str, EvalReport] = {}

    for name, factory in operators.items():
        per_seed = _run_operator_eval(factory, definition, config)
        report = _build_report(name, per_seed)
        results[name] = report

    return results


def _run_operator_eval(
    factory: Callable[[int], Operator],
    definition: GameDefinition,
    config: EvalConfig,
) -> list[OperatorResult]:
    """运行单个操作者的所有种子评估。"""

    if config.max_workers == 1:
        # 单进程模式（方便调试）
        return [
            run_single_eval(
                factory, definition, seed,
                max_steps=config.max_steps,
                score_waves=config.score_waves,
            )
            for seed in config.seeds
        ]

    # 多进程模式
    tasks = [
        (factory, definition, seed, config.max_steps, config.score_waves)
        for seed in config.seeds
    ]
    started = perf_counter()
    per_seed: list[OperatorResult] = []

    with ProcessPoolExecutor(max_workers=config.max_workers) as executor:
        futures = [executor.submit(_run_single_eval_worker, task) for task in tasks]
        for future in futures:
            per_seed.append(future.result())

    return per_seed


def _build_report(name: str, per_seed: list[OperatorResult]) -> EvalReport:
    """从种子结果构建统计报告。"""

    completed = [r for r in per_seed if r.status == "completed"]
    scores = tuple(r.score for r in completed)
    rank_scores = tuple(r.rank_score for r in completed)

    if len(scores) >= 2:
        q = quantiles(scores, n=20)
        p25 = q[4]
        p50 = q[9]
        p75 = q[14]
        p95 = q[18]
        std_val = stdev(scores)
    elif len(scores) == 1:
        p25 = p50 = p75 = p95 = scores[0]
        std_val = 0.0
    else:
        p25 = p50 = p75 = p95 = 0.0
        std_val = 0.0

    if len(rank_scores) >= 2:
        rank_p50 = quantiles(rank_scores, n=4)[1]
        rank_std_val = stdev(rank_scores)
    elif len(rank_scores) == 1:
        rank_p50 = rank_scores[0]
        rank_std_val = 0.0
    else:
        rank_p50 = 0.0
        rank_std_val = 0.0

    total_duration = sum(r.duration_seconds for r in per_seed)

    return EvalReport(
        operator_name=name,
        seed_count=len(per_seed),
        scores=scores,
        rank_scores=rank_scores,
        mean=mean(scores) if scores else 0.0,
        std=std_val,
        rank_mean=mean(rank_scores) if rank_scores else 0.0,
        rank_std=rank_std_val,
        min=min(scores) if scores else 0.0,
        max=max(scores) if scores else 0.0,
        rank_min=min(rank_scores) if rank_scores else 0.0,
        rank_max=max(rank_scores) if rank_scores else 0.0,
        p25=p25,
        p50=p50,
        p75=p75,
        p95=p95,
        rank_p50=rank_p50,
        duration_seconds=total_duration,
        per_seed=tuple(per_seed),
    )


def save_eval_results(
    results: dict[str, EvalReport],
    path: str | Path,
    *,
    config: EvalConfig | None = None,
    leaderboard_dir: str | Path | None = None,
) -> None:
    """保存评估结果，并可同时写出 leaderboard 专用 replay。"""

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_preset": str(config.data_dir) if config else "unknown",
        "config": {
            "seeds": list(config.seeds) if config else [],
            "max_steps": config.max_steps if config else 1000,
            "max_workers": config.max_workers if config else None,
        },
        "results": {
            name: report.to_dict()
            for name, report in results.items()
        },
    }

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    if leaderboard_dir is not None:
        save_leaderboard_replays(results, leaderboard_dir, config=config)


def save_leaderboard_replays(
    results: dict[str, EvalReport],
    directory: str | Path,
    *,
    config: EvalConfig | None = None,
) -> list[Path]:
    """将每个成功的基准方法运行保存为 leaderboard 专用 replay。"""

    output_dir = Path(directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).isoformat()
    data_metadata = _leaderboard_data_metadata(config)
    written: list[Path] = []

    for name, report in results.items():
        operator_slug = _filename_slug(name)
        for result in report.per_seed:
            if (
                result.status != "completed"
                or result.score_data is None
                or result.final_observation is None
            ):
                continue

            replay_data = dict(data_metadata)
            replay_data["game_seed"] = result.seed
            replay_data["scoring_seed"] = result.score_data.get("seed")
            replay = {
                "schema_version": 1,
                "kind": "baseline_replay",
                "created_at": created_at,
                "updated_at": created_at,
                "session_id": result.session_id,
                "status": "completed",
                "baseline": {
                    "operator": name,
                    "implementation": result.operator_name,
                },
                "data": replay_data,
                "turns": [],
                "final_observation": result.final_observation,
                "score": result.score_data,
                "stats": {
                    "timing": {
                        "total_duration_seconds": result.duration_seconds,
                    },
                },
            }
            path = output_dir / f"baseline-{operator_slug}-seed-{result.seed}.json"
            path.write_text(
                json.dumps(replay, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            written.append(path)

    return written


def _leaderboard_data_metadata(config: EvalConfig | None) -> dict[str, Any]:
    if config is None:
        return {}
    try:
        return describe_data_source(config.data_dir)
    except (OSError, ValueError):
        return {"data_dir": str(config.data_dir)}


def _filename_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "operator"


def print_eval_summary(results: dict[str, EvalReport]) -> None:
    """打印评估结果的摘要表格。"""

    print(f"\n{'操作者':<25} {'种子数':>6} {'均值':>8} {'标准差':>8} {'最小':>8} {'中位':>8} {'最大':>8} {'P95':>8} {'段位中位':>10}")
    print("-" * 100)
    for name, report in results.items():
        print(
            f"{report.operator_name:<25} "
            f"{report.seed_count:>6} "
            f"{report.mean:>8.2f} "
            f"{report.std:>8.2f} "
            f"{report.min:>8.2f} "
            f"{report.p50:>8.2f} "
            f"{report.max:>8.2f} "
            f"{report.p95:>8.2f} "
            f"{report.rank_p50:>10.2f}"
        )
