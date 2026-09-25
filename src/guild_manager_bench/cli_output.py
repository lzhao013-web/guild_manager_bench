from __future__ import annotations

import json
import re
import shlex
import sys
import traceback
from collections import Counter
from time import perf_counter
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from guild_manager_bench.bench.llm.runner import _compute_run_stats
from guild_manager_bench.bench.llm.trace import LlmGameRun
from guild_manager_bench.game.state import MATERIAL_NAMES


DisplayMode = Literal["watch", "verbose", "debug", "quiet", "json"]
_TOOL_NAMES = {
    "get_party": "查看队伍", "get_monsters": "查看讨伐目标",
    "get_crafting": "查看制作配方", "get_inventory": "查看装备库存",
    "get_upgrades": "查看公会升级", "get_recruitment": "查看招募候选",
    "get_events": "查看事件", "craft_equipment": "制作装备",
    "purchase_upgrade": "升级公会", "allocate_experience": "分配经验",
    "recruit_adventurer": "招募冒险者", "dismiss_adventurer": "解散冒险者",
    "equip_item": "穿戴装备", "unequip_item": "卸下装备",
    "preview_battle": "模拟对战", "preview_team_power": "预览队伍评分",
    "write_memo": "保存备忘录", "end_turn": "安排讨伐并结束回合",
}
_FAILURES = {
    "empty_response_limit": "模型连续未调用工具",
    "model_step_limit": "模型请求次数达到回合上限",
    "end_turn_attempt_limit": "结束回合连续失败",
    "empty_response": "模型未调用工具",
    "end_turn_failed": "结束回合失败",
    "budget_exhausted": "工具预算已用尽，等待模型结束回合",
}
_STREAM_EVENTS = {"model_delta", "model_reasoning_delta", "tool_call_delta"}
_TERMINAL_ESCAPE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def format_duration(seconds: float) -> str:
    if seconds >= 3600:
        return f"{int(seconds // 3600)}h {int(seconds % 3600 // 60)}m {seconds % 60:.1f}s"
    if seconds >= 60:
        return f"{int(seconds // 60)}m {seconds % 60:.1f}s"
    return f"{seconds:.1f}s" if seconds >= 1 else f"{seconds * 1000:.0f}ms"


def format_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


def _usage_value(usage: Mapping[str, Any], key: str, alias: str) -> int | None:
    value = usage.get(key)
    if not isinstance(value, (int, float)):
        value = usage.get(alias)
    return int(value) if isinstance(value, (int, float)) else None


def _resource_spent(run: LlmGameRun, resource: str, tool_names: set[str]) -> int | None:
    """只统计指定操作的资源支出。旧存档缺少状态快照时不推算。"""
    spent = 0
    for turn in run.turns:
        before = turn.observation_before
        for call in turn.tool_calls:
            if call.ok is False:
                continue
            after = call.result.get("_observation_after")
            if call.name in tool_names:
                if before is None or after is None:
                    return None
                spent += max(0, before[resource] - after[resource])
            if after is not None:
                before = after
    return spent


