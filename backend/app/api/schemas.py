"""HTTP API 的请求和响应数据契约。"""

import uuid
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


BROWSER_SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


def validate_http_url(
    value: str,
    *,
    field_name: str = "api_url",
    strip_trailing_slash: bool = True,
) -> str:
    """规范化 HTTP(S) 地址，拒绝没有主机名的值。"""
    normalized = value.strip()
    if strip_trailing_slash:
        normalized = normalized.rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a valid HTTP(S) URL")
    return normalized


def validate_nonempty_secret(value: object) -> str:
    """拒绝空密钥，并返回去除首尾空白后的值。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("api_key must not be empty")
    return value.strip()


class AgentRunRequest(BaseModel):
    """在同一 conversation_id 下发送一条新的用户消息。"""

    message: str = Field(min_length=1)
    conversation_id: str = Field(
        default="default",
        pattern=BROWSER_SESSION_ID_PATTERN,
    )
    browser_session_id: str = Field(
        default="browser-agent-main",
        min_length=1,
        pattern=BROWSER_SESSION_ID_PATTERN,
    )
    llm_endpoint_id: str | None = Field(
        default=None,
        pattern=BROWSER_SESSION_ID_PATTERN,
    )
    llm_model: str | None = Field(default=None, min_length=1, max_length=256)
    run_id: str = Field(
        default_factory=lambda: f"run-{uuid.uuid4()}",
        pattern=BROWSER_SESSION_ID_PATTERN,
    )

    @model_validator(mode="after")
    def validate_llm_selection(self):
        """调用方和模型必须成对出现，避免静默选错模型。"""
        if (self.llm_endpoint_id is None) != (self.llm_model is None):
            raise ValueError(
                "llm_endpoint_id and llm_model must be provided together"
            )
        if self.llm_model is not None:
            self.llm_model = self.llm_model.strip()
            if not self.llm_model:
                raise ValueError("llm_model must not be empty")
        return self


class LLMConfigRequest(BaseModel):
    """前端提交的 OpenAI 兼容模型连接配置。"""

    api_url: str = Field(min_length=1, max_length=2_048)
    api_key: SecretStr
    model: str = Field(min_length=1, max_length=256)

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        return validate_http_url(value)

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, value: object) -> str:
        return validate_nonempty_secret(value)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model must not be empty")
        return normalized


class LLMConfigResult(BaseModel):
    """确认生效的非敏感配置；响应中不包含 API Key。"""

    configured: bool = True
    api_url: str
    model: str


class LLMModelDiscoveryRequest(BaseModel):
    """调用兼容服务的 Models API 自动发现可选模型。"""

    api_url: str = Field(min_length=1, max_length=2_048)
    api_key: SecretStr

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        return validate_http_url(value)

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, value: object) -> str:
        return validate_nonempty_secret(value)


class LLMModelDiscoveryResult(BaseModel):
    models: list[str]


class LLMEndpointConfigRequest(BaseModel):
    """一个调用方地址和用户勾选启用的模型。"""

    id: str = Field(pattern=BROWSER_SESSION_ID_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    api_url: str = Field(min_length=1, max_length=2_048)
    api_key: SecretStr
    models: list[str] = Field(min_length=1, max_length=256)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be empty")
        return normalized

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        return validate_http_url(value)

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, value: object) -> str:
        return validate_nonempty_secret(value)

    @field_validator("models")
    @classmethod
    def validate_models(cls, value: list[str]) -> list[str]:
        normalized = []
        for model in value:
            model_id = model.strip()
            if model_id and model_id not in normalized:
                normalized.append(model_id)
        if not normalized:
            raise ValueError("at least one model must be enabled")
        return normalized


class LLMEndpointsConfigRequest(BaseModel):
    endpoints: list[LLMEndpointConfigRequest] = Field(
        min_length=1,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_unique_ids(self):
        endpoint_ids = [endpoint.id for endpoint in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("endpoint ids must be unique")
        return self


class LLMEndpointConfigResult(BaseModel):
    id: str
    name: str
    api_url: str
    models: list[str]


class LLMEndpointsConfigResult(BaseModel):
    configured: bool = True
    endpoints: list[LLMEndpointConfigResult]


class PageSuggestionsRequest(BaseModel):
    """当前页面的精简文本，仅用于生成首页快捷建议。"""

    url: str = Field(max_length=2_048)
    title: str = Field(max_length=512)
    content: str = Field(min_length=1, max_length=12_000)
    locale: str = Field(default="zh-CN", max_length=32)
    limit: int = Field(default=3, ge=2, le=3)


class PageSuggestionsResult(BaseModel):
    suggestions: list[str] = Field(min_length=2, max_length=3)


class BrowserSessionStartRequest(BaseModel):
    """请求显式启动或接管一个浏览器会话。"""

    browser_session_id: str = Field(pattern=BROWSER_SESSION_ID_PATTERN)
    mode: Literal["current", "isolated", "existing"] = "isolated"
    cdp_url: str | None = None
    expected_url: str | None = Field(default=None, max_length=2_048)

    @model_validator(mode="after")
    def validate_mode_settings(self):
        """校验显式浏览器选择所需的目标参数。"""
        if self.mode == "existing" and not self.cdp_url:
            raise ValueError("cdp_url is required when mode is 'existing'")
        if self.mode in {"current", "isolated"} and self.cdp_url is not None:
            raise ValueError("cdp_url is only valid when mode is 'existing'")
        if self.expected_url is not None:
            normalized = validate_http_url(
                self.expected_url,
                field_name="expected_url",
                strip_trailing_slash=False,
            )
            if self.mode != "current":
                raise ValueError("expected_url is only valid when mode is 'current'")
            self.expected_url = normalized
        return self


class BrowserSessionResult(BaseModel):
    """浏览器会话完成启动和探测后的状态。"""

    browser_session_id: str
    mode: Literal["current", "isolated", "existing"]
    ownership: Literal["backend", "external"]
    status: Literal["starting", "ready", "disconnected", "error", "closed"]
    ready: bool
    url: str | None
