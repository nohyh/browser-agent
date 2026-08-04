"""跨 LLM 适配器共享的错误分类。"""

from openai import APIConnectionError, APITimeoutError


TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


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
        return serializer()
    return {
        "type": type(exc).__name__,
        "message": str(exc) or type(exc).__name__,
        "retryable": isinstance(exc, TimeoutError),
    }
