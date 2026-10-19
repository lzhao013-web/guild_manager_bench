"""按回合比较正式回放中的战力与资源，不重算游戏或评分。"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
import json
from math import isfinite
from pathlib import Path
from typing import Any, Callable

from rich.console import Group
from rich.panel import Panel
from rich.text import Text


METRICS = {
    "balance": (("rank_score", "战力 Rank Score"), ("gold", "金币余额"), ("experience_pool", "未分配经验池")),
    "income": (("rank_score", "战力 Rank Score"), ("gold_income", "累计讨伐金币收入"), ("experience_income", "累计讨伐经验收入")),
}
COLORS = {"medium": ("cyan", "#007c91", "m"), "high": ("green", "#287d36", "h"), "xhigh": ("magenta", "#9a38a3", "x")}


def _curve_style(run_name: str) -> tuple[str, str, str]:
    """原档位名及模型-档位任务名共用对应档位的配色，完整名称保留在图例中。"""
    return COLORS[run_name.rsplit("-", 1)[-1]]


@dataclass(frozen=True)
class CurvePoint:
    turn: int
    rank_score: float | None
    gold: float | None
    experience_pool: float | None
    gold_income: float | None
    experience_income: float | None


@dataclass(frozen=True)
class RunCurves:
    effort: str
    status: str
    max_turns: int
    points: tuple[CurvePoint, ...]


def extract_curves(replay: dict[str, Any], effort: str) -> RunCurves:
    if replay.get("kind") != "llm_replay":
        raise ValueError("曲线要求 llm_replay 格式的回放")
    completed = [turn for turn in replay["turns"] if turn["status"] == "completed"]
    numbers = [turn["turn"] for turn in completed]
    if numbers != sorted(set(numbers)):
        raise ValueError("已完成回合必须按回合数递增且不能重复")
    max_turns = replay["final_observation"]["max_turns"]
    # 原生续跑重写历史时可能丢失结算快照，但保留下一回合准备前的 observation_before。
    # 下一回合开局尚无操作，其余额与上回合结算后相同。不能使用可能已被操作改变的 final_observation。
    next_openings = {turn["turn"] - 1: turn["observation_before"] for turn in replay["turns"]
                     if isinstance(turn.get("observation_before"), dict)}
    gold_income: float | None = 0
    experience_income: float | None = 0
    previous_turn = 0
    points = []
    for turn in completed:
        number = turn["turn"]
        if number != previous_turn + 1:
            gold_income = experience_income = None
        previous_turn = number
        settlements = [step for step in turn["steps"]
                       if step.get("type") == "tool_result" and step.get("name") == "end_turn"
                       and (step.get("result", {}).get("ok") is True
                            or str(step.get("content", "")).lstrip().startswith(("成功 ", "OK ")))]
        if len(settlements) > 1:
            raise ValueError(f"T{number} 包含多条成功结算，无法确定曲线口径")
        settlement = settlements[0] if settlements else {}
        # 旧回放可能没有状态快照或结构化奖励。缺失保持未知，不从正文反推。
        observation = settlement.get("observation_after") or next_openings.get(number, {})
        battles = settlement.get("result", {}).get("turn_result", {}).get("battles")
        gold_income = _accumulate(gold_income, battles, "gold")
        experience_income = _accumulate(experience_income, battles, "experience")
        points.append(CurvePoint(
            number, _number(turn.get("rank_score")), _number(observation.get("gold")),
            _number(observation.get("experience_pool")), gold_income, experience_income,
        ))
    return RunCurves(effort, replay["status"], max_turns, tuple(points))


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


def _accumulate(previous: float | None, battles: list[dict] | None, resource: str) -> float | None:
    if previous is None or battles is None:
        return None
    values = [_number(battle.get("reward", {}).get(resource)) for battle in battles]
    if any(value is None for value in values):
        return None
    return previous + sum(values)


@dataclass
class CurveMonitor:
    read_json: Callable
    runs: dict[str, RunCurves] = field(default_factory=dict)
    warnings: dict[str, str] = field(default_factory=dict)
    _signatures: dict[str, tuple[Path, int, int]] = field(default_factory=dict)

    def refresh(self, rows: list[dict]) -> None:
        for row in rows:
            effort = row["effort"]
            self.warnings.pop(effort, None)
            if not row.get("archive_dir"):
                self.warnings[effort] = "等待创建回放存档"
                continue
            path = Path(row["archive_dir"]) / "replay.json"
            if effort in self._signatures and self._signatures[effort][0] != path:
                # 新存档不能沿用旧存档的曲线。
                self.runs.pop(effort, None)
                self._signatures.pop(effort)
            try:
                stat = path.stat()
                signature = (path, stat.st_mtime_ns, stat.st_size)
                if self._signatures.get(effort) != signature:
                    run = extract_curves(self.read_json(path), effort)
                    self.runs[effort] = run
                    self._signatures[effort] = signature
            except (OSError, ValueError) as exc:
                state = "保留上次曲线" if effort in self.runs else "暂无曲线"
                self.warnings[effort] = f"回放暂不可读或格式不符（{type(exc).__name__}），{state}"
            if row.get("monitor_warning"):
                self.warnings[effort] = self.warnings.get(effort, "") + " " + row["monitor_warning"]


def segments(run: RunCurves, metric: str) -> list[list[tuple[int, float]]]:
    """只连接连续且有值的回合，不跨越缺失值或尚未发生的回合。"""
    groups: list[list[tuple[int, float]]] = []
    previous = None
    for point in run.points:
        value = getattr(point, metric)
        if value is None:
            previous = None
            continue
        if previous is None or point.turn != previous + 1:
            groups.append([])
        groups[-1].append((point.turn, value))
        previous = point.turn
    return groups


def terminal_curves(batch: Path, rows: list[dict], monitor: CurveMonitor, *, resource_mode: str,
                    metric: str = "all", width: int = 100):
    runs = [monitor.runs[row["effort"]] for row in rows if row["effort"] in monitor.runs]
    max_turns = max((run.max_turns for run in runs), default=35)
    parts: list = [Text(f"{batch.name} · 回合曲线比较", style="bold")]
    legend = Text()
    for row in rows:
        effort = row["effort"]
        style, _, symbol = _curve_style(effort)
        run = monitor.runs.get(effort)
        turns = len(run.points) if run else 0
        legend.append(f"{symbol} {effort} 已结算{turns}回合  ", style=style)
    parts.append(legend)
    for key, title in selected_metrics(resource_mode, metric):
        lines = _terminal_plot(runs, key, max_turns, width=max(12, min(140, width - 20)),
                               height=7 if metric == "all" else 12)
        parts.append(Panel(Group(*lines), title=title, expand=False))
    for row in rows:
        effort = row["effort"]
        if effort in monitor.warnings:
            parts.append(Text(f"{effort}：{monitor.warnings[effort]}", style="yellow"))
    parts.append(Text(
        "横轴为已结束的回合。只画实测点，不补零、不外推，缺失处断线。\n"
        + ("战力为每回合已记录的实际评分，尚未完成评分的回合保持空缺。" if metric == "power" else
           "资源为结算及恢复后的金币余额、未分配经验池，不代表累计产出。" if resource_mode == "balance" else
           "资源为累计胜利讨伐奖励，含已生效天赋加成，不扣消费或经验分配。")
        + "\n同格曲线交叠显示 *。--metric 可单独展开，--output 可导出完整SVG。Ctrl+C只关闭监控。", style="dim"))
    return Group(*parts)


def selected_metrics(resource_mode: str, metric: str) -> list[tuple[str, str]]:
    choices = METRICS[resource_mode]
    indexes = {"power": 0, "gold": 1, "experience": 2}
    return list(choices) if metric == "all" else [choices[indexes[metric]]]


def _terminal_plot(runs: list[RunCurves], metric: str, max_turns: int, *, width: int, height: int) -> list[Text]:
    upper = _upper_bound(runs, metric)
    # 同一格保留所有曲线归属，不让绘制顺序掩盖其他run。
    cells = [[set() for _ in range(width)] for _ in range(height)]
    for run in runs:
        for group in segments(run, metric):
            coords = [(_x(turn, max_turns, width - 1), round((1 - value / upper) * (height - 1)))
                      for turn, value in group]
            for i, (x, y) in enumerate(coords):
                cells[y][x].add(run.effort)
                if i:
                    px, py = coords[i - 1]
                    steps = max(abs(x - px), abs(y - py))
                    for step in range(steps + 1):
                        fraction = step / max(1, steps)
                        cells[round(py + (y - py) * fraction)][round(px + (x - px) * fraction)].add(run.effort)
    lines = []
    for row, entries in enumerate(cells):
        line = Text(f"{_label(upper * (height - 1 - row) / (height - 1)):>8} │")
        for owners in entries:
            if not owners:
                line.append(" ")
            elif len(owners) > 1:
                line.append("*", style="bold white")
            else:
                style, _, symbol = _curve_style(next(iter(owners)))
                line.append(symbol, style=style)
        lines.append(line)
    lines.append(Text("         └" + "─" * width))
    ticks = [" "] * width
    for turn in sorted({1, max_turns, *range(5, max_turns, 5)}):
        text = str(turn)
        start = min(width - len(text), max(0, _x(turn, max_turns, width - 1) - len(text) // 2))
        for i, char in enumerate(text):
            ticks[start + i] = char
    lines.append(Text("          " + "".join(ticks) + " 回合"))
    if not any(getattr(point, metric) is not None for run in runs for point in run.points):
        lines.append(Text("暂无已记录数据", style="dim"))
    return lines


def render_svg(batch: Path, rows: list[dict], monitor: CurveMonitor, *, resource_mode: str,
               metric: str = "all") -> str:
    runs = [monitor.runs[row["effort"]] for row in rows if row["effort"] in monitor.runs]
    metrics = selected_metrics(resource_mode, metric)
    max_turns = max((run.max_turns for run in runs), default=35)
    height = 140 + len(metrics) * 250 + len(monitor.warnings) * 20
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="{height}" viewBox="0 0 1100 {height}">',
             '<style>text{font:14px "Microsoft YaHei",sans-serif;fill:#253044}.grid{stroke:#e4e8ef}.axis{stroke:#9aa7b7}</style>',
             f'<rect width="1100" height="{height}" fill="#fff"/>',
             _svg_text(40, 32, f"{batch.name} · 回合曲线比较")]
    for index, row in enumerate(rows):
        effort = row["effort"]
        _, color, _ = _curve_style(effort)
        run = monitor.runs.get(effort)
        x = 40 + index * 300
        parts.append(f'<path d="M{x} 62h25" stroke="{color}" stroke-width="3"/>')
        parts.append(_svg_text(x + 32, 67, f"{effort} · 已结算{len(run.points) if run else 0}回合"))
    for index, (key, title) in enumerate(metrics):
        left, top, width, plot_height = 95, 105 + index * 250, 950, 165
        upper = _upper_bound(runs, key)
        parts.append(_svg_text(40, top - 12, title))
        for tick in range(5):
            y = top + plot_height * tick / 4
            parts.append(f'<path d="M{left} {y}h{width}" class="grid"/>')
            parts.append(_svg_text(left - 10, y + 5, _label(upper * (4 - tick) / 4), anchor="end"))
        for turn in sorted({1, max_turns, *range(5, max_turns, 5)}):
            x = left + (turn - 1) * width / max(1, max_turns - 1)
            parts.append(f'<path d="M{x} {top}v{plot_height}" class="grid"/>')
            parts.append(_svg_text(x, top + plot_height + 23, str(turn), anchor="middle"))
        parts.append(_svg_text(left + width, top + plot_height + 44, "回合", anchor="end"))
        for run in runs:
            color = _curve_style(run.effort)[1]
            for group in segments(run, key):
                coords = [(left + (turn - 1) * width / max(1, max_turns - 1), top + (1 - value / upper) * plot_height)
                          for turn, value in group]
                if len(coords) > 1:
                    points = " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)
                    parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/>')
                for (turn, value), (x, y) in zip(group, coords, strict=True):
                    parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="{color}"><title>'
                                 + escape(f"{run.effort} · T{turn} · {title}: {value:g}") + '</title></circle>')
    y = 105 + len(metrics) * 250
    parts.append(_svg_text(40, y, "仅已完成回合，缺失处断线，不补零或外推。战力直接读取回放，不重新评分。"))
    note = ("金币为结算后余额，经验为结算后未分配经验池。" if resource_mode == "balance"
            else "金币与经验为累计实际讨伐奖励，含天赋加成，不扣消费。")
    parts.append(_svg_text(40, y + 22, note))
    for index, (effort, warning) in enumerate(monitor.warnings.items()):
        parts.append(_svg_text(40, y + 44 + index * 20, f"{effort}：{warning}"))
    return "\n".join(parts) + "\n</svg>\n"


def _svg_text(x: float, y: float, text: str, *, anchor: str = "start") -> str:
    return f'<text x="{x}" y="{y}" text-anchor="{anchor}">{escape(text)}</text>'


def _upper_bound(runs: list[RunCurves], metric: str) -> float:
    values = [getattr(point, metric) for run in runs for point in run.points if getattr(point, metric) is not None]
    return max(1, max(values, default=0)) * 1.05


def _x(turn: int, max_turns: int, width: int) -> int:
    return round((turn - 1) * width / max(1, max_turns - 1))


def _label(value: float) -> str:
    return f"{value / 1000:.1f}k" if value >= 10000 else f"{value:.0f}"
