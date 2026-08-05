"""LLM 厂商协议适配，并向 Agent 暴露统一领域模型。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Protocol

from pydantic import AliasChoices, Field, ValidationError, field_validator

from app.models import AgentAction, AgentDecision
from app.utils.errors import is_transient_error


class _ProviderAgentAction(AgentAction):
    """严格输出边界使用字符串承载可变工具参数。"""

    # 部分兼容厂商把函数名写成 type 或 tool，边界层统一收敛为内部 name。
    name: str = Field(
        validation_alias=AliasChoices("name", "type", "tool")
    )
    arguments: str = Field(default="")

    @field_validator("arguments", mode="before")
    @classmethod
    def accept_compatible_object(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return value


class _ProviderAgentDecision(AgentDecision):
    """只用于模型传输；进入 Agent 前会还原为领域模型。"""

    actions: list[_ProviderAgentAction] = Field(
        default_factory=list,
        max_length=3,
    )


@dataclass(frozen=True)
class ProviderDecision:
    decision: AgentDecision
    raw_response: Any
    llm_calls: int = 1
    failed_llm_calls: int = 0
    usage_unavailable_calls: int | None = None
    additional_input_characters: int = 0


class ProviderOutputError(ValueError):
    """携带有限原文诊断，但不把大段响应写入异常正文。"""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        raw_output: str | None = None,
        repair_error: Exception | None = None,
        raw_response: Any = None,
        llm_calls: int = 1,
        usage_unavailable_calls: int = 1,
        additional_input_characters: int = 0,
    ):
        self.error_type = f"provider_output_{kind}"
        self.raw_response = raw_response
        self.llm_calls = llm_calls
        self.failed_llm_calls = llm_calls
        self.usage_unavailable_calls = usage_unavailable_calls
        self.additional_input_characters = additional_input_characters
        self.details = _output_diagnostics(kind, raw_output, repair_error)
        super().__init__(message)


class ProviderAdapter(Protocol):
    output_instructions: str

    async def decide(
        self,
        *,
        model: str,
        input_messages: list[dict[str, str]],
    ) -> ProviderDecision: ...


class OpenAIResponsesAdapter:
    """兼容严格和宽松实现的 OpenAI Responses 协议。"""

    DECISION_FORMAT = _ProviderAgentDecision
    REPAIR_OUTPUT_LIMIT = 12_000
    output_instructions = (
        "actions 中每个 arguments 必须是 JSON 对象字符串，例如 "
        '{"selector":"@e107"} 需要写成 '
        '"{\\"selector\\":\\"@e107\\"}"。'
    )

    def __init__(self, client: Any):
        self.client = client

    async def decide(
        self,
        *,
        model: str,
        input_messages: list[dict[str, str]],
    ) -> ProviderDecision:
        response = None
        try:
            response = await self.client.responses.parse(
                model=model,
                input=input_messages,
                text_format=self.DECISION_FORMAT,
            )
            if response.output_parsed is None:
                raise ValueError(
                    "OpenAI response did not contain an AgentDecision"
                )
            decision = self._to_agent_decision(response.output_parsed)
            return ProviderDecision(decision=decision, raw_response=response)
        except (ValidationError, ValueError) as exc:
            raw_output = (
                _raw_output_from_error(exc)
                or getattr(response, "output_text", None)
                or _serialize_output(
                    getattr(response, "output_parsed", None)
                )
            )
            if not raw_output:
                raise ProviderOutputError(
                    _validation_error_kind(exc),
                    "Provider returned an invalid structured decision",
                    raw_response=response,
                    usage_unavailable_calls=int(
                        getattr(response, "usage", None) is None
                    ),
                ) from exc
            return await self._recover_decision(
                model=model,
                raw_output=raw_output,
                validation_error=exc,
                raw_response=response,
            )

    async def _recover_decision(
        self,
        *,
        model: str,
        raw_output: str,
        validation_error: Exception,
        raw_response: Any,
    ) -> ProviderDecision:
        usage_missing = int(getattr(raw_response, "usage", None) is None)
        try:
            decision = self._decision_from_text(raw_output)
        except (ValidationError, ValueError):
            pass
        else:
            return ProviderDecision(
                decision=decision,
                raw_response=raw_response or SimpleNamespace(usage=None),
                usage_unavailable_calls=usage_missing,
            )

        repair_input = self._repair_input(raw_output, validation_error)
        repair_characters = sum(
            len(str(message.get("content", "")))
            for message in repair_input
        )
        network_failures = 0
        for attempt in range(2):
            try:
                repair_response = await self.client.responses.create(
                    model=model,
                    input=repair_input,
                )
            except Exception as exc:
                network_failures += 1
                if attempt == 0 and is_transient_error(exc):
                    continue
                raise ProviderOutputError(
                    _validation_error_kind(validation_error),
                    "Provider JSON repair failed",
                    raw_output=raw_output,
                    repair_error=exc,
                    raw_response=raw_response,
                    llm_calls=2 + attempt,
                    usage_unavailable_calls=usage_missing + network_failures,
                    additional_input_characters=(
                        repair_characters * (attempt + 1)
                    ),
                ) from exc

            try:
                decision = self._decision_from_text(
                    str(repair_response.output_text or "")
                )
            except (ValidationError, ValueError) as exc:
                raise ProviderOutputError(
                    _validation_error_kind(validation_error),
                    "Provider returned invalid JSON and repair was also invalid",
                    raw_output=raw_output,
                    repair_error=exc,
                    raw_response=repair_response,
                    llm_calls=2 + attempt,
                    usage_unavailable_calls=(
                        usage_missing
                        + network_failures
                        + int(repair_response.usage is None)
                    ),
                    additional_input_characters=(
                        repair_characters * (attempt + 1)
                    ),
                ) from exc

            return ProviderDecision(
                decision=decision,
                raw_response=repair_response,
                llm_calls=2 + attempt,
                failed_llm_calls=1 + network_failures,
                usage_unavailable_calls=(
                    usage_missing
                    + network_failures
                    + int(repair_response.usage is None)
                ),
                additional_input_characters=(
                    repair_characters * (attempt + 1)
                ),
            )
        raise RuntimeError("unreachable")

    def _decision_from_text(self, output: str) -> AgentDecision:
        transport = self.DECISION_FORMAT.model_validate_json(
            _unwrap_decision_payload(_normalize_json_text(output))
        )
        return self._to_agent_decision(transport)

    def _repair_input(
        self,
        raw_output: str,
        validation_error: Exception,
    ) -> list[dict[str, str]]:
        payload = json.dumps(
            {
                "invalid_output": raw_output[: self.REPAIR_OUTPUT_LIMIT],
                "validation_error": str(validation_error)[:1_000],
            },
            ensure_ascii=False,
        )
        return [
            {
                "role": "system",
                "content": (
                    "只修复浏览器 Agent 决策的 JSON 格式，不改变事实、状态、"
                    "动作和最终答案。必须只返回一个合法 JSON 对象，不要使用 "
                    "Markdown，不要把内容包在 decision、data、result 等字段里。"
                    "顶层必须包含 status(continue|completed|blocked)、"
                    "evaluation_previous_goal、memory。"
                    "continue 还需 next_goal 和 1-3 个 actions；actions 使用 "
                    "name 字段，arguments 必须是 JSON 对象字符串。"
                    "completed/blocked 还需 completion_evidence 和 "
                    "final_answer，且不能有 actions。"
                    "evaluation_previous_goal 或 memory 缺失时，根据原输出"
                    "补写简洁的进度文本。示例：{\"status\":\"continue\","
                    "\"evaluation_previous_goal\":\"...\",\"memory\":\"...\","
                    "\"next_goal\":\"...\",\"actions\":[{\"name\":"
                    "\"agent_browser_open\",\"arguments\":"
                    "\"{\\\"url\\\":\\\"https://x.com\\\"}\"}]}"
                ),
            },
            {"role": "user", "content": payload},
        ]

    @classmethod
    def _to_agent_decision(cls, output: Any) -> AgentDecision:
        if isinstance(output, AgentDecision) and not isinstance(
            output,
            cls.DECISION_FORMAT,
        ):
            return output

        transport = cls.DECISION_FORMAT.model_validate(output)
        actions = []
        for action in transport.actions:
            try:
                arguments = json.loads(action.arguments)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Action {action.name!r} arguments must be valid JSON"
                ) from exc
            if not isinstance(arguments, dict):
                raise ValueError(
                    f"Action {action.name!r} arguments must decode to an object"
                )
            actions.append(
                AgentAction(
                    name=action.name,
                    arguments=arguments,
                    observation_id=action.observation_id,
                    observation_revision=action.observation_revision,
                )
            )

        payload = transport.model_dump(exclude={"actions"})
        payload["actions"] = actions
        return AgentDecision.model_validate(payload)


def _normalize_json_text(output: str) -> str:
    """只移除传输包装，不猜测缺失的业务字段。"""
    candidate = output.lstrip("\ufeff").strip()
    if candidate.startswith("```"):
        candidate = candidate.partition("\n")[2]
        if candidate.rstrip().endswith("```"):
            candidate = candidate.rstrip()[:-3].rstrip()
    start, end = candidate.find("{"), candidate.rfind("}")
    return candidate[start : end + 1] if start >= 0 and end >= start else candidate


DECISION_WRAPPER_KEYS = frozenset(
    {"decision", "data", "result", "output", "response"}
)


def _unwrap_decision_payload(output: str) -> str:
    """兼容模型把整个决策包在单键包装里的输出，如 {\"decision\": {...}}。"""
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return output
    if not isinstance(payload, dict) or len(payload) != 1:
        return output
    for key in DECISION_WRAPPER_KEYS:
        wrapped = payload.get(key)
        if isinstance(wrapped, dict):
            return json.dumps(wrapped, ensure_ascii=False)
    return output


def _raw_output_from_error(exc: Exception) -> str | None:
    if not isinstance(exc, ValidationError):
        return None
    for error in exc.errors(include_input=True):
        value = error.get("input")
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)
    return None


def _serialize_output(output: Any) -> str | None:
    if output is None:
        return None
    if hasattr(output, "model_dump"):
        output = output.model_dump()
    return json.dumps(output, ensure_ascii=False, default=str)


def _validation_error_kind(exc: Exception) -> str:
    if isinstance(exc, ValidationError) and any(
        error.get("type") == "json_invalid" for error in exc.errors()
    ):
        return "invalid_json"
    return "schema_validation"


def _output_diagnostics(
    kind: str,
    raw_output: str | None,
    repair_error: Exception | None,
) -> dict[str, Any]:
    raw_output = raw_output or ""
    details: dict[str, Any] = {
        "provider_error_type": kind,
        "raw_output_characters": len(raw_output),
        "raw_output_sha256": hashlib.sha256(
            raw_output.encode("utf-8")
        ).hexdigest(),
        "raw_output_preview": raw_output[:4_000],
        "repair_attempted": repair_error is not None,
        "repair_succeeded": False,
    }
    if repair_error is not None:
        details["repair_error_type"] = type(repair_error).__name__
        details["repair_error"] = str(repair_error)[:1_000]
    return details
