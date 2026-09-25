import sys

import pytest

from guild_manager_bench import cli


def test_run_accepts_extended_reasoning_effort(monkeypatch) -> None:
    captured = {}

    def fake_run(args) -> None:
        captured["reasoning_effort"] = args.reasoning_effort

    monkeypatch.setattr(cli, "_run", fake_run)

    for reasoning_effort in ("ultra", "max"):
        monkeypatch.setattr(
            sys,
            "argv",
            ["guild-manager", "run", "--reasoning-effort", reasoning_effort],
        )

        cli.main()

        assert captured["reasoning_effort"] == reasoning_effort


@pytest.mark.parametrize("option, field", [
    ("--verbose", "verbose"), ("--debug", "debug"), ("--json", "json_output"),
    ("--show-model-text", "show_model_text"), ("--show-reasoning", "show_reasoning"),
    ("--no-color", "no_color"),
])
def test_run_accepts_display_options(monkeypatch, option, field) -> None:
    captured = {}
    monkeypatch.setattr(cli, "_run", lambda args: captured.update(vars(args)))
    monkeypatch.setattr(sys, "argv", ["guild-manager", "run", option])
    cli.main()
    assert captured[field] is True


def test_run_accepts_openai_responses_provider(monkeypatch) -> None:
    captured = {}

    def fake_run(args) -> None:
        captured["provider"] = args.provider

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["guild-manager", "run", "--provider", "openai-responses"],
    )

    cli.main()

    assert captured["provider"] == "openai-responses"


@pytest.mark.parametrize("options", [
    ["--quiet", "--verbose"], ["--json", "--debug"],
    ["--json", "--show-model-text"], ["--quiet", "--show-reasoning"],
])
def test_run_rejects_conflicting_output_options(monkeypatch, options) -> None:
    monkeypatch.setattr(sys, "argv", ["guild-manager", "run", *options])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize("provider", ["openai", "openai-responses", "anthropic"])
