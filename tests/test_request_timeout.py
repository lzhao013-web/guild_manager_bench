import pytest

from guild_manager_bench.api.routes_llm_debug import _debug_agent
from guild_manager_bench.api.workbench_models import RunSpec
from guild_manager_bench.bench.llm import (
    AnthropicMessagesConfig,
    GoogleGenerateContentConfig,
    OpenAIChatCompletionsConfig,
    OpenAIResponsesConfig,
)

PROVIDERS = [
    (OpenAIChatCompletionsConfig, "OPENAI_TIMEOUT"),
    (OpenAIResponsesConfig, "OPENAI_RESPONSES_TIMEOUT"),
    (AnthropicMessagesConfig, "ANTHROPIC_TIMEOUT"),
    (GoogleGenerateContentConfig, "GOOGLE_TIMEOUT"),
]


@pytest.fixture(autouse=True)
def clear_timeouts(monkeypatch):
    for name in ("OPENAI_TIMEOUT", "OPENAI_COMPAT_TIMEOUT", "OPENAI_RESPONSES_TIMEOUT", "ANTHROPIC_TIMEOUT", "GOOGLE_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("config_type,environment", PROVIDERS)
def test_provider_default_timeout_is_600(config_type, environment):
    assert config_type(model="fixture").timeout == 600
    assert config_type.from_env(model="fixture", env_file=None).timeout == 600


@pytest.mark.parametrize("config_type,environment", PROVIDERS)
def test_explicit_and_environment_timeouts_keep_precedence(config_type, environment, monkeypatch, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{environment}=45\n", encoding="utf-8")
    assert config_type.from_env(model="fixture", env_file=dotenv).timeout == 45
    monkeypatch.setenv(environment, "90")
    assert config_type.from_env(model="fixture", env_file=dotenv).timeout == 90
    assert config_type.from_env(model="fixture", env_file=dotenv, timeout=180).timeout == 180


@pytest.mark.parametrize("provider", ["openai", "openai-responses", "anthropic", "google"])
def test_debug_request_default_and_explicit_timeout(provider):
    payload = {"provider": provider, "model": "fixture", "api_key": "fixture"}
    assert _debug_agent(payload).config.timeout == 600
    assert _debug_agent({**payload, "timeout": 180}).config.timeout == 180


def test_workbench_default_does_not_change_explicit_timeout():
    assert RunSpec(preset="advanced").timeout == 600
    assert RunSpec(preset="advanced", timeout=180).timeout == 180
