from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping


# ── ANSI / 格式化辅助 ──────────────────────────────────────────────────────


def _enable_ansi() -> None:
    """在 Windows 上启用 ANSI 转义序列支持。"""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass


def _format_tokens(n: int | float) -> str:
    """将 token 数量格式化为人类可读形式。"""
    if not isinstance(n, (int, float)):
        return str(n)
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.0f}k"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _format_duration(seconds: float) -> str:
    """将秒数格式化为人类可读形式。"""
    if not isinstance(seconds, (int, float)):
        return str(seconds)
    if seconds >= 60:
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}m{secs:04.1f}s"
    if seconds >= 1:
        return f"{seconds:.1f}s"
    return f"{seconds * 1000:.0f}ms"


def _model_display_name(agent: Any) -> str:
    """获取 agent 的模型显示名称。"""
    config = getattr(agent, "config", None)
    if config is not None and hasattr(config, "model"):
        return config.model or "unknown"
    return "unknown"


# ── 命令行入口 ──────────────────────────────────────────────────────────────


def main() -> None:
    """命令行入口。"""

    parser = argparse.ArgumentParser(prog="guild-manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── serve ──
    serve_parser = subparsers.add_parser("serve", help="启动可视化服务")
    serve_parser.add_argument("--data-dir", default="data")
    serve_parser.add_argument("--preset", default=None)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    # ── build-leaderboard ──
    bl_parser = subparsers.add_parser("build-leaderboard", help="构建排行榜数据")
    bl_parser.add_argument(
        "--data-dir", type=Path,
        default=Path("web/leaderboard/data"),
        help="replay JSON 文件目录 (默认: web/leaderboard/data)",
    )
    bl_parser.add_argument(
        "--output", type=Path,
        default=Path("web/leaderboard/leaderboard_data.json"),
        help="输出文件路径 (默认: web/leaderboard/leaderboard_data.json)",
    )
    bl_parser.add_argument(
        "--full-rebuild", action="store_true",
        help="忽略缓存，完全重新构建",
    )

    # ── run ──
    run_parser = subparsers.add_parser("run", help="运行 LLM agent benchmark")
    run_parser.add_argument("--provider", default="openai", choices=["openai", "anthropic"], help="LLM provider (默认: openai)")
    run_parser.add_argument("--model", default=None, help="模型名称 (也可通过 OPENAI_MODEL / ANTHROPIC_MODEL 环境变量设置)")
    run_parser.add_argument("--api-key", default=None, help="API Key (也可通过 OPENAI_API_KEY 环境变量设置)")
    run_parser.add_argument("--base-url", default=None, help="API Base URL (也可通过 OPENAI_BASE_URL 环境变量设置)")
    run_parser.add_argument("--data-dir", default="data", help="数据根目录或直接游戏数据目录 (默认: data)")
    run_parser.add_argument("--preset", default=None, help="数据 preset 名称 (默认: default)")
    run_parser.add_argument("--game-seed", type=int, default=None, help="游戏随机种子")
    run_parser.add_argument("--scoring-seed", type=int, default=None, help="评分随机种子")
    run_parser.add_argument("--archive-dir", default="runs/llm", help="存档目录 (默认: runs/llm，设为 none 禁用存档)")
    run_parser.add_argument("--resume", default=None, help="从指定存档目录续跑 (传入 archive run 目录路径)")
    run_parser.add_argument("--max-tool-calls-per-turn", type=int, default=20, help="每回合最大工具调用次数 (默认: 20)")
    run_parser.add_argument("--reasoning-effort", default=None, choices=["none", "minimal", "low", "medium", "high", "xhigh"], help="OpenAI-compatible 推理强度 (默认不传)")
    run_parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=None, help="启用或禁用 Anthropic adaptive thinking (默认不传)")
    run_parser.add_argument("--thinking-effort", default=None, choices=["low", "medium", "high", "max"], help="Anthropic 思考强度，max 仅部分模型支持 (默认不传)")
    run_parser.add_argument("--timeout", type=float, default=None, help="API 请求超时秒数 (也可通过 provider 对应环境变量设置)")
    run_parser.add_argument("--no-stream", action="store_true", help="禁用流式输出")
    run_parser.add_argument("--quiet", "-q", action="store_true", help="静默模式，只输出最终结果")

    # ── serve-leaderboard ──
    sl_parser = subparsers.add_parser("serve-leaderboard", help="启动排行榜静态服务")
    sl_parser.add_argument("--host", default="127.0.0.1")
    sl_parser.add_argument("--port", type=int, default=8080)
    sl_parser.add_argument(
        "--directory", type=Path,
        default=Path("web/leaderboard"),
        help="排行榜静态文件目录 (默认: web/leaderboard)",
    )

    args = parser.parse_args()

    if args.command == "serve":
        _serve(args.data_dir, args.preset, args.host, args.port)
    elif args.command == "run":
        _run(args)
    elif args.command == "build-leaderboard":
        _build_leaderboard(args.data_dir, args.output, full_rebuild=args.full_rebuild)
    elif args.command == "serve-leaderboard":
        _serve_leaderboard(args.host, args.port, args.directory)


