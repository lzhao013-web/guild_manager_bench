import io
import json
import urllib.error
from copy import deepcopy
from pathlib import Path

import pytest

from guild_manager_bench.bench.llm import (
    GoogleGenerateContentAgent,
    GoogleGenerateContentConfig,
    GoogleGenerateContentError,
    LlmRunConfig,
    run_llm_game,
    run_llm_turn,
    tool_schemas,
)
from guild_manager_bench.bench.llm.tools import GuildManagerTools


@pytest.fixture(autouse=True)
def clear_google_env(monkeypatch):
    for name in (
        "GOOGLE_MODEL", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_GENERATIVE_AI_API_KEY", "GOOGLE_BASE_URL", "GOOGLE_TIMEOUT",
        "GOOGLE_MAX_TOKENS", "GOOGLE_REASONING_EFFORT", "GOOGLE_SESSION_ID", "OPENCODE_SESSION_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def generation(parts, **fields):
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}], **fields}


def function_part(name, args, **fields):
    return {"functionCall": {"name": name, "args": args}, **fields}


def test_google_config_precedence_and_optional_settings(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GOOGLE_MODEL=dotenv-model\nGOOGLE_API_KEY=dotenv-key\n"
        "GOOGLE_BASE_URL=https://dotenv.test/v1beta\nGOOGLE_TIMEOUT=60\n"
        "GOOGLE_MAX_TOKENS=4096\nGOOGLE_SESSION_ID=dotenv-session\n",
        encoding="utf-8",
    )
    config = GoogleGenerateContentConfig.from_env(env_file=env_file)
    assert (config.model, config.api_key, config.timeout, config.max_tokens) == ("dotenv-model", "dotenv-key", 60, 4096)
    assert config.base_url == "https://dotenv.test/v1beta"
    assert config.session_id == "dotenv-session"
    assert config.session_id_explicit is True

    monkeypatch.setenv("GOOGLE_MODEL", "process-model")
    monkeypatch.setenv("GOOGLE_API_KEY", "process-key")
    assert GoogleGenerateContentConfig.from_env(env_file=env_file).model == "process-model"
    assert GoogleGenerateContentConfig.from_env(env_file=env_file).api_key == "process-key"
    config = GoogleGenerateContentConfig.from_env(
        model="explicit-model", api_key="explicit-key", base_url="https://explicit.test/v1beta",
        timeout=30, max_tokens=2048, session_id="explicit-session", env_file=env_file,
    )
    assert (config.model, config.api_key, config.timeout, config.max_tokens) == ("explicit-model", "explicit-key", 30, 2048)
    assert config.base_url == "https://explicit.test/v1beta"
    assert config.session_id == "explicit-session"


@pytest.mark.parametrize("key_name", ["GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"])
def test_google_config_accepts_api_key_env_names(monkeypatch, key_name):
    monkeypatch.setenv(key_name, "test-key")
    config = GoogleGenerateContentConfig.from_env(model="gemini-test", env_file=None)
    assert config.api_key == "test-key"
    assert config.base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert config.timeout == 600
    assert config.max_tokens is None


def test_google_config_requires_model():
    with pytest.raises(GoogleGenerateContentError, match="GOOGLE_MODEL"):
        GoogleGenerateContentConfig.from_env(env_file=None)


