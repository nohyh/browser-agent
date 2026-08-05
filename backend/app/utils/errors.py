"""跨 LLM 适配器共享的错误分类。"""

from typing import Any, cast

from openai import APIConnectionError, APITimeoutError


TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

ERROR_CODES = frozenset(
    {
        "provider_output",
        "provider_output_invalid_json",
        "provider_output_schema_validation",
        "provider_transport",
        "browser_transport",
        "session_disconnected",
        "tool_timeout",
        "invalid_tool_arguments",
        "stale_element_ref",
        "task_cancelled",
        "runtime_not_ready",
        "llm_not_configured",
        "llm_selection_not_configured",
        "browser_session_not_ready",
        "pending_mutation",
        "action_observation_required",
        "read_only_completion_evidence_required",
        "navigation_completion_evidence_required",
        "action_failed",
        "action_uncertain",
        "repeated_action",
    }
)


class ToolValidationError(ValueError):
    """MCP inputSchema 本地校验失败。"""

    code = "invalid_tool_arguments"
    retryable = False

    def __init__(
        self,
        tool_name: str,
        arguments: dict[str, object],
        details: dict[str, Any],
    ):
        self.tool_name = tool_name
        self.arguments = arguments
        self.details = details
        super().__init__(
            f"Invalid arguments for {tool_name}: "
            f"{details}"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "type": type(self).__name__,
            "code": self.code,
            "message": str(self),
            "tool_name": self.tool_name,
            "details": self.details,
            "retryable": self.retryable,
        }


def is_transient_error(exc: object) -> bool:
    """识别可安全重试一次的网络错误和服务端状态码。"""
    return (
        isinstance(exc, (APIConnectionError, APITimeoutError))
        or getattr(exc, "status_code", None) in TRANSIENT_STATUS_CODES
    )


def exception_details(exc: Exception) -> dict[str, object]:
    """把任意异常转换成可记录、可序列化的稳定结构。"""
    serializer = getattr(exc, "as_dict", None)
    if callable(serializer):
        details = serializer()
        if isinstance(details, dict):
            details.setdefault(
                "code",
                getattr(exc, "code", None)
                or getattr(exc, "error_type", None)
                or type(exc).__name__,
            )
            return cast(dict[str, object], details)
        return {"code": type(exc).__name__, "message": str(exc)}
    code = (
        getattr(exc, "code", None)
        or getattr(exc, "error_type", None)
        or (
            "tool_timeout"
            if isinstance(exc, TimeoutError)
            else "browser_transport"
            if isinstance(exc, (ConnectionError, EOFError))
            else type(exc).__name__
        )
    )
    return {
        "type": type(exc).__name__,
        "code": code,
        "message": str(exc) or type(exc).__name__,
        "retryable": bool(
            getattr(exc, "retryable", isinstance(exc, TimeoutError))
        ),
    }
