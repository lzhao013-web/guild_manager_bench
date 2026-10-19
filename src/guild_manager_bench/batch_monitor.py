"""批次监控的只读详情视图，不参与游戏和模型请求。"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import time
from typing import Any, Callable

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from guild_manager_bench.cli_output import format_duration, format_tokens
from guild_manager_bench.game.state import MATERIAL_NAMES


_LABELS = {"starting": "启动中", "running": "运行中", "completed": "已完成", "failed": "失败",
           "interrupted": "已中断", "exited": "异常退出", "unknown": "状态待确认"}
_NEW_TOOLS = {"get_forecast": "未来查询", "get_battle_log": "战斗记录",
              "configure_tactics": "技能配置", "preview_team_power": "方案试算"}
_TOOL_LABELS = {
    **_NEW_TOOLS, "get_recruitment": "查候选", "get_inventory": "查装备",
    "get_crafting": "查配方", "get_upgrades": "查天赋", "get_events": "查事件",
    "recruit_adventurer": "招募", "dismiss_adventurer": "解雇", "craft_equipment": "制作",
    "equip_item": "穿装", "unequip_item": "卸装", "purchase_upgrade": "购天赋",
    "allocate_experience": "分经验", "preview_battle": "战斗预览", "write_memo": "备忘",
    "end_turn": "结束回合",
}
_ESCAPE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_READ_BYTES = 16 * 1024 * 1024


def _clean(value: Any, limit: int = 180) -> str:
    text = _CONTROL.sub("", _ESCAPE.sub("", str(value).replace("\n", " ").replace("\r", " ")))
    return text if len(text) <= limit else text[:limit - 1] + "…"


@dataclass
class TraceDetails:
    path: Path
    offset: int = 0
    total_bytes: int = 0
    updated_at: str | None = None
    warning: str | None = None
    current_turn: int | None = None
    state_turn: int | None = None
    observation: dict[str, Any] | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    turn_requests: int = 0
    turn_tools: int = 0
    turn_failures: int = 0
    tool_counts: Counter = field(default_factory=Counter)
    failed_tools: Counter = field(default_factory=Counter)
    retries: Counter = field(default_factory=Counter)
    scores: deque = field(default_factory=lambda: deque(maxlen=6))
    recent: deque = field(default_factory=lambda: deque(maxlen=5))
    errors: deque = field(default_factory=lambda: deque(maxlen=3))
    last_battles: list[dict[str, Any]] | None = None
    battle_turn: int | None = None
    last_response_ms: float | None = None
    active_tool: str | None = None

    @property
    def caught_up(self) -> bool:
        return self.offset == self.total_bytes and self.warning is None

    def poll(self, open_reader: Callable, *, max_bytes: int = _READ_BYTES) -> None:
        """逐行提交完整记录，保留未写完的末行，避免重复累计事件。"""
        self.warning = None
        try:
            with open_reader(self.path) as text:
                stream = text.buffer
                stream.seek(0, 2)
                self.total_bytes = stream.tell()
                if self.total_bytes < self.offset:
                    self.warning = "日志已缩短，保留原统计。请重开监控重新读取。"
                    return
                stream.seek(self.offset)
                boundary = min(self.total_bytes, self.offset + max_bytes)
                while stream.tell() < boundary:
                    # 读取本次打开时已存在的内容，不等待写入中的末行。
                    line = stream.readline(self.total_bytes - stream.tell())
                    if not line.endswith(b"\n"):
                        break
                    record = json.loads(line)
                    self.consume(record)
                    self.offset = stream.tell()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self.warning = f"详情日志暂不可读（{type(exc).__name__}），保留已读数据。"

    def consume(self, record: dict[str, Any]) -> None:
        event = record.get("event")
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        self.updated_at = record.get("created_at")
        if kind in {"run_started", "turn_started"}:
            self._observe(event["observation"])
            if kind == "turn_started":
                self.current_turn = event["observation"]["turn"]
                self.turn_requests = self.turn_tools = self.turn_failures = 0
                self.active_tool = None
                self.budget = {}
        elif kind == "model_request":
            self.turn_requests += 1
        elif kind == "model_response":
            self.last_response_ms = event["timing"]["duration_ms"]
        elif kind == "tool_call":
            self.active_tool = event["name"]
        elif kind == "tool_result":
            name, result = event["name"], event["result"]
            self.tool_counts[name] += 1
            self.turn_tools += 1
            self.active_tool = None
            self.budget = dict(result["tool_budget"])
            failed = result.get("ok") is False
            summary = _tool_summary(name, result, event.get("content", ""))
            self.recent.append({"turn": event["turn"], "name": name, "failed": failed, "summary": summary})
            if failed:
                self.failed_tools[name] += 1
                self.turn_failures += 1
                self.errors.append(f"T{event['turn']} {name}：{_clean(result.get('error', '未提供原因'))}")
            if isinstance(result.get("_observation_after"), dict):
                self._observe(result["_observation_after"])
            if name == "end_turn" and not failed:
                self.last_battles = result["turn_result"]["battles"]
                self.battle_turn = event["turn"]
        elif kind == "turn_scored":
            self.scores.append((event["turn"], event["rank_score"]))
        elif kind in {"retry", "turn_retry"}:
            self.retries[event["reason"]] += 1
            self.errors.append(f"T{event.get('turn', '?')} 重试：{_clean(event['reason'])}")
        elif kind == "turn_failed":
            trace = event["trace"]
            self.errors.append(f"T{trace['turn']} 回合失败：{_clean(trace['failure_reason'])}")
        elif kind == "checkpoint_failed":
            self.errors.append(f"存档失败：{_clean(event['error'])}")
        # 不保留模型正文、推理、请求内容或原始 API 响应。

    def _observe(self, observation: dict[str, Any]) -> None:
        equipment = {item["instance_id"]: item["name"] for item in observation["equipment_inventory"]}
        self.state_turn = observation["turn"]
        self.observation = {
            "gold": observation["gold"], "experience_pool": observation["experience_pool"],
            "materials": dict(observation["materials"]), "party_size_limit": observation["party_size_limit"],
            "party": [{
                "name": member["name"], "level": member["level"],
                "hp": member["resources"]["current_hp"], "max_hp": member["effective_stats"]["hp"],
                "mp": member["resources"]["current_mp"], "max_mp": member["effective_stats"]["mp"],
                "equipment": [equipment[item["instance_id"]] for item in member["equipment"]],
            } for member in observation["adventurers"]],
        }


def _tool_summary(name: str, result: dict[str, Any], content: str) -> str:
    if result.get("ok") is False:
        return _clean(result.get("error", "未提供原因"))
    if name == "preview_team_power" and "rank_score_delta" in result:
        return (f"试算 {result['current_rank_score']:.1f} → {result['projected_rank_score']:.1f} "
                f"({result['rank_score_delta']:+.1f})，未执行")
    if name == "get_forecast":
        return f"{result['kind']} T{result['start_turn']}—{result['end_turn']}，命中 {result['total_matches']}，返回 {len(result['items'])}"
    if name == "get_battle_log":
        source = "预览" if result["source"] == "preview" else "实战"
        return f"T{result['turn']} {source}，{result['detail']}"
    if name == "write_memo":
        return "已保存跨回合备忘录"
    if name == "preview_battle":
        preview = result["preview"]
        return _clean(f"{preview['adventurer_name']} → {preview['monster_name']}，{'胜' if preview['won'] else '未胜'}（预览）")
    summary = result.get("event", {}).get("summary")
    if summary:
        return _clean(summary)
    # 只取工具结果的第一条摘要，不展示整份查询结果。
    lines = content.splitlines()
    return _clean(lines[1] if len(lines) > 1 else "查询完成")


class DetailMonitor:
    def __init__(self, open_reader: Callable) -> None:
        self.open_reader = open_reader
        self.states: dict[str, TraceDetails] = {}

    def refresh(self, rows: list[dict], *, catch_up: bool = False) -> None:
        for row in rows:
            archive = row.get("archive_dir")
            if not archive:
                continue
            path = Path(archive) / "trace.jsonl"
            effort = row["effort"]
            detail = self.states.get(effort)
            if detail is None or detail.path != path:
                detail = self.states[effort] = TraceDetails(path)
            detail.poll(self.open_reader)
            if catch_up:
                # --once 和已结束批次追到本次已看到的文件末尾，不追赶不断追加的新内容。
                target = detail.total_bytes
                while detail.offset < target and detail.warning is None:
                    before = detail.offset
                    detail.poll(self.open_reader)
                    if detail.offset == before:
                        break


def dashboard(batch: Path, rows: list[dict], details: dict[str, TraceDetails], *, effort: str | None = None):
    overview = Table(title=Text(f"GPT-6.1 Sol · {batch.name}"), expand=True)
    for label in ("档位 / 状态", "当前 / 已完成", "最近Rank / Δ", "预算 / 本轮请求", "累计输入 / 输出", "总耗时 / 更新距今"):
        overview.add_column(label, overflow="fold")
    now = time.time()
    for row in rows:
        detail = details.get(row.get("effort"))
        rank = row.get("rank")
        delta = None
        if detail and len(detail.scores) >= 2:
            last, prev = detail.scores[-1], detail.scores[-2]
            if last[0] == row.get("completed_turns") and rank is not None and abs(last[1] - rank) < 0.01:
                delta = last[1] - prev[1]
        rank_text = "—" if rank is None else f"{rank:.1f}"
        if delta is not None:
            rank_text += f" / {delta:+.1f}"
        limit = row.get("budget_limit")
        requests = "—"
        if detail and detail.caught_up and detail.current_turn == row.get("current_turn"):
            requests = str(detail.turn_requests)
            limit = detail.budget.get("max_tool_calls", limit)
        age = "—" if row.get("updated_at") is None else format_duration(max(0, now - row["updated_at"]))
        elapsed = format_duration(max(0, row.get("finished_at", now) - row.get("started_at", now)))
        overview.add_row(
            Text(f"{row.get('effort', '?')} · {_LABELS[row['status']]}"),
            f"T{_known(row.get('current_turn'))} / {_known(row.get('completed_turns'))}/{row.get('total_turns', 35)}",
            rank_text, f"{_known(row.get('budget_used'))}/{_known(limit)} / {requests}",
            f"{_tokens(row.get('input_tokens'))} / {_tokens(row.get('output_tokens'))}", f"{elapsed} / {age}",
        )
    chosen = [row for row in rows if effort is None or row.get("effort") == effort]
    cards = Table.grid(expand=True, padding=(0, 1))
    for _ in chosen:
        cards.add_column(ratio=1)
    if chosen:
        cards.add_row(*[_detail_card(row, details.get(row.get("effort")), expanded=effort is not None) for row in chosen])
    footer = Text(
        "Rank为最近已结算回合，试算不会改动真实队伍。统计含失败调用，end_turn不占预算。\n"
        "窄窗口可加 --effort medium/high/xhigh 展开一局，--compact 返回简表。Ctrl+C只关闭监控。",
        style="dim",
    )
    return Group(overview, cards, footer)


def _detail_card(row: dict, detail: TraceDetails | None, *, expanded: bool) -> Panel:
    lines: list[Text] = []

    def add(value: str, style: str = "", *, wrap: bool = False) -> None:
        lines.append(Text(_clean(value, 600), style=style, overflow="fold" if wrap else "ellipsis", no_wrap=not wrap))

    phase = row.get("error") or row.get("phase", "等待进度")
    end = row.get("finished_at", time.time())
    phase_age = format_duration(max(0, end - row.get("phase_started_at", end)))
    add(f"{phase} · {phase_age}", "cyan", wrap=expanded)
    if row.get("monitor_warning"):
        add(row["monitor_warning"], "yellow", wrap=expanded)
    observation = detail.observation if detail else None
    if detail and detail.warning:
        add(detail.warning, "yellow", wrap=expanded)
    if detail and not detail.caught_up:
        add(f"详情读取中 {detail.offset / 1048576:.1f}/{detail.total_bytes / 1048576:.1f} MiB，以下为已读部分", "yellow", wrap=expanded)
    if detail and detail.updated_at:
        add(f"详情截至 {_timestamp_text(detail.updated_at)} · 状态T{_known(detail.state_turn)}", "dim")
    source = observation if observation is not None else row
    add(f"金币 {_known(source.get('gold'))} · 经验 {_known(source.get('experience_pool'))}")
    materials = source.get("materials")
    if materials is not None:
        material_text = "、".join(f"{MATERIAL_NAMES.get(key, key)}×{value}" for key, value in materials.items() if value) or "无"
        add(f"材料：{material_text}", wrap=expanded)
    else:
        add("材料：尚未读到状态快照", "dim")
    members = source.get("party")
    if members is None:
        add("队伍：尚无记录", "dim")
    else:
        add(f"队伍 {len(members)}/{_known(source.get('party_size_limit'))}", "bold")
        for member in members:
            text = f"{member['name']} Lv{member['level']}"
            if "hp" in member:
                text += f" HP {member['hp']}/{member['max_hp']} MP {member['mp']}/{member['max_mp']} 装{len(member['equipment'])}件"
            add(text)
            if expanded and "equipment" in member:
                add("  装备：" + ("、".join(member["equipment"]) or "无"), wrap=True)
    if detail is None or detail.updated_at is None:
        add("操作与战斗详情：等待日志", "dim")
    else:
        counts = [f"{label} {detail.tool_counts[name]}" for name, label in _NEW_TOOLS.items()]
        add(" · ".join(counts[:2]))
        add(" · ".join(counts[2:]))
        add(f"累计工具 {sum(detail.tool_counts.values())} · 失败 {sum(detail.failed_tools.values())} · 重试通知 {sum(detail.retries.values())}")
        if detail.active_tool:
            add(f"执行中：{detail.active_tool}", "cyan")
        response = "—" if detail.last_response_ms is None else format_duration(detail.last_response_ms / 1000)
        add(f"本轮工具 {detail.turn_tools} / 失败 {detail.turn_failures} · 最近响应 {response}")
        add("最近操作", "bold")
        for operation in list(detail.recent)[-(5 if expanded else 3):]:
            name = operation["name"] if expanded else _TOOL_LABELS.get(operation["name"], operation["name"])
            status = "失败" if operation["failed"] else "完成"
            add(f"T{operation['turn']} {status} {name} · {operation['summary']}",
                "red" if operation["failed"] else "", wrap=expanded)
        if detail.last_battles is not None:
            battles = detail.last_battles
            wins = sum(bool(battle["won"]) for battle in battles)
            gold = sum(battle["reward"]["gold"] for battle in battles)
            xp = sum(battle["reward"]["experience"] for battle in battles)
            add(f"T{detail.battle_turn} 实战 {wins}胜/{len(battles) - wins}未胜 · 金币+{gold} 经验+{xp}", "bold")
            if expanded:
                for battle in battles:
                    add(f"  {battle['adventurer_name']} → {battle['monster_name']}：{'胜' if battle['won'] else '未胜'}", wrap=True)
        if expanded and detail.scores:
            add("最近评分：" + " → ".join(f"T{turn} {score:.1f}" for turn, score in detail.scores), wrap=True)
        for error in list(detail.errors)[-(3 if expanded else 1):]:
            add(error, "yellow", wrap=expanded)
    if expanded and row.get("archive_dir"):
        add(f"存档：{row['archive_dir']}", "dim", wrap=True)
    return Panel(Group(*lines), title=Text(row.get("effort", "?")), border_style="cyan")


def _timestamp_text(value: str) -> str:
    # 归档使用本地时间 YYYYMMDD-HHMMSS-microseconds。
    return f"{value[9:11]}:{value[11:13]}:{value[13:15]}" if len(value) >= 15 else value


def _known(value: Any) -> str:
    return "—" if value is None else str(value)


def _tokens(value: int | None) -> str:
    return "—" if value is None else format_tokens(value)