def _run(args: argparse.Namespace) -> None:
    """运行 LLM agent benchmark。"""

    import json

    from guild_manager_bench.bench.llm import (
        LlmRunConfig,
        OpenAIChatCompletionsAgent,
        run_llm_game,
    )
    from guild_manager_bench.bench.llm.runner import _compute_run_stats
    from guild_manager_bench.game.presets import resolve_data_source

    _enable_ansi()

    # ── 构造 agent ──
    if args.provider == "anthropic":
        from guild_manager_bench.bench.llm import AnthropicMessagesAgent

        agent = AnthropicMessagesAgent.from_env(
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
            timeout=args.timeout,
            thinking=args.thinking,
            effort=args.thinking_effort,
        )
    else:
        agent = OpenAIChatCompletionsAgent.from_env(
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout,
        )

    # 禁用流式：移除 respond_stream 使 runner 回退到 respond
    if args.no_stream and hasattr(agent, "respond_stream"):
        delattr(agent, "respond_stream")

    data_preset = resolve_data_source(args.data_dir, args.preset)

    # ── 构造配置 ──
    archive_dir = None if args.archive_dir.lower() == "none" else args.archive_dir
    config = LlmRunConfig(
        archive_dir=archive_dir,
        max_tool_calls_per_turn=args.max_tool_calls_per_turn,
        game_seed=args.game_seed,
        scoring_seed=args.scoring_seed,
    )

    quiet = args.quiet
    model_name = _model_display_name(agent)
    _use_color = sys.stdout.isatty()

    # ── ANSI 样式辅助 ──
    def _cs(text: str, *codes: int) -> str:
        if not _use_color:
            return str(text)
        prefix = "".join(f"\033[{c}m" for c in codes)
        return f"{prefix}{text}\033[0m"

    _bold = lambda t: _cs(t, 1)
    _dim = lambda t: _cs(t, 2)
    _red = lambda t: _cs(t, 31)
    _green = lambda t: _cs(t, 32)
    _yellow = lambda t: _cs(t, 33)
    _blue = lambda t: _cs(t, 34)
    _magenta = lambda t: _cs(t, 35)
    _cyan = lambda t: _cs(t, 36)
    _bgreen = lambda t: _cs(t, 92)
    _bred = lambda t: _cs(t, 91)
    _bcyan = lambda t: _cs(t, 96)

    # ── 跨事件状态 ──
    state: dict[str, Any] = {
        "max_turns": None,
        "turn_start": 0.0,
        "prev_rank_score": None,
    }
    run_start = perf_counter()

    # ── 工具分类 ──
    _READ_TOOLS = frozenset({
        "get_party", "get_monsters", "get_crafting", "get_inventory",
        "get_upgrades", "get_recruitment", "get_events",
    })
    _WRITE_TOOLS = frozenset({
        "craft_equipment", "purchase_upgrade", "allocate_experience",
        "recruit_adventurer", "dismiss_adventurer", "equip_item", "unequip_item",
    })

    def _tool_arrow_and_name(name: str) -> tuple[str, str]:
        """返回工具的 (箭头, 样式名)。"""
        if name == "end_turn":
            return _green("✓▸"), _bold(_green(name))
        if name == "write_memo":
            return _yellow("▸"), _yellow(name)
        if name in _READ_TOOLS:
            return _dim("▸"), _dim(name)
        if name in _WRITE_TOOLS:
            return _cyan("▸"), _bold(_cyan(name))
        if name in ("preview_battle", "preview_team_power"):
            return _magenta("▸"), _magenta(name)
        return "▸", name

    # ── 打印辅助 ──
    def _print(*parts: str) -> None:
        if not quiet:
            try:
                print(*parts, flush=True)
            except UnicodeEncodeError:
                enc = sys.stdout.encoding or "ascii"
                safe = tuple(
                    part.encode(enc, errors="replace").decode(enc)
                    for part in parts
                )
                print(*safe, flush=True)

    def _short_args(arguments: dict | None, max_len: int = 60) -> str:
        """将工具参数格式化为短摘要。"""
        if not arguments:
            return ""
        filtered = {k: v for k, v in arguments.items() if k != "session_id"}
        if not filtered:
            return ""
        parts = []
        for k, v in filtered.items():
            s = str(v)
            if len(s) > 20:
                s = s[:18] + "…"
            parts.append(f"{k}={s}")
        text = ", ".join(parts)
        if len(text) > max_len:
            text = text[: max_len - 1] + "…"
        return f"  ({text})"

    # ── 事件回调 ──
    def on_event(event: dict) -> None:
        t = event["type"]

        if t == "run_started":
            session_id = event.get("session_id", "?")
            cfg = event.get("config", {})
            _print()
            _print(_bold("  ⚔  Guild Manager Bench"))
            _print(f"  {_dim(f'{args.provider} / {model_name}')}")
            _print(f"  {_dim(f'session {session_id[:8]}…')}")
            seed_parts = []
            if cfg.get("game_seed") is not None:
                seed_parts.append(f"game={cfg['game_seed']}")
            if cfg.get("scoring_seed") is not None:
                seed_parts.append(f"scoring={cfg['scoring_seed']}")
            if seed_parts:
                _print(f"  {_dim(' · '.join(seed_parts))}")
            _print(_dim(f"  {'─' * 44}"))

        elif t == "run_resumed":
            restored = event.get("restored_turns", 0)
            _print(_yellow(f"  ↻ Resumed from turn {restored + 1}"))

        elif t == "turn_started":
            obs = event.get("observation", {})
            turn = obs.get("turn", event.get("turn", "?"))
            mt = obs.get("max_turns")
            state["max_turns"] = mt
            state["turn_start"] = perf_counter()

            progress = f"Turn {turn}/{mt}" if mt else f"Turn {turn}"
            parts = [_bold(f"── {progress} ──")]

            state_parts = []
            gold = obs.get("gold")
            if gold is not None:
                state_parts.append(f"💰{gold}")
            xp = obs.get("experience_pool")
            if xp is not None:
                state_parts.append(f"⭐{xp}")
            adventurers = obs.get("adventurers")
            party_size = obs.get("party_size")
            party_limit = obs.get("party_size_limit")
            if party_size is not None and party_limit is not None:
                state_parts.append(f"👥{party_size}/{party_limit}")
            elif isinstance(adventurers, list):
                state_parts.append(f"👥{len(adventurers)}")
            monsters = obs.get("monsters")
            if isinstance(monsters, list):
                state_parts.append(f"🗡{len(monsters)}")
            if state_parts:
                parts.append("  ".join(state_parts))

            _print()
            _print("  " + "   ".join(parts))

        elif t == "model_response":
            timing = event.get("timing", {})
            usage = event.get("usage", {})
            step = event.get("step")

            duration_ms = timing.get("duration_ms", 0)
            duration_s = duration_ms / 1000 if isinstance(duration_ms, (int, float)) else 0

            inp = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
            out = usage.get("output_tokens") or usage.get("completion_tokens") or 0

            parts = []
            if duration_s > 0:
                parts.append(_dim(f"⏱ {_format_duration(duration_s)}"))
            if inp or out:
                parts.append(_dim(f"→{_format_tokens(inp)} ←{_format_tokens(out)}"))
            if step:
                parts.append(_dim(f"step {step}"))
            if parts:
                _print(f"  {'  '.join(parts)}")

        elif t == "tool_call":
            name = event.get("name", "")
            call_args = event.get("arguments")
            arrow, styled_name = _tool_arrow_and_name(name)
            args_text = _short_args(call_args)
            _print(f"  {arrow} {styled_name}{args_text}")

        elif t == "tool_result":
            result = event.get("result", {})
            ok = result.get("ok")
            name = event.get("name", "")
            if ok is not False:
                summary = _tool_success_summary(name, result)
                _print(f"    {_green('✓')} {summary}" if summary else f"    {_green('✓')}")
            elif name != "end_turn":
                err = result.get("error") or "unknown error"
                _print(f"    {_red('✗')} {_red(err)}")

        elif t == "turn_completed":
            trace = event.get("trace", {})
            turn = trace.get("turn", "?")
            tool_calls = trace.get("tool_calls", [])
            tool_count = len(tool_calls)
            fail_count = sum(1 for c in tool_calls if c.get("result", {}).get("ok") is False)
            ok_count = tool_count - fail_count
            turn_duration = perf_counter() - state["turn_start"]

            detail_parts = [f"{ok_count} ok"]
            if fail_count:
                detail_parts.append(f"{fail_count} fail")
            rank_score = trace.get("rank_score")

            parts = [f"Turn {turn}", "  ".join(detail_parts), _format_duration(turn_duration)]

            if rank_score is not None:
                prev = state["prev_rank_score"]
                score_text = f"rank {int(rank_score)}"
                if prev is not None:
                    delta = rank_score - prev
                    if delta > 0:
                        score_text += f" {_green(f'▲{int(delta)}')}"
                    elif delta < 0:
                        score_text += f" {_red(f'▼{abs(int(delta))}')}"
                parts.append(score_text)
                state["prev_rank_score"] = rank_score

            _print(f"  {_bold(_green('✅'))} {'  │  '.join(parts)}")

        elif t == "turn_failed":
            trace = event.get("trace", {})
            turn = trace.get("turn", "?")
            reason = trace.get("failure_reason", "unknown")
            tool_calls = trace.get("tool_calls", [])
            turn_duration = perf_counter() - state["turn_start"]
            _print(
                f"  {_bold(_red('❌'))} Turn {turn} failed  "
                f"{_red(reason)}  "
                f"({len(tool_calls)} tools, {_format_duration(turn_duration)})"
            )

        elif t == "turn_retry":
            turn = event.get("turn", "?")
            retry_count = event.get("retry_count", 0)
            total = event.get("total_allowed", "?")
            reason = event.get("reason", "")
            _print(
                f"  {_yellow('↻')} {_yellow(f'Turn {turn} retry ({retry_count}/{total})')}"
                f"  {_dim(reason)}"
            )

        elif t == "retry":
            reason = event.get("reason", "")
            msg = event.get("message", "")
            short = msg[:80] + "…" if len(msg) > 80 else msg
            _print(f"  {_yellow('↻')} retry  {_dim(reason)}  {_dim(short)}")

        elif t in ("run_completed", "run_failed"):
            _print()

    # ── 执行 ──
    resume_dir = args.resume
    try:
        run = run_llm_game(
            agent,
            data_dir=data_preset.data_dir,
            config=config,
            event_sink=on_event,
            resume_archive_dir=resume_dir,
            data_source=data_preset.to_dict(),
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)

    # ── 输出结果 ──
    wall_time = perf_counter() - run_start
    stats = _compute_run_stats(run)
    score = run.score
    timing = stats["timing"]
    tokens = stats["token_usage"]
    actions = stats["game_actions"]
    tool_stats = stats["tool_calls"]

    _sep = "═" * 46
    _sep_thin = "─" * 46

    print()
    print(_bold(_cs(f"  {_sep}", 36)))
    is_ok = run.status == "completed"
    print(_bold(_cs(f"  ⚔  Run {'Complete' if is_ok else 'Failed'}", 36)))
    print(_bold(_cs(f"  {_sep}", 36)))
    print()

    status_icon = _bold(_green("✅ Completed")) if is_ok else _bold(_red("❌ Failed"))
    print(f"  {'Status':<13}{status_icon}")
    if score is not None:
        rank = score.get("rank_score") if isinstance(score, Mapping) else None
        if rank is not None:
            print(f"  {'Score':<13}{_bold(f'{rank:.0f}')}")
        else:
            total = score.get("total") if isinstance(score, Mapping) else None
            print(f"  {'Score':<13}{_bold(str(total if total is not None else score))}")
    print(f"  {'Session':<13}{_dim(run.session_id[:16] + '…' if len(run.session_id) > 16 else run.session_id)}")
    max_t = state["max_turns"]
    turns_display = f"{len(run.turns)}/{max_t}" if max_t else str(len(run.turns))
    print(f"  {'Turns':<13}{turns_display}")
    print()

    # Performance
    print(f"  {_dim(_sep_thin)}")
    print(f"  {_bold('Performance')}")
    print(f"  {_dim(_sep_thin)}")
    print(f"  {'Wall Time':<13}{_format_duration(wall_time)}")
    print(f"  {'Model Time':<13}{_format_duration(timing['total_duration_seconds'])}")
    inp_str = _format_tokens(tokens["input_tokens"])
    out_str = _format_tokens(tokens["output_tokens"])
    print(f"  {'Tokens':<13}{inp_str} in / {out_str} out")
    cache_read = tokens.get("cache_read_input_tokens")
    if cache_read:
        print(f"  {'Cache read':<13}{_format_tokens(cache_read)}")
    cache_create = tokens.get("cache_creation_input_tokens")
    if cache_create:
        print(f"  {'Cache create':<13}{_format_tokens(cache_create)}")
    print()

    # Battles
    won = actions["battles_won"]
    lost = actions["battles_lost"]
    total_battles = won + lost
    print(f"  {_dim(_sep_thin)}")
    print(f"  {_bold('Battles')}")
    print(f"  {_dim(_sep_thin)}")
    print(f"  {'Won / Lost':<13}{_green(str(won))} / {_red(str(lost))}")
    if total_battles > 0:
        win_rate = won / total_battles * 100
        print(f"  {'Win Rate':<13}{win_rate:.0f}%")
    print()

    # Economy
    print(f"  {_dim(_sep_thin)}")
    print(f"  {_bold('Economy')}")
    print(f"  {_dim(_sep_thin)}")
    print(f"  {'Gold':<13}{actions['total_gold_earned']}")
    print(f"  {'XP Earned':<13}{actions['total_experience_earned']}")
    crafted = actions.get("total_equipment_crafted", 0)
    upgrades = actions.get("total_upgrades_purchased", 0)
    recruits = actions.get("total_recruits", 0)
    if crafted or upgrades or recruits:
        extra = []
        if crafted:
            extra.append(f"{crafted} crafted")
        if upgrades:
            extra.append(f"{upgrades} upgrades")
        if recruits:
            extra.append(f"{recruits} recruits")
        print(f"  {'Actions':<13}{'  '.join(extra)}")
    print()

    # Tools
    tc_total = tool_stats.get("total", 0)
    tc_ok = tool_stats.get("successful", 0)
    print(f"  {_dim(_sep_thin)}")
    print(f"  {_bold('Tools')}")
    print(f"  {_dim(_sep_thin)}")
    print(f"  {'Total':<13}{tc_total} calls")
    if tc_total > 0:
        print(f"  {'Success':<13}{tc_ok} ({tc_ok / tc_total * 100:.0f}%)")
    by_name = tool_stats.get("by_name", {})
    if by_name:
        top_tools = sorted(by_name.items(), key=lambda x: x[1], reverse=True)[:6]
        tool_str = "  ".join(f"{n}:{c}" for n, c in top_tools)
        print(f"  {'Top':<13}{_dim(tool_str)}")
    print()

    # Archive
    if run.archive_dir:
        print(f"  {_dim(_sep_thin)}")
        print(f"  {_bold('Archive')}")
        print(f"  {_dim(_sep_thin)}")
        print(f"  {'Path':<13}{_dim(run.archive_dir)}")
        print()

    print(_bold(_cs(f"  {_sep}", 36)))

    if run.failure_reason:
        print(f"  {_red(f'Failure: {run.failure_reason}')}")

    # ── JSON 详情（调试用）──
    if not quiet:
        print()
        print(_dim("(stats json)"))
        print(json.dumps(stats, indent=2, ensure_ascii=False))


