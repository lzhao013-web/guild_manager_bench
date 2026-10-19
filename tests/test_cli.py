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


@pytest.mark.parametrize("provider, key", [
    ("openai", "max_tokens"),
    ("openai-responses", "max_output_tokens"),
    ("anthropic", "max_tokens"),
    ("google", "max_tokens"),
])
def test_run_passes_max_tokens_to_each_provider(monkeypatch, provider, key) -> None:
    from guild_manager_bench.bench.llm import (
        AnthropicMessagesAgent,
        GoogleGenerateContentAgent,
        OpenAIChatCompletionsAgent,
        OpenAIResponsesAgent,
    )

    agent_classes = {
        "openai": OpenAIChatCompletionsAgent,
        "openai-responses": OpenAIResponsesAgent,
        "anthropic": AnthropicMessagesAgent,
        "google": GoogleGenerateContentAgent,
    }
    captured = {}

    def from_env(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after config")

    monkeypatch.setattr(agent_classes[provider], "from_env", from_env)

    for argv_tail, expected in ((["--max-tokens", "32000"], 32000), ([], None)):
        captured.clear()
        monkeypatch.setattr(
            sys,
            "argv",
            ["guild-manager", "run", "--provider", provider, *argv_tail],
        )

        with pytest.raises(SystemExit):
            cli.main()

        assert captured[key] == expected


@pytest.mark.parametrize("provider, key", [
    ("openai", "reasoning_effort"), ("openai-responses", "reasoning_effort"),
    ("anthropic", "effort"), ("google", "reasoning_effort"),
])
def test_run_dispatches_unified_reasoning_effort(monkeypatch, provider, key):
    from guild_manager_bench.bench.llm import (
        AnthropicMessagesAgent, GoogleGenerateContentAgent,
        OpenAIChatCompletionsAgent, OpenAIResponsesAgent,
    )
    classes = {
        "openai": OpenAIChatCompletionsAgent, "openai-responses": OpenAIResponsesAgent,
        "anthropic": AnthropicMessagesAgent, "google": GoogleGenerateContentAgent,
    }
    captured = {}

    def from_env(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after config")

    monkeypatch.setattr(classes[provider], "from_env", from_env)
    monkeypatch.setattr(sys, "argv", ["guild-manager", "run", "--provider", provider, "--reasoning-effort", "high"])
    with pytest.raises(SystemExit):
        cli.main()
    assert captured[key] == "high"


def test_old_thinking_effort_is_alias_for_unified_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "_run", lambda args: captured.update(vars(args)))
    monkeypatch.setattr(sys, "argv", ["guild-manager", "run", "--provider", "anthropic", "--thinking-effort", "high"])
    cli.main()
    assert captured["reasoning_effort"] == "high"


def test_reasoning_effort_and_legacy_alias_cannot_conflict(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "guild-manager", "run", "--provider", "anthropic",
        "--reasoning-effort", "high", "--thinking-effort", "low",
    ])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize("provider, effort", [("google", "max"), ("anthropic", "minimal")])
def test_cli_rejects_invalid_provider_effort_before_network(monkeypatch, capsys, provider, effort):
    import json

    def urlopen(*args, **kwargs):
        pytest.fail("invalid effort must not issue a network request")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(sys, "argv", [
        "guild-manager", "run", "--provider", provider, "--model", "test-model",
        "--reasoning-effort", effort, "--archive-dir", "none", "--json",
    ])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert "reasoning_effort" in json.loads(capsys.readouterr().out)["failure_reason"]


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


