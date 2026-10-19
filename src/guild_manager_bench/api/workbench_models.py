"""运行请求契约。任务凭证仅在内存和工作进程标准输入中传递。"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from guild_manager_bench.bench.llm.prompts import DEFAULT_OBJECTIVE


class RunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=120)
    preset: str = Field(min_length=1, max_length=80)
    provider: Literal["openai", "openai-responses", "anthropic", "google"] = "openai"
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: SecretStr | None = None
    profile_id: str | None = Field(default=None, min_length=1, max_length=80)
    reasoning_effort: str | None = Field(default=None, max_length=30)
    thinking: bool | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=1000000)
    timeout: float = Field(default=180, ge=1, le=3600)
    objective: str = Field(default=DEFAULT_OBJECTIVE, min_length=1, max_length=16000)
    max_tool_calls_per_turn: int = Field(default=20, ge=0, le=1000)
    game_seed: int | None = None
    scoring_seed: int | None = None
    resume_run_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def profile_connection(self):
        if self.profile_id and (self.base_url is not None or self.api_key is not None):
            raise ValueError("使用 API 配置时，地址和密钥由服务端配置提供，请勿同时传入")
        return self

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("服务地址必须是完整的 HTTP(S) URL")
        _ = parsed.port  # 拒绝非数字端口和超出范围的端口。
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("服务地址不能包含认证信息、查询参数或片段，请使用 API Key 字段")
        return value.rstrip("/")

    @field_validator("resume_run_id")
    @classmethod
    def safe_run_id(cls, value: str | None) -> str | None:
        if value is not None and (
            not value or value in {".", ".."} or any(c in value for c in "/\\")
        ):
            raise ValueError("无效的存档标识")
        return value


class CreateBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    runs: list[RunSpec] = Field(min_length=1, max_length=64)


class SchedulerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_parallel: int = Field(default=2, ge=1, le=8)
    per_endpoint: int = Field(default=2, ge=1, le=8)