def _tool_success_summary(name: str, result: dict) -> str:
    """从成功的工具结果中提取一句话摘要，空字符串表示无摘要。"""
    if name == "start_session":
        sid = result.get("session_id", "")
        return f"session={sid}" if sid else ""
    if name == "end_turn":
        turn_result = result.get("turn_result")
        if isinstance(turn_result, Mapping):
            parts = []
            battles = turn_result.get("battles") or []
            if battles:
                won = sum(1 for b in battles if b.get("won"))
                parts.append(f"{won}/{len(battles)} battles won")
            crafted = turn_result.get("crafted_equipment_ids") or []
            if crafted:
                parts.append(f"{len(crafted)} crafted")
            purchased = turn_result.get("purchased_upgrade_ids") or []
            if purchased:
                parts.append(f"{len(purchased)} upgrades")
            recruited = turn_result.get("recruited_adventurer_ids") or []
            if recruited:
                parts.append(f"{len(recruited)} recruited")
            return ", ".join(parts) if parts else "turn ended"
        return "turn ended"
    if name == "craft_equipment":
        event = result.get("event")
        if isinstance(event, Mapping):
            return event.get("summary", "")
        return ""
    if name == "purchase_upgrade":
        event = result.get("event")
        if isinstance(event, Mapping):
            return event.get("summary", "")
        return ""
    if name == "allocate_experience":
        event = result.get("event")
        if isinstance(event, Mapping):
            return event.get("summary", "")
        return ""
    if name == "recruit_adventurer":
        adv = result.get("recruited_adventurer")
        if isinstance(adv, Mapping):
            adv_name = adv.get("name", "")
            return f"recruited {adv_name}" if adv_name else "recruited"
        return ""
    if name == "dismiss_adventurer":
        event = result.get("event")
        if isinstance(event, Mapping):
            return event.get("summary", "")
        return ""
    if name == "equip_item":
        event = result.get("event")
        if isinstance(event, Mapping):
            return event.get("summary", "")
        return ""
    if name == "unequip_item":
        event = result.get("event")
        if isinstance(event, Mapping):
            return event.get("summary", "")
        return ""
    if name == "write_memo":
        memo = result.get("memo")
        if isinstance(memo, Mapping):
            return f"memo saved ({memo.get('count', '?')} total)"
        return ""
    if name == "preview_battle":
        preview = result.get("preview")
        if isinstance(preview, Mapping):
            verdict = preview.get("verdict", "")
            return f"preview: {verdict}" if verdict else ""
        return ""
    # get_party, get_monsters, get_crafting, get_inventory, get_upgrades,
    # get_recruitment, get_events 都是只读查询，不需要摘要
    return ""


def _serve(data_dir: str, preset: str | None, host: str, port: int) -> None:
    """启动可视化服务。"""

    import uvicorn

    from guild_manager_bench.api.app import create_app

    uvicorn.run(create_app(data_dir, preset=preset), host=host, port=port)


def _build_leaderboard(data_dir: Path, output: Path, *, full_rebuild: bool = False) -> None:
    """构建排行榜数据文件。"""

    from guild_manager_bench.bench.leaderboard import build_leaderboard

    build_leaderboard(data_dir.resolve(), output.resolve(), incremental=not full_rebuild)


def _serve_leaderboard(host: str, port: int, directory: Path) -> None:
    """启动排行榜静态文件服务。"""

    import http.server
    import functools

    directory = directory.resolve()
    if not directory.is_dir():
        print(f"Error: {directory} is not a directory")
        raise SystemExit(1)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    with http.server.HTTPServer((host, port), handler) as httpd:
        print(f"Leaderboard → http://{host}:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