@pytest.mark.parametrize("provider", ["openai", "openai-responses", "anthropic", "google"])
@pytest.mark.parametrize("mode", [[], ["--quiet"], ["--verbose"], ["--debug"], ["--json"]])
def test_cli_runs_offline_with_each_provider_and_output_mode(monkeypatch, capsys, provider, mode) -> None:
    import json
    from guild_manager_bench.bench.llm import (
        AnthropicMessagesAgent, AnthropicMessagesConfig,
        GoogleGenerateContentAgent, GoogleGenerateContentConfig,
        OpenAIChatCompletionsAgent, OpenAIChatCompletionsConfig,
        OpenAIResponsesAgent, OpenAIResponsesConfig,
    )
    classes = {
        "openai": (OpenAIChatCompletionsAgent, OpenAIChatCompletionsConfig),
        "openai-responses": (OpenAIResponsesAgent, OpenAIResponsesConfig),
        "anthropic": (AnthropicMessagesAgent, AnthropicMessagesConfig),
        "google": (GoogleGenerateContentAgent, GoogleGenerateContentConfig),
    }
    responses = {
        "openai": {"choices": [{"message": {"role": "assistant", "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "end_turn", "arguments": '{"hunts": []}'}}
        ]}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        "openai-responses": {"output": [{"type": "function_call", "call_id": "call-1", "name": "end_turn", "arguments": '{"hunts": []}'}], "usage": {"input_tokens": 10, "output_tokens": 5}},
        "anthropic": {"content": [{"type": "tool_use", "id": "call-1", "name": "end_turn", "input": {"hunts": []}}], "usage": {"input_tokens": 10, "output_tokens": 5}},
        "google": {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "end_turn", "args": {"hunts": []}}}
        ]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3, "thoughtsTokenCount": 2}},
    }
    requests = []

    def transport(url, headers, body, timeout):
        requests.append(body)
        return responses[provider]

    agent_class, config_class = classes[provider]
    agent = agent_class(config_class(model="offline-test", api_key="do-not-print-this-key"), transport=transport)
    def from_env(**kwargs):
        from dataclasses import replace
        field = "effort" if provider == "anthropic" else "reasoning_effort"
        agent.config = replace(agent.config, **{field: kwargs[field]})
        return agent

    monkeypatch.setattr(agent_class, "from_env", from_env)
    # 即使环境强制颜色，重定向输出仍保持无 ANSI、无动画。
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr(sys, "argv", [
        "guild-manager", "run", "--provider", provider, "--no-stream",
        "--archive-dir", "none", "--reasoning-effort", "high", *mode,
    ])
    cli.main()
    output = capsys.readouterr()
    assert output.err == ""
    assert "do-not-print-this-key" not in output.out
    assert "\x1b" not in output.out
    assert len(requests) == 8
    for body in requests:
        if provider == "google":
            assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "HIGH"}
        elif provider == "anthropic":
            assert body["output_config"] == {"effort": "high"}
        elif provider == "openai-responses":
            assert body["reasoning"]["effort"] == "high"
        else:
            assert body["reasoning_effort"] == "high"
    if mode == ["--json"]:
        report = json.loads(output.out)
        assert report["status"] == "completed"
        assert report["stats"]["token_usage"]["input_tokens"] == 80
        assert report["stats"]["token_usage"]["output_tokens"] == 40
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


@pytest.mark.parametrize("provider", ["openai", "google"])
def test_cli_streaming_prints_requested_model_content_once(monkeypatch, capsys, provider) -> None:
    from guild_manager_bench.bench.llm import (
        GoogleGenerateContentAgent, GoogleGenerateContentConfig,
        OpenAIChatCompletionsAgent, OpenAIChatCompletionsConfig,
    )
    calls = []

    def stream_transport(url, headers, body, timeout):
        calls.append(body)
        if provider == "google":
            yield {"candidates": [{"content": {"parts": [{"text": "API推理标记", "thought": True}]}}]}
            yield {"candidates": [{"content": {"parts": [{"text": "模型正文标记"}]}}]}
            yield {"candidates": [{"content": {"parts": [{"functionCall": {"name": "end_turn", "args": {"hunts": []}}}]}, "finishReason": "STOP"}]}
            return
        yield {"choices": [{"delta": {"reasoning_content": "API推理标记"}}]}
        yield {"choices": [{"delta": {"content": "模型正文标记"}}]}
        yield {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call-1", "type": "function",
            "function": {"name": "end_turn", "arguments": '{"hunts": []}'},
        }]}}]}
        yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    agent_class, config_class = {
        "openai": (OpenAIChatCompletionsAgent, OpenAIChatCompletionsConfig),
        "google": (GoogleGenerateContentAgent, GoogleGenerateContentConfig),
    }[provider]
    agent = agent_class(config_class(model="offline-test"), stream_transport=stream_transport)
    monkeypatch.setattr(agent_class, "from_env", lambda **kwargs: agent)
    monkeypatch.setattr(sys, "argv", ["guild-manager", "run", "--provider", provider, "--archive-dir", "none", "--show-model-text", "--show-reasoning"])
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