def test_google_reasoning_effort_config_precedence(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_MODEL=gemini-test\nGOOGLE_REASONING_EFFORT=high\n", encoding="utf-8")
    assert GoogleGenerateContentConfig.from_env(env_file=env_file).reasoning_effort == "high"
    monkeypatch.setenv("GOOGLE_REASONING_EFFORT", "low")
    assert GoogleGenerateContentConfig.from_env(env_file=env_file).reasoning_effort == "low"
    assert GoogleGenerateContentConfig.from_env(env_file=env_file, reasoning_effort="medium").reasoning_effort == "medium"
    monkeypatch.delenv("GOOGLE_REASONING_EFFORT")
    assert GoogleGenerateContentConfig.from_env(model="m", env_file=None).reasoning_effort is None


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high"])
@pytest.mark.parametrize("streaming", [False, True])
def test_google_reasoning_effort_maps_to_native_thinking_level(effort, streaming):
    requests = []

    def transport(url, headers, body, timeout):
        requests.append(body)
        return generation([{"text": "ok"}])

    config = GoogleGenerateContentConfig.from_env(
        model="gemini-test", env_file=None, reasoning_effort=effort,
        max_tokens=8192, temperature=0.2,
    )
    agent = GoogleGenerateContentAgent(config, transport=transport, stream_transport=lambda *args: [transport(*args)])
    respond = agent.respond_stream if streaming else agent.respond
    respond(messages=[{"role": "user", "content": "go"}], tools=[])
    assert requests[0]["generationConfig"] == {
        "temperature": 0.2, "maxOutputTokens": 8192,
        "thinkingConfig": {"thinkingLevel": effort.upper()},
    }


@pytest.mark.parametrize("effort", ["none", "max", "xhigh", "ultra"])
def test_google_rejects_unsupported_reasoning_effort(effort):
    with pytest.raises(GoogleGenerateContentError, match="reasoning_effort"):
        GoogleGenerateContentConfig(model="m", reasoning_effort=effort)


def test_google_request_uses_native_protocol_and_preserves_tool_schema():
    captured = {}

    def transport(url, headers, body, timeout):
        captured.update(url=url, headers=headers, body=body, timeout=timeout)
        return generation([{"text": "ok"}])

    schemas = tool_schemas()
    original = deepcopy(schemas)
    agent = GoogleGenerateContentAgent(
        GoogleGenerateContentConfig(
            model="models/gemini-2.5-flash", api_key="test-key", timeout=45,
            temperature=0.3, top_p=0.8, max_tokens=4096, tool_choice="required",
            session_id="google-session",
        ), transport=transport,
    )
    response = agent.respond(messages=(
        {"role": "system", "content": "first"},
        {"role": "system", "content": "second"},
        {"role": "user", "content": "go"},
    ), tools=schemas)
    assert response.text == "ok"
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    assert captured["headers"]["x-goog-api-key"] == "test-key"
    assert captured["headers"]["x-opencode-session"] == "google-session"
    assert "Authorization" not in captured["headers"]
    assert captured["timeout"] == 45
    body = captured["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "first"}, {"text": "second"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "go"}]}]
    assert body["generationConfig"] == {"temperature": 0.3, "topP": 0.8, "maxOutputTokens": 4096}
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}
    declarations = body["tools"][0]["functionDeclarations"]
    assert [d["parametersJsonSchema"] for d in declarations] == [s["parameters"] for s in schemas]
    assert schemas == original
    assert "model" not in body and "stream" not in body


@pytest.mark.parametrize("choice, expected", [("auto", "AUTO"), ("none", "NONE"), ("required", "ANY"), (None, None)])
def test_google_tool_choice_and_missing_tools(choice, expected):
    requests = []
    agent = GoogleGenerateContentAgent(
        GoogleGenerateContentConfig(model="m", tool_choice=choice),
        transport=lambda url, headers, body, timeout: requests.append(body) or generation([{"text": "ok"}]),
    )
    messages = [{"role": "user", "content": "go"}]
    agent.respond(messages=messages, tools=tool_schemas())
    if expected is None:
        assert "toolConfig" not in requests[0]
    else:
        assert requests[0]["toolConfig"]["functionCallingConfig"]["mode"] == expected
    agent.respond(messages=messages, tools=[])
    assert "tools" not in requests[1] and "toolConfig" not in requests[1]
    assert "generationConfig" not in requests[1]


def test_google_response_separates_thoughts_and_normalizes_usage():
    parts = [
        {"text": "thinking", "thought": True},
        {"text": "ready", "thoughtSignature": "text-signature"},
        {"functionCall": {"id": "native-id", "name": "get_adventurers", "args": {}}, "thoughtSignature": "call-signature"},
        function_part("end_turn", {"hunts": []}),
    ]
    native = generation(parts, usageMetadata={
        "promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 30,
        "cachedContentTokenCount": 40, "totalTokenCount": 150,
    })
    response = GoogleGenerateContentAgent(
        GoogleGenerateContentConfig(model="m"), transport=lambda *args: native,
    ).respond(messages=[{"role": "user", "content": "go"}], tools=[])
    assert response.text == "ready"
    assert response.assistant_metadata["reasoning_content"] == "thinking"
    assert response.assistant_metadata["google_content_parts"] == parts
    assert [(c.name, c.call_id, dict(c.arguments)) for c in response.tool_calls] == [
        ("get_adventurers", "native-id", {}), ("end_turn", None, {"hunts": []}),
    ]
    assert response.usage["input_tokens"] == 100
    assert response.usage["output_tokens"] == 50
    assert response.usage["total_tokens"] == 150
    assert response.usage["cache_read_input_tokens"] == 40
    assert response.usage["output_tokens_details"]["reasoning_tokens"] == 30
    assert response.raw == native


@pytest.mark.parametrize("streaming", [False, True])
def test_google_tool_roundtrip_preserves_parts_and_function_response_ids(streaming):
    requests = []
    parts = [
        {"text": "thinking", "thought": True, "thoughtSignature": "reasoning-signature"},
        {"functionCall": {"id": "native-id", "name": "get_adventurers", "args": {}}, "thoughtSignature": "call-signature"},
        function_part("get_upgrades", {}),
    ]

    def transport(url, headers, body, timeout):
        requests.append(deepcopy(body))
        return generation(parts if len(requests) == 1 else [function_part("end_turn", {"hunts": []})])

    def stream_transport(*args):
        response = transport(*args)
        for part in response["candidates"][0]["content"]["parts"]:
            yield {"candidates": [{"content": {"role": "model", "parts": [part]}}]}
        yield {"candidates": [{"finishReason": "STOP"}]}

    agent = GoogleGenerateContentAgent(
        GoogleGenerateContentConfig(model="m"), transport=transport, stream_transport=stream_transport,
    )
    if not streaming:
        agent.respond_stream = None
    tools = GuildManagerTools.from_data_dir()
    tools.start_session("google-roundtrip")
    trace = run_llm_turn(agent, tools, "google-roundtrip", config=LlmRunConfig(archive_dir=None))
    assert trace.status == "completed"
    assert len(requests) == 2
    contents = requests[1]["contents"]
    assert contents[-2] == {"role": "model", "parts": parts}
    results = contents[-1]
    assert results["role"] == "user"
    first, second = [p["functionResponse"] for p in results["parts"]]
    assert first["name"] == "get_adventurers" and first["id"] == "native-id"
    assert second["name"] == "get_upgrades" and "id" not in second
    assert isinstance(first["response"]["output"], str) and first["response"]["output"]
    assert trace.model_responses[0].assistant_metadata["google_content_parts"] == parts


def test_google_stream_accumulates_text_usage_and_preserves_signature_only_parts():
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "think", "thought": True}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "hel"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "lo"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "", "thoughtSignature": "late-signature"}]}}]},
        {"candidates": [{"content": {"parts": [function_part("end_turn", {"hunts": []}, thoughtSignature="tool-signature")]}}]},
        {"candidates": [{"finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3, "thoughtsTokenCount": 2, "totalTokenCount": 15}},
    ]
    events = []
    requests = []

    def transport(url, headers, body, timeout):
        requests.append((url, headers, body))
        return chunks

    agent = GoogleGenerateContentAgent(GoogleGenerateContentConfig(model="m"), stream_transport=transport)
    response = agent.respond_stream(messages=[{"role": "user", "content": "go"}], tools=tool_schemas(), event_sink=events.append)
    assert response.text == "hello"
    assert response.assistant_metadata["reasoning_content"] == "think"
    assert response.tool_calls[0].name == "end_turn"
    assert response.usage["output_tokens"] == 5
    assert requests[0][0].endswith("/models/m:streamGenerateContent?alt=sse")
    assert requests[0][1]["Accept"] == "text/event-stream"
    assert "stream" not in requests[0][2]
    assert response.assistant_metadata["google_content_parts"][3] == {"text": "", "thoughtSignature": "late-signature"}
    assert [e["text"] for e in events if e["type"] == "model_delta"] == ["hel", "lo"]
    assert [e["text"] for e in events if e["type"] == "model_reasoning_delta"] == ["think"]
    assert len([e for e in events if e["type"] == "tool_call_delta"]) == 1
    assert events[-1]["type"] == "model_stream_completed"
    assert events[-1]["chunk_count"] == 6 and events[-1]["finish_reason"] == "STOP"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("payload, message", [
    ({"error": {"code": 400, "message": "invalid model"}}, "invalid model"),
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "SAFETY"),
    ({"candidates": [{"finishReason": "MALFORMED_FUNCTION_CALL"}]}, "MALFORMED_FUNCTION_CALL"),
    ({}, "no candidates"),
    (generation([{"functionCall": {"name": "end_turn", "args": "bad"}}]), "args"),
])
def test_google_invalid_or_blocked_responses_raise(streaming, payload, message):
    agent = GoogleGenerateContentAgent(
        GoogleGenerateContentConfig(model="m"), transport=lambda *args: payload,
        stream_transport=lambda *args: [payload],
    )
    respond = agent.respond_stream if streaming else agent.respond
    with pytest.raises(GoogleGenerateContentError, match=message):
        respond(messages=[{"role": "user", "content": "go"}], tools=[])


@pytest.mark.parametrize("streaming", [False, True])
def test_google_empty_max_tokens_response_can_be_retried_without_empty_model_content(streaming):
    requests = []

    def transport(url, headers, body, timeout):
        requests.append(body)
        if len(requests) == 1:
            return {"candidates": [{"finishReason": "MAX_TOKENS"}]}
        assert all(content["parts"] for content in body["contents"])
        assert [content["role"] for content in body["contents"]] == ["user"]
        assert len(body["contents"][0]["parts"]) == 2
        return generation([function_part("end_turn", {"hunts": []})])

    agent = GoogleGenerateContentAgent(
        GoogleGenerateContentConfig(model="m"), transport=transport,
        stream_transport=lambda *args: [transport(*args)],
    )
    if not streaming:
        agent.respond_stream = None
    tools = GuildManagerTools.from_data_dir()
    tools.start_session("empty-response")
    trace = run_llm_turn(agent, tools, "empty-response", config=LlmRunConfig(archive_dir=None))
    assert trace.status == "completed"
    assert len(requests) == 2


def test_google_truncated_stream_raises_instead_of_executing_partial_calls():
    payload = {"candidates": [{"content": {"parts": [function_part("end_turn", {"hunts": []})]}}]}
    agent = GoogleGenerateContentAgent(
        GoogleGenerateContentConfig(model="m"), stream_transport=lambda *args: [payload],
    )
    with pytest.raises(GoogleGenerateContentError, match="without finishReason"):
        agent.respond_stream(messages=[{"role": "user", "content": "go"}], tools=[])


def test_google_stream_usage_only_chunks_are_cumulative():
    chunks = [
        generation([{"text": "ok"}], usageMetadata={"promptTokenCount": 10, "candidatesTokenCount": 1}),
        {"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2, "thoughtsTokenCount": 3}},
    ]
    agent = GoogleGenerateContentAgent(
        GoogleGenerateContentConfig(model="m"), stream_transport=lambda *args: chunks,
    )
    response = agent.respond_stream(messages=[{"role": "user", "content": "go"}], tools=[])
    assert response.usage["input_tokens"] == 10
    assert response.usage["output_tokens"] == 5


def test_google_stream_without_usage_does_not_invent_usage():
    agent = GoogleGenerateContentAgent(
        GoogleGenerateContentConfig(model="m"), stream_transport=lambda *args: [generation([{"text": "ok"}])],
    )
    assert agent.respond_stream(messages=[{"role": "user", "content": "go"}], tools=[]).usage == {}


@pytest.mark.parametrize("streaming", [False, True])
def test_google_http_transport_and_sse_framing(monkeypatch, streaming):
    payload = generation([function_part("end_turn", {"hunts": []})])
    requests = []
    if streaming:
        # SSE 允许同一事件由多行 data 构成。
        wire = (": keepalive\nevent: message\n" + "\n".join("data: " + line for line in json.dumps(payload, indent=2).splitlines()) + "\n\n").encode()
    else:
        wire = json.dumps(payload).encode()

    def urlopen(request, timeout):
        requests.append(request)
        return io.BytesIO(wire)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    agent = GoogleGenerateContentAgent(GoogleGenerateContentConfig(model="m", api_key="secret", session_id="initial"))
    agent.bind_session("bound")
    respond = agent.respond_stream if streaming else agent.respond
    response = respond(messages=[{"role": "user", "content": "go"}], tools=[])
    assert response.tool_calls[0].name == "end_turn"
    assert requests[0].get_header("X-goog-api-key") == "secret"
    assert requests[0].get_header("X-opencode-session") == "bound"
    assert requests[0].get_method() == "POST"
    assert "contents" in json.loads(requests[0].data)


@pytest.mark.parametrize("streaming", [False, True])
def test_google_http_errors_preserve_status_and_detail(monkeypatch, streaming):
    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, io.BytesIO(b"denied"))

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    agent = GoogleGenerateContentAgent(GoogleGenerateContentConfig(model="m"))
    respond = agent.respond_stream if streaming else agent.respond
    with pytest.raises(GoogleGenerateContentError, match="HTTP 403: denied"):
        respond(messages=[{"role": "user", "content": "go"}], tools=[])


def test_google_reasoning_effort_is_recorded_in_archive(tmp_path):
    agent = GoogleGenerateContentAgent(
        GoogleGenerateContentConfig(model="gemini-test", reasoning_effort="high", api_key="do-not-archive"),
        stream_transport=lambda *args: [generation([function_part("end_turn", {"hunts": []})])],
    )
    run = run_llm_game(agent, config=LlmRunConfig(archive_dir=tmp_path), data_dir=Path("data/presets/default"))
    assert run.status == "completed"
    replay = json.loads((Path(run.archive_dir) / "replay.json").read_text(encoding="utf-8"))
    assert replay["agent"]["config"]["reasoning_effort"] == "high"
    assert "api_key" not in replay["agent"]["config"]


@pytest.mark.parametrize("streaming", [False, True])
def test_google_complete_game_binds_session_and_records_usage(streaming):
    requests = []
    payload = generation([function_part("end_turn", {"hunts": []})], usageMetadata={"promptTokenCount": 10, "candidatesTokenCount": 3, "thoughtsTokenCount": 2})

    def transport(url, headers, body, timeout):
        requests.append(dict(headers))
        return payload

    agent = GoogleGenerateContentAgent(
        GoogleGenerateContentConfig(model="m"), transport=transport,
        stream_transport=lambda *args: [transport(*args)],
    )
    if not streaming:
        agent.respond_stream = None
    run = run_llm_game(
        agent, data_dir=Path("data/presets/default"), session_id="google-game",
        config=LlmRunConfig(archive_dir=None),
    )
    assert run.status == "completed" and len(run.turns) == 8
    assert all(h["x-opencode-session"] == "google-game" for h in requests)
    records = [record for turn in run.turns for record in turn.model_responses]
    assert sum(record.usage["input_tokens"] for record in records) == 80
    assert sum(record.usage["output_tokens"] for record in records) == 40