@pytest.mark.parametrize("mode", [[], ["--quiet"], ["--verbose"], ["--debug"], ["--json"]])
def test_cli_runs_offline_with_each_provider_and_output_mode(monkeypatch, capsys, provider, mode) -> None:
    import json
    from guild_manager_bench.bench.llm import (
        AnthropicMessagesAgent, AnthropicMessagesConfig,
        OpenAIChatCompletionsAgent, OpenAIChatCompletionsConfig,
        OpenAIResponsesAgent, OpenAIResponsesConfig,
    )
    classes = {
        "openai": (OpenAIChatCompletionsAgent, OpenAIChatCompletionsConfig),
        "openai-responses": (OpenAIResponsesAgent, OpenAIResponsesConfig),
        "anthropic": (AnthropicMessagesAgent, AnthropicMessagesConfig),
    }
    responses = {
        "openai": {"choices": [{"message": {"role": "assistant", "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "end_turn", "arguments": '{"hunts": []}'}}
        ]}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        "openai-responses": {"output": [{"type": "function_call", "call_id": "call-1", "name": "end_turn", "arguments": '{"hunts": []}'}], "usage": {"input_tokens": 10, "output_tokens": 5}},
        "anthropic": {"content": [{"type": "tool_use", "id": "call-1", "name": "end_turn", "input": {"hunts": []}}], "usage": {"input_tokens": 10, "output_tokens": 5}},
    }
    requests = []

    def transport(url, headers, body, timeout):
        requests.append(body)
        return responses[provider]

    agent_class, config_class = classes[provider]
    agent = agent_class(config_class(model="offline-test", api_key="do-not-print-this-key"), transport=transport)
    monkeypatch.setattr(agent_class, "from_env", lambda **kwargs: agent)
    # 即使环境强制颜色，重定向输出仍保持无 ANSI、无动画。
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr(sys, "argv", [
        "guild-manager", "run", "--provider", provider, "--no-stream",
        "--archive-dir", "none", *mode,
    ])
    cli.main()
    output = capsys.readouterr()
    assert output.err == ""
    assert "do-not-print-this-key" not in output.out
    assert "\x1b" not in output.out
    assert len(requests) == 8
    if mode == ["--json"]:
        report = json.loads(output.out)
        assert report["status"] == "completed"
        assert report["stats"]["token_usage"]["input_tokens"] == 80
        assert report["stats"]["model_interaction"]["total_turns_completed"] == 8
        assert report["data"]["game_seed"] == 20260524
    else:
        assert "公会经营完成" in output.out
        assert "stats json" not in output.out
        if mode == ["--quiet"]:
            assert "正在等待模型响应" not in output.out
        elif mode == ["--debug"]:
            assert "model_request" in output.out


@pytest.mark.parametrize("failure, exit_code", [(RuntimeError("network unavailable"), 1), (KeyboardInterrupt(), 130)])
@pytest.mark.parametrize("mode", [[], ["--json"]])
def test_cli_reports_runtime_errors_and_interrupts_with_resume_command(monkeypatch, capsys, tmp_path, failure, exit_code, mode) -> None:
    import json
    from guild_manager_bench.bench.llm import OpenAIChatCompletionsAgent, OpenAIChatCompletionsConfig

    def transport(*args):
        raise failure

    agent = OpenAIChatCompletionsAgent(OpenAIChatCompletionsConfig(model="offline-test"), transport=transport)
    monkeypatch.setattr(OpenAIChatCompletionsAgent, "from_env", lambda **kwargs: agent)
    monkeypatch.setattr(sys, "argv", ["guild-manager", "run", "--no-stream", "--archive-dir", str(tmp_path), *mode])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == exit_code
    output = capsys.readouterr()
    if mode == ["--json"]:
        report = json.loads(output.out)
        assert report["status"] == ("interrupted" if exit_code == 130 else "failed")
        assert "--resume" in report["resume_command"]
        assert output.err == ""
    else:
        assert "--resume" in output.err
        assert "Traceback" not in output.err
    replay_path = next(tmp_path.glob("*/replay.json"))
    assert json.loads(replay_path.read_text(encoding="utf-8"))["status"] == "interrupted"


def test_cli_returns_failure_exit_code_and_counts_failed_attempts(monkeypatch, capsys) -> None:
    import json
    from guild_manager_bench.bench.llm import OpenAIChatCompletionsAgent, OpenAIChatCompletionsConfig
    agent = OpenAIChatCompletionsAgent(
        OpenAIChatCompletionsConfig(model="offline-test"),
        transport=lambda *args: {"choices": [{"message": {"content": "no tools"}}]},
    )
    monkeypatch.setattr(OpenAIChatCompletionsAgent, "from_env", lambda **kwargs: agent)
    monkeypatch.setattr(sys, "argv", ["guild-manager", "run", "--no-stream", "--archive-dir", "none", "--json"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed"
    assert report["stats"]["model_interaction"]["total_turns_completed"] == 0
    assert report["stats"]["model_interaction"]["total_turns_failed"] == 3
    assert report["score"] is None


def test_cli_streaming_prints_requested_model_content_once(monkeypatch, capsys) -> None:
    from guild_manager_bench.bench.llm import OpenAIChatCompletionsAgent, OpenAIChatCompletionsConfig
    calls = []

    def stream_transport(url, headers, body, timeout):
        calls.append(body)
        yield {"choices": [{"delta": {"reasoning_content": "API推理标记"}}]}
        yield {"choices": [{"delta": {"content": "模型正文标记"}}]}
        yield {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call-1", "type": "function",
            "function": {"name": "end_turn", "arguments": '{"hunts": []}'},
        }]}}]}
        yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    agent = OpenAIChatCompletionsAgent(OpenAIChatCompletionsConfig(model="offline-test"), stream_transport=stream_transport)
    monkeypatch.setattr(OpenAIChatCompletionsAgent, "from_env", lambda **kwargs: agent)
    monkeypatch.setattr(sys, "argv", ["guild-manager", "run", "--archive-dir", "none", "--show-model-text", "--show-reasoning"])
    cli.main()
    output = capsys.readouterr().out
    assert len(calls) == 8
    assert output.count("模型正文标记") == 8
    assert output.count("API推理标记") == 8
    assert "公会经营完成" in output


@pytest.mark.parametrize("use_env", [False, True])
def test_cli_no_color_disables_styles_in_interactive_terminal(monkeypatch, use_env) -> None:
    import io
    from guild_manager_bench.bench.llm import OpenAIChatCompletionsAgent, OpenAIChatCompletionsConfig

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    stream = Terminal()
    agent = OpenAIChatCompletionsAgent(
        OpenAIChatCompletionsConfig(model="offline-test"), transport=lambda *args: {
            "choices": [{"message": {"tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "end_turn", "arguments": '{"hunts": []}'},
            }]}}],
        },
    )
    monkeypatch.setattr(OpenAIChatCompletionsAgent, "from_env", lambda **kwargs: agent)
    if use_env:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "argv", ["guild-manager", "run", "--archive-dir", "none", "--no-stream", "--quiet", *([] if use_env else ["--no-color"])])
    cli.main()
    assert "公会经营完成" in stream.getvalue()
    assert "\x1b" not in stream.getvalue()