class RunReporter:
    """消费运行事件。所有展示状态独立于游戏、模型消息和工具预算。"""

    def __init__(
        self, console: Console, *, mode: DisplayMode = "watch", provider: str,
        show_model_text: bool = False, show_reasoning: bool = False,
    ) -> None:
        self.console = console
        self.mode = mode
        self.provider = provider
        self.verbose = mode in {"verbose", "debug"}
        self.show_model_text = show_model_text
        self.show_reasoning = show_reasoning
        self.settings: dict[str, Any] = {}
        self.resume_base_url: str | None = None
        self.config: dict[str, Any] = {}
        self.data: dict[str, Any] = {}
        self.session_id: str | None = None
        self.archive_dir: str | None = None
        self.checkpoint_written = False
        self.resumed = False
        self.restored_completed = 0
        self.completed = 0
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.usage_requests = 0
        self.retries: Counter[str] = Counter()
        self.observation: dict[str, Any] = {}
        self.turn_before: dict[str, Any] = {}
        self.turn = 0
        self.turn_started = perf_counter()
        self.turn_requests = 0
        self.turn_tools = 0
        self.turn_failures = 0
        self.budget_used = 0
        self.prev_score: float | None = None
        self.first_turn = True
        self.phase = "正在初始化"
        self.phase_started = perf_counter()
        self.checkpoint_phase = (self.phase, self.phase_started)
        self.stream_tail = ""
        self.secrets: tuple[str, ...] = ()
        self.live: Live | None = None
        self.spinner = Spinner("line", style="cyan")

    def configure(self, config: Any, *, streaming: bool) -> None:
        # 白名单避免将 API Key、URL 中的凭证或 extra_body 打印到配置面板。
        self.settings = {
            name: getattr(config, name)
            for name in ("model", "timeout", "reasoning_effort", "thinking", "effort")
            if hasattr(config, name)
        }
        self.settings["streaming"] = streaming
        key = config.api_key
        self.secrets = (key,) if key else ()
        url = urlsplit(config.base_url)
        if not (url.username or url.password or url.query or url.fragment or (key and key in config.base_url)):
            self.resume_base_url = config.base_url

    def __enter__(self) -> RunReporter:
        if self.console.is_terminal and not self.console.is_dumb_terminal and self.mode not in {"quiet", "json"}:
            self.live = Live(
                console=self.console, get_renderable=self._live_renderable,
                refresh_per_second=4, transient=True,
                redirect_stdout=False, redirect_stderr=False,
            )
            self.live.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None

    def _clean(self, value: Any) -> str:
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, "[已隐藏]")
        text = _CONTROL.sub("", _TERMINAL_ESCAPE.sub("", text))
        return text.encode(self.console.encoding, errors="replace").decode(self.console.encoding)

    def _text(self, value: Any, style: str = "") -> Text:
        return Text(self._clean(value), style=style)

    def _print(self, value: Any = "") -> None:
        if self.mode not in {"quiet", "json"}:
            self.console.print(value if not isinstance(value, str) else self._text(value))

    def _stage(self, phase: str, *, announce: bool = False) -> None:
        self.phase = phase
        self.phase_started = perf_counter()
        if announce and self.live is None:
            self._print(self._text(phase, "dim"))

    def _live_renderable(self) -> Panel:
        status = Text.assemble(
            self._text(self.phase, "cyan"),
            (f"  {format_duration(perf_counter() - self.phase_started)}", "dim"),
        )
        progress = f"已完成 {self.completed}/{self.observation.get('max_turns', '?')} 回合"
        tokens = (
            f"输入 {format_tokens(self.input_tokens)} / 输出 {format_tokens(self.output_tokens)} Token"
            if self.usage_requests else "Token 等待 API 提供"
        )
        detail = self._text(
            f"{progress}  ·  本次请求 {self.requests} 次  ·  {tokens}\n"
            f"本回合非 end_turn 预算 {self.budget_used}/{self.config.get('max_tool_calls_per_turn', '?')}",
            "dim",
        )
        self.spinner.update(text=status)
        items: list[Any] = [self.spinner, detail]
        if self.observation:
            items.append(ProgressBar(total=self.observation["max_turns"], completed=self.completed))
        if self.stream_tail:
            items.append(self._text(self.stream_tail, "dim"))
        return Panel(Group(*items), border_style="dim", padding=(0, 1))

    def _table(self, title: str, columns: tuple[str, ...], rows: list[tuple[Any, ...]]) -> Table:
        table = Table(
            title=self._text(title, "bold"), box=box.SIMPLE, expand=False,
            header_style="bold cyan", padding=(0, 1), safe_box=True,
        )
        for column in columns:
            table.add_column(column, overflow="fold")
        for row in rows:
            table.add_row(*(value if isinstance(value, Text) else self._text(value) for value in row))
        return table

    def _fields(self, title: str, rows: list[tuple[str, Any]]) -> Panel:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim")
        table.add_column(overflow="fold")
        for label, value in rows:
            table.add_row(self._text(label), value if isinstance(value, Text) else self._text(value))
        return Panel(table, title=self._text(title, "bold"), border_style="cyan")

    def on_event(self, event: dict[str, Any]) -> None:
        kind = event["type"]
        if self.mode == "debug" and kind not in _STREAM_EVENTS and not kind.startswith("checkpoint_"):
            self._print(Panel(self._text(json.dumps(event, ensure_ascii=False, indent=2)), title=self._text(kind), border_style="dim"))

        if kind == "run_started":
            self.config = event["config"]
            self.data = event["data"]
            self.observation = event["observation"]
            self.session_id = event["session_id"]
            self._print(self._fields("Guild Manager Bench · 公会经营实况", [
                ("模型", f"{self.provider} / {self.settings['model']}"),
                ("数据", self.data.get("preset") or self.data["data_dir"]),
                ("随机种子", f"游戏 {self.data['game_seed']} / 评分 {self.data['scoring_seed']}"),
                ("回合", self.observation["max_turns"]),
                ("工具预算", f"每回合 {self.config['max_tool_calls_per_turn']} 次，不含 end_turn"),
                ("模型设置", self._model_settings()),
                ("会话", self.session_id),
            ]))
        elif kind in {"run_archived", "run_resumed"}:
            self.archive_dir = event["archive"]["directory"]
            self._print(self._text(f"存档 · {self.archive_dir}", "dim"))
            if kind == "run_resumed":
                self.resumed = True
                stats = event["stats"]
                self.restored_completed = stats["model_interaction"]["total_turns_completed"]
                self.completed = self.restored_completed
                self.input_tokens = stats["token_usage"]["input_tokens"]
                self.output_tokens = stats["token_usage"]["output_tokens"]
                self.usage_requests = int(bool(self.input_tokens or self.output_tokens))
                self.prev_score = event["last_rank_score"]
                self.checkpoint_written = True
                self._print(self._text(
                    f"续跑 · 已完成 {self.completed} 回合，恢复到第 {event['restored_observation']['turn']} 回合"
                    f" · 历史失败尝试 {stats['model_interaction']['total_turns_failed']} 次", "yellow",
                ))
        elif kind == "turn_started":
            self._start_turn(event["observation"])
        elif kind == "model_request":
            self.requests += 1
            self.turn_requests += 1
            self.stream_tail = ""
            self._stage(f"正在等待模型响应 · 本回合请求 {event['step']}", announce=True)
        elif kind in _STREAM_EVENTS:
            if self.phase.startswith("正在等待模型"):
                self.phase = "正在接收模型输出"
            show = (kind == "model_delta" and self.show_model_text) or (kind == "model_reasoning_delta" and self.show_reasoning)
            if show:
                self.stream_tail = (self.stream_tail + event.get("text", ""))[-240:]
        elif kind == "model_response":
            self._model_response(event)
        elif kind == "tool_call":
            self._stage(f"正在执行 · {_TOOL_NAMES.get(event['name'], event['name'])}")
            if self.verbose and self.mode != "debug":
                self._print(self._text(f"{event['name']}  {json.dumps(event['arguments'], ensure_ascii=False)}", "dim"))
        elif kind == "tool_result":
            self._tool_result(event)
        elif kind == "turn_completed":
            self._stage("回合已结算，等待评分")
        elif kind == "scoring_started":
            self._stage("正在计算终局 Arena 评分" if event["scope"] == "final" else "正在计算回合评分", announce=True)
        elif kind == "turn_scored":
            self.completed += 1
            self._complete_turn(event)
        elif kind == "turn_failed":
            reason = event["trace"]["failure_reason"]
            self._print(self._text(f"失败 · 第 {event['trace']['turn']} 回合尝试 · {_FAILURES.get(reason, reason)}", "red"))
            self._stage("本次回合尝试失败")
        elif kind in {"retry", "turn_retry"}:
            reason = event["reason"]
            self.retries[reason] += 1
            count = f" {event['retry_count']}/{event['total_allowed']}" if kind == "turn_retry" else ""
            self._print(self._text(f"重试{count} · {_FAILURES.get(reason, reason)}", "yellow"))
            self._stage("正在准备重试")
        elif kind == "checkpoint_started":
            self.checkpoint_phase = (self.phase, self.phase_started)
            self._stage("正在写入存档")
        elif kind in {"checkpoint_completed", "checkpoint_failed"}:
            self.phase, self.phase_started = self.checkpoint_phase
            if kind == "checkpoint_completed":
                self.checkpoint_written = True
            else:
                self._print(self._text(f"警告 · 本次存档未更新：{event['error']}", "yellow"))
        elif kind in {"run_completed", "run_failed"}:
            self.close()

    def _model_settings(self) -> str:
        parts = ["流式" if self.settings["streaming"] else "非流式", f"超时 {self.settings['timeout']:g}s"]
        effort = self.settings.get("reasoning_effort") or self.settings.get("effort")
        parts.append(f"推理强度 {effort}" if effort else "推理强度由服务端决定")
        thinking = self.settings.get("thinking")
        if thinking is not None:
            parts.append("thinking 开启" if thinking else "thinking 关闭")
        return " · ".join(parts)

    def _start_turn(self, observation: dict[str, Any]) -> None:
        self.observation = observation
        self.turn_before = observation
        self.turn = observation["turn"]
        self.turn_started = perf_counter()
        self.turn_requests = self.turn_tools = self.turn_failures = self.budget_used = 0
        self._print()
        self._print(Panel(self._text(
            f"金币 {observation['gold']}    经验池 {observation['experience_pool']}    "
            f"队伍 {observation['party_size']}/{observation['party_size_limit']}    "
            f"讨伐目标 {len(observation['monsters'])}"
        ), title=f"第 {self.turn} / {observation['max_turns']} 回合", border_style="cyan"))
        if self.first_turn or self.verbose:
            self._print(self._party(observation, "当前队伍"))
        if self.verbose:
            self._print(self._table("可讨伐目标", ("名称", "等级", "生命", "攻击/防御", "奖励"), [
                (m["name"], m["tier"], m["stats"]["hp"], f"{m['stats']['attack']}/{m['stats']['defense']}", self._reward(m["reward"]))
                for m in observation["monsters"]
            ]))
        self.first_turn = False
        self._stage("正在准备回合")

    def _party(self, observation: Mapping[str, Any], title: str) -> Any:
        equipment = {e["instance_id"]: e["name"] for e in observation["equipment_inventory"]}
        members = observation["adventurers"]
        if not members:
            return self._text(f"{title} · 暂无冒险者", "dim")
        rows = []
        for member in members:
            stats, resources = member["effective_stats"], member["resources"]
            gear = "、".join(equipment[item["instance_id"]] for item in member["equipment"]) or "无"
            rows.append((member["name"], f"Lv.{member['level']}", f"{resources['current_hp']}/{stats['hp']}", gear))
        return self._table(title, ("冒险者", "等级", "生命", "装备"), rows)

    def _model_response(self, event: dict[str, Any]) -> None:
        usage = event["usage"]
        inp = _usage_value(usage, "input_tokens", "prompt_tokens")
        out = _usage_value(usage, "output_tokens", "completion_tokens")
        if inp is not None or out is not None:
            self.usage_requests += 1
        self.input_tokens += inp or 0
        self.output_tokens += out or 0
        tokens = f"输入 {format_tokens(inp) if inp is not None else '未提供'} / 输出 {format_tokens(out) if out is not None else '未提供'} Token"
        self._print(self._text(f"模型请求 {event['step']} · {format_duration(event['timing']['duration_ms'] / 1000)} · {tokens}", "dim"))
        if self.mode != "debug":
            if self.show_model_text and event["text"]:
                self._print(Panel(self._text(event["text"]), title="模型正文", border_style="dim"))
            reasoning = event["assistant_metadata"].get("reasoning_content")
            if self.show_reasoning and reasoning:
                self._print(Panel(self._text(reasoning), title="API 推理文本 / 摘要", border_style="dim"))
        self.stream_tail = ""
        self._stage("正在处理模型响应")

    def _tool_result(self, event: dict[str, Any]) -> None:
        name, result = event["name"], event["result"]
        self.turn_tools += 1
        self.budget_used = result["tool_budget"]["used"]
        title = _TOOL_NAMES.get(name, name)
        if result.get("ok") is False:
            self.turn_failures += 1
            self._print(self._text(f"失败 · {title} · {result['error']}", "red"))
        elif name == "end_turn":
            self._battles(result["turn_result"]["battles"])
            self._turn_resources(result["_observation_after"])
        else:
            summary = result.get("event", {}).get("summary") or self._query_summary(name, result)
            self._print(self._text(f"完成 · {summary or title}", "dim" if name.startswith("get_") else "green"))
            self._changes(result)
        if self.verbose and self.mode != "debug":
            self._print(Panel(self._text(event["content"]), title="工具返回", border_style="dim"))
        if "_observation_after" in result:
            self.observation = result["_observation_after"]
        self._stage("正在处理工具结果")

    def _query_summary(self, name: str, result: dict[str, Any]) -> str:
        label = _TOOL_NAMES.get(name, name)
        queries = {
            "get_party": ("adventurers", "位冒险者"), "get_monsters": ("monsters", "个目标"),
            "get_crafting": ("crafting_recipes", "份配方"), "get_inventory": ("equipment_inventory", "件装备"),
            "get_upgrades": ("global_upgrades", "项升级"), "get_recruitment": ("recruit_candidates", "位候选"),
            "get_events": ("events", "条事件"),
        }
        if name in queries:
            key, unit = queries[name]
            return f"{label} · {len(result[key])} {unit}"
        if name == "write_memo":
            memo = result["memo"]
            content = memo["content"].replace("\n", " ")
            return f"备忘录已保存 · 共 {memo['count']} 条 · {content[:100]}{'…' if len(content) > 100 else ''}"
        if name == "preview_battle":
            p = result["preview"]
            return f"模拟对战 · {p['adventurer_name']} → {p['monster_name']} · {'胜利' if p['won'] else '未获胜'}（非实际讨伐）"
        if name == "preview_team_power":
            return f"预览队伍评分 · Rank Score {result['rank_score']:.1f}"
        return label

    def _changes(self, result: dict[str, Any]) -> None:
        after = result.get("_observation_after", self.observation)
        names = {
            a["adventurer_id"]: a["name"]
            for obs in (self.observation, after) for a in obs["adventurers"]
        }
        if after["party_size"] != self.observation["party_size"]:
            self._print(self._text(f"  队伍人数  {self.observation['party_size']} → {after['party_size']}", "dim"))
        if "recruited_adventurer" in result:
            adventurer = result["recruited_adventurer"]
            stats = adventurer["effective_stats"]
            self._print(self._text(
                f"  Lv.{adventurer['level']} · 生命 {adventurer['resources']['current_hp']}/{stats['hp']}"
                f" · 攻击 {stats['attack']} / 防御 {stats['defense']}", "dim",
            ))
        for change in result.get("event", {}).get("changes", []):
            if change["kind"] == "turn":
                continue
            before, current = change.get("before"), change.get("after")
            if not self.verbose and change["kind"] in {"adventurer", "equipment", "upgrade"} and before is None:
                continue  # 操作标题已经包含新增对象的名字。
            label = MATERIAL_NAMES.get(change["label"], change["label"])
            if isinstance(before, str):
                before = names.get(before, before)
            if isinstance(current, str):
                current = names.get(current, current)
            delta = f" ({current - before:+g})" if isinstance(before, (int, float)) and isinstance(current, (int, float)) else ""
            values = f"{before} → {current}{delta}" if before is not None else str(current)
            self._print(self._text(f"  {label}  {values}", "dim"))

    def _reward(self, reward: Mapping[str, Any]) -> str:
        parts = [f"金币 +{reward['gold']}", f"经验 +{reward['experience']}"]
        parts.extend(f"{MATERIAL_NAMES.get(k, k)} +{v}" for k, v in reward.get("materials", {}).items() if v)
        return "  ".join(parts)

    def _battles(self, battles: list[dict[str, Any]]) -> None:
        if not battles:
            self._print(self._text("回合结束 · 本回合未安排讨伐", "dim"))
            return
        self._print(self._table("讨伐战报", ("对阵", "结果", "收益"), [
            (f"{b['adventurer_name']} → {b['monster_name']}", self._text("胜利" if b["won"] else "战败", "green" if b["won"] else "red"), self._reward(b["reward"]))
            for b in battles
        ]))
        wins = sum(bool(b["won"]) for b in battles)
        self._print(f"战斗 {wins} 胜 {len(battles) - wins} 负 · "
                    f"收入 金币 +{sum(b['reward']['gold'] for b in battles)} / 经验 +{sum(b['reward']['experience'] for b in battles)}")

    def _turn_resources(self, after: dict[str, Any]) -> None:
        before_by_id = {a["adventurer_id"]: a for a in self.observation["adventurers"]}
        for member in after["adventurers"]:
            before = before_by_id[member["adventurer_id"]]["resources"]
            current = member["resources"]
            changes = [
                f"{label} {before[key]} → {current[key]}"
                for key, label in (("current_hp", "生命"), ("current_mp", "法力"))
                if before[key] != current[key]
            ]
            if changes:
                self._print(self._text(f"  回合后（含恢复） · {member['name']} · {' / '.join(changes)}", "dim"))

    def _complete_turn(self, event: dict[str, Any]) -> None:
        score = event["rank_score"]
        score_text = f"Rank Score {score:.1f}"
        if self.prev_score is not None:
            score_text = f"Rank Score {self.prev_score:.1f} → {score:.1f} ({score - self.prev_score:+.1f})"
        score_style = ""
        if self.prev_score is not None and score != self.prev_score:
            score_style = "green" if score > self.prev_score else "red"
        self.prev_score = score
        gold = self.observation["gold"] - self.turn_before["gold"]
        xp = self.observation["experience_pool"] - self.turn_before["experience_pool"]
        self._print(self._fields(f"第 {event['turn']} 回合结算", [
            ("资源净变化", f"金币 {gold:+} / 经验池 {xp:+}"),
            ("队伍评分", self._text(score_text, score_style)),
            ("执行", f"模型请求 {self.turn_requests} 次 / 工具调用 {self.turn_tools} 次 / 失败 {self.turn_failures} 次"),
            ("耗时", f"{format_duration(perf_counter() - self.turn_started)}（含评分 {format_duration(event['duration_ms'] / 1000)}）"),
        ]))
        self._stage("回合完成")

    def resume_command(self) -> str | None:
        if not self.archive_dir or not self.checkpoint_written:
            return None
        parts = ["guild-manager", "run", "--provider", self.provider]
        if self.settings.get("model"):
            parts += ["--model", self.settings["model"]]
        if self.resume_base_url:
            parts += ["--base-url", self.resume_base_url]
        parts += ["--data-dir", self.data["data_dir"], "--resume", self.archive_dir]
        for key, flag in (("game_seed", "--game-seed"), ("scoring_seed", "--scoring-seed")):
            parts += [flag, str(self.data[key])]
        parts += ["--max-tool-calls-per-turn", str(self.config["max_tool_calls_per_turn"])]
        for key, flag in (("reasoning_effort", "--reasoning-effort"), ("effort", "--thinking-effort"), ("timeout", "--timeout")):
            if self.settings.get(key) is not None:
                parts += [flag, str(self.settings[key])]
        if self.settings.get("thinking") is not None:
            parts.append("--thinking" if self.settings["thinking"] else "--no-thinking")
        if not self.settings["streaming"]:
            parts.append("--no-stream")
        # Windows 默认给出 PowerShell 可复制的字面量参数，不拼接凭证。
        if sys.platform == "win32":
            return " ".join("'" + p.replace("'", "''") + "'" if not re.fullmatch(r"[\w./:=+-]+", p) else p for p in parts)
        return shlex.join(parts)

    def _json(self, value: dict[str, Any]) -> None:
        # 不经过 Rich，避免终端宽度折行破坏 JSON 字符串。
        self.console.file.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
        self.console.file.flush()

    def finish(self, run: LlmGameRun, *, wall_time: float) -> None:
        self.close()
        stats = _compute_run_stats(run)
        records = [r for t in run.turns for r in t.model_responses]
        reported_input = sum(_usage_value(r.usage, "input_tokens", "prompt_tokens") is not None for r in records)
        reported_output = sum(_usage_value(r.usage, "output_tokens", "completion_tokens") is not None for r in records)
        cache_usage: Counter[str] = Counter()
        for record in records:
            usage = record.usage
            # OpenAI 的缓存读数在 details 内，Anthropic 单独报告，不计算跨 provider 缓存率。
            cached = usage.get("cache_read_input_tokens")
            if cached is None:
                details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
                cached = details.get("cached_tokens") if isinstance(details, Mapping) else None
            if isinstance(cached, (int, float)):
                cache_usage["read_tokens"] += int(cached)
            created = usage.get("cache_creation_input_tokens")
            if isinstance(created, (int, float)):
                cache_usage["created_tokens"] += int(created)
        summary = {
            "schema_version": 1, "status": run.status, "session_id": run.session_id,
            "provider": self.provider, "model_settings": self.settings, "data": self.data,
            "score": run.score, "stats": stats, "final_observation": dict(run.final_observation),
            "failure_reason": run.failure_reason, "archive_dir": run.archive_dir,
            "invocation": {
                "wall_time_seconds": round(wall_time, 3), "resumed": self.resumed,
                "restored_completed_turns": self.restored_completed,
                "retry_notifications": dict(self.retries),
            },
            "usage_coverage": {"responses": len(records), "input": reported_input, "output": reported_output},
            "gold_spent": _resource_spent(run, "gold", {"craft_equipment", "purchase_upgrade", "recruit_adventurer"}),
            "experience_allocated": _resource_spent(run, "experience_pool", {"allocate_experience"}),
            "cache_usage": dict(cache_usage),
            "score_history": [{"turn": t.turn, "rank_score": t.rank_score} for t in run.turns if t.rank_score is not None],
            "resume_command": self.resume_command() if run.status != "completed" else None,
        }
        if self.mode == "json":
            self._json(summary)
            return
        self._final_report(run, summary)

    def _final_report(self, run: LlmGameRun, report: dict[str, Any]) -> None:
        stats, score = report["stats"], run.score
        actions, interaction = stats["game_actions"], stats["model_interaction"]
        completed = interaction["total_turns_completed"]
        self.console.print(self._fields("公会经营完成" if run.status == "completed" else "公会经营未完成", [
            ("状态", self._text("完成", "green") if run.status == "completed" else self._text("失败", "red")),
            ("回合", f"已完成 {completed}/{run.final_observation['max_turns']} / 失败尝试 {interaction['total_turns_failed']} 次"),
            ("Rank Score", f"{score['rank_score']:.1f}" if score is not None else "未进行终局评分"),
            ("会话", run.session_id),
        ]))
        if report["score_history"]:
            history = " → ".join(f"T{item['turn']}:{item['rank_score']:.1f}" for item in report["score_history"])
            self.console.print(self._text(f"评分轨迹 · {history}", "dim"))
        self.console.print(self._party(run.final_observation, "最终阵容"))
        if score is not None and score["rank_score_per_adventurer"]:
            self.console.print(self._table("终局 Rank Score 贡献", ("冒险者", "贡献分", "占比"), [
                (a["name"], f"{a['rank_score']:.1f}", f"{a['rank_score_share']:.1%}")
                for a in score["rank_score_per_adventurer"]
            ]))
        total = actions["battles_total"]
        battle_rows: list[tuple[str, Any]] = [
            ("实际讨伐", f"{actions['battles_won']} 胜 / {actions['battles_lost']} 负"),
            ("讨伐胜率", f"{actions['battles_won'] / total:.1%}" if total else "无讨伐"),
        ]
        strongest = actions["strongest_defeated_enemy"]
        if strongest:
            battle_rows.append(("最强击败目标", f"{strongest['name']}（按属性估算）"))
        if score is not None:
            battle_rows.append(("终局 Arena 胜率", f"{score['chosen_win_rate']:.1%}（独立模拟评分，不计入实际讨伐）"))
        self.console.print(self._fields("战斗表现", battle_rows))
        self.console.print(self._fields("经营表现", [
            ("讨伐总收入", f"金币 {actions['total_gold_earned']} / 经验 {actions['total_experience_earned']}"),
            ("金币支出", report["gold_spent"] if report["gold_spent"] is not None else "旧存档缺少快照，无法统计"),
            ("经验分配", report["experience_allocated"] if report["experience_allocated"] is not None else "旧存档缺少快照，无法统计"),
            ("最终余额", f"金币 {run.final_observation['gold']} / 经验池 {run.final_observation['experience_pool']}"),
            ("经营操作", f"招募 {actions['total_recruits']} / 制作 {actions['total_equipment_crafted']} / 升级 {actions['total_upgrades_purchased']} / 分配经验 {actions['total_experience_allocated']} 次"),
        ]))
        coverage, tokens, tools = report["usage_coverage"], stats["token_usage"], stats["tool_calls"]
        responses = coverage["responses"]
        token_text = lambda key, side: f"{format_tokens(tokens[key])}（usage {coverage[side]}/{responses} 个响应）" if coverage[side] else "API 未提供"
        model_time = stats["timing"]["total_duration_seconds"]
        performance: list[tuple[str, Any]] = [
            ("本次进程耗时", format_duration(report["invocation"]["wall_time_seconds"])),
            ("累计模型耗时", f"{format_duration(model_time)}（含续跑历史）" if self.resumed else format_duration(model_time)),
            ("模型响应", f"{responses} 次，均耗时 {format_duration(model_time / responses)}" if responses else "0 次"),
            ("输入 Token", token_text("input_tokens", "input")),
            ("输出 Token", token_text("output_tokens", "output")),
            ("工具调用", f"{tools['total']} 次 / 成功 {tools['successful']} / 失败 {tools['failed']}"),
            ("本次重试通知", f"{sum(self.retries.values())} 次（不含 provider 内部 HTTP 重试）"),
        ]
        for key, label in (("read_tokens", "缓存读取 Token"), ("created_tokens", "缓存创建 Token")):
            if key in report["cache_usage"]:
                performance.append((label, format_tokens(report["cache_usage"][key])))
        self.console.print(self._fields("模型效率 · 累计统计", performance))
        if self.verbose:
            self.console.print(self._table("工具统计", ("工具", "总数", "成功", "失败"), [
                (name, counts["total"], counts["successful"], counts["failed"])
                for name, counts in sorted(tools["by_name_detail"].items(), key=lambda pair: -pair[1]["total"])
            ]))
        failures = Counter(c.error for t in run.turns for c in t.tool_calls if c.ok is False)
        if failures:
            self.console.print(self._fields("工具失败原因", [(str(reason), f"{count} 次") for reason, count in failures.items()]))
        if run.failure_reason:
            self.console.print(self._text(f"失败原因 · {_FAILURES.get(run.failure_reason, run.failure_reason)}", "red"))
        if run.archive_dir:
            self.console.print(self._fields("存档", [("目录", run.archive_dir), ("回放", "replay.json"), ("完整调用链", "trace.jsonl")]))
        if report["resume_command"]:
            self.console.print(self._text("使用原 API 凭证和地址环境变量续跑：\n" + report["resume_command"], "yellow"))

    def abort(self, reason: str, *, interrupted: bool = False, exception: Exception | None = None) -> None:
        self.close()
        status = "interrupted" if interrupted else "failed"
        command = self.resume_command()
        if self.mode == "json":
            self._json({
                "schema_version": 1, "status": status, "failure_reason": self._clean(reason),
                "phase": self._clean(self.phase), "session_id": self.session_id,
                "archive_dir": self.archive_dir, "resume_command": command,
            })
            return
        error_console = Console(
            stderr=True, highlight=False, markup=False, force_terminal=sys.stderr.isatty(),
            color_system=self.console.color_system,
        )
        error_console.print(self._fields("运行中断" if interrupted else "运行失败", [
            ("阶段", self.phase), ("原因", reason),
            ("存档", self.archive_dir or "未创建存档"),
        ]))
        if command:
            error_console.print(self._text("从最后成功写入的存档继续，保留原 API 凭证和地址环境变量：\n" + command, "yellow"))
        if self.mode == "debug" and exception is not None:
            error_console.print(self._text("".join(traceback.format_exception(exception))))
