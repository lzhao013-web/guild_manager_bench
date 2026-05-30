"""并行评估脚本：多进程同时跑不同操作者。"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any


def _eval_single_operator(args: tuple) -> tuple[str, Any, float]:
    """在子进程中评估单个操作者。"""
    import importlib
    from guild_manager_bench.bench.eval_runner import EvalConfig, run_eval_suite

    name, class_path, seeds, max_steps = args
    module_path, class_name = class_path.rsplit(":", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)

    if class_name == "RandomFullOperator":
        factory = lambda seed: cls(seed=seed)  # noqa: E731
    else:
        factory = lambda seed: cls()  # noqa: E731

    config = EvalConfig(seeds=tuple(seeds), max_steps=max_steps, max_workers=1)
    started = time.time()
    results = run_eval_suite({name: factory}, config)
    elapsed = time.time() - started
    return name, results[name], elapsed


OPERATOR_CLASSES = {
    "RandomFull": "guild_manager_bench.bench.operators.random_full_operator:RandomFullOperator",
    "Greedy": "guild_manager_bench.bench.operators.greedy_operator:GreedyOperator",
    "Search": "guild_manager_bench.bench.operators.search_operator:SearchOperator",
}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10, help="种子数量")
    parser.add_argument("--workers", type=int, default=3, help="并行进程数")
    args = parser.parse_args()

    seeds = list(range(args.seeds))
    tasks = [(name, path, seeds, 1000) for name, path in OPERATOR_CLASSES.items()]

    started = time.time()
    raw_results: dict[str, Any] = {}
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(_eval_single_operator, task) for task in tasks]
        for future in futures:
            name, report, elapsed = future.result()
            raw_results[name] = report
            print(f"  {name}: {elapsed:.1f}s", flush=True)

    total = time.time() - started
    print(f"\nTotal wall time: {total:.1f}s ({args.workers} workers parallel)")

    print(f"\n{'Operator':<20} {'seeds':>5} {'mean':>8} {'std':>8} {'min':>8} {'p50':>8} {'max':>8}")
    print("-" * 70)
    for name in OPERATOR_CLASSES:
        r = raw_results[name]
        print(
            f"{name:<20} {r.seed_count:>5} {r.mean:>8.2f} {r.std:>8.2f} "
            f"{r.min:>8.2f} {r.p50:>8.2f} {r.max:>8.2f}"
        )


if __name__ == "__main__":
    main()
