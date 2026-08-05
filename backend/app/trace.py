"""浏览器 Agent 的诊断事件记录与脱敏。"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Callable, Iterable

from app.utils.values import compact_value, extract_snapshot


TRACE_SCHEMA_VERSION = 1
DEFAULT_TRACE_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TRACE_RETENTION_DAYS = 14
DEFAULT_TRACE_MAX_TOTAL_BYTES = 100 * 1024 * 1024

SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "challenge",
    "cookie",
    "js_challenge",
    "jsc_orig_r",
    "password",
    "secret",
    "solution",
    "token",
    "value",
}
URL_KEYS = {
    "cdp_url",
    "expected_url",
    "href",
    "redirect_uri",
    "target",
    "url",
    "uri",
}
SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?i)([?&](?:access_token|api_key|apikey|authorization|challenge|"
    r"cookie|js_challenge|jsc_orig_r|password|secret|solution|token)=)"
    r"[^&#\s\"']+"
)
SECRET_SHAPED_PATTERN = re.compile(
    r"(?i)\b(?:sk|pk|rk|ghp|github_pat|xox[baprs]-|AIza)[-_A-Za-z0-9]{12,}\b"
    r"|\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"
    r"|\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"
)
SECRET_PATH_SEGMENTS = frozenset(
    {"auth", "challenge", "invite", "password", "reset", "secret", "token"}
)
BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _redact_secret_text(value: str) -> str:
    redacted = SENSITIVE_QUERY_PATTERN.sub(r"\1[REDACTED]", value)
    return SECRET_SHAPED_PATTERN.sub("[REDACTED]", redacted)


def _redact_url(value: str) -> str:
    redacted = SENSITIVE_QUERY_PATTERN.sub(r"\1[REDACTED]", value)
    try:
        parts = urlsplit(redacted)
    except ValueError:
        return _redact_secret_text(redacted)
    path_parts = parts.path.split("/")
    for index, path_part in enumerate(path_parts):
        previous = path_parts[index - 1].casefold() if index else ""
        if (
            previous in SECRET_PATH_SEGMENTS
            or SECRET_SHAPED_PATTERN.search(path_part) is not None
        ):
            path_parts[index] = "[REDACTED]"
    fragment = "[REDACTED]" if parts.fragment else ""
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            "/".join(path_parts),
            parts.query,
            fragment,
        )
    )


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_secret_text(value)
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if str(key).lower() in SENSITIVE_KEYS
                else _redact_url(item)
                if str(key).lower() in URL_KEYS and isinstance(item, str)
                else redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """按工具语义隐藏输入值、脚本和认证参数。"""
    normalized_name = tool_name.casefold()
    value_keys = {"input", "input_value", "text", "value"}
    if any(marker in normalized_name for marker in ("fill", "type", "input")):
        value_keys.update({"content", "data"})
    safe_arguments: dict[str, Any] = {}
    for key, value in arguments.items():
        normalized_key = str(key).casefold()
        if normalized_key in SENSITIVE_KEYS or normalized_key in value_keys:
            safe_arguments[key] = "[REDACTED]"
        elif normalized_key in {"script", "expression"} and (
            "eval" in normalized_name or "function" in normalized_name
        ):
            safe_arguments[key] = "[REDACTED_SCRIPT]"
        elif normalized_key in URL_KEYS and isinstance(value, str):
            safe_arguments[key] = _redact_url(value)
        else:
            safe_arguments[key] = redact_value(value)
    return safe_arguments


def cleanup_trace_directory(
    directory: Path,
    *,
    retention_days: int = DEFAULT_TRACE_RETENTION_DAYS,
    max_total_bytes: int = DEFAULT_TRACE_MAX_TOTAL_BYTES,
    protected_paths: Iterable[Path] = (),
) -> dict[str, int]:
    """按时间和总容量清理已完成的 Markdown/JSONL 轨迹文件。"""
    if not directory.exists():
        return {"deleted_files": 0, "deleted_bytes": 0}
    protected = {path.resolve() for path in protected_paths}
    candidates = [
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix in {".jsonl", ".md"}
    ]
    cutoff = datetime.now(timezone.utc).timestamp() - max(retention_days, 0) * 86400
    deleted_files = 0
    deleted_bytes = 0

    for path in sorted(candidates, key=lambda item: item.stat().st_mtime):
        if path.resolve() in protected or path.stat().st_mtime >= cutoff:
            continue
        size = path.stat().st_size
        path.unlink()
        deleted_files += 1
        deleted_bytes += size

    remaining = [
        path
        for path in candidates
        if path.exists() and path.resolve() not in protected
    ]
    total_bytes = sum(path.stat().st_size for path in remaining)
    for path in sorted(remaining, key=lambda item: item.stat().st_mtime):
        if total_bytes <= max_total_bytes:
            break
        size = path.stat().st_size
        path.unlink()
        total_bytes -= size
        deleted_files += 1
        deleted_bytes += size
    return {"deleted_files": deleted_files, "deleted_bytes": deleted_bytes}


class TraceRecorder:
    def __init__(
        self,
        trace_file: Path | None,
        tool_outcome: Callable[..., dict[str, Any]] | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        retain_events: bool = True,
        *,
        jsonl_file: Path | None = None,
        conversation_id: str | None = None,
        max_bytes: int = DEFAULT_TRACE_MAX_BYTES,
    ):
        self.events: list[dict[str, Any]] = []
        self.trace_file = trace_file
        self.jsonl_file = jsonl_file or (
            trace_file.with_suffix(".jsonl") if trace_file is not None else None
        )
        self._tool_outcome = tool_outcome
        self.event_sink = event_sink
        self.retain_events = retain_events
        self.conversation_id = conversation_id
        self.max_bytes = max(1, max_bytes)
        self._sequence = 0
        self._last_snapshot_hash: str | None = None
        if self.trace_file is not None:
            self.trace_file.parent.mkdir(parents=True, exist_ok=True)
        if self.jsonl_file is not None:
            self.jsonl_file.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict[str, Any]) -> None:
        """写入去重、脱敏后的诊断事件，避免日志反向复制完整上下文。"""
        event = self._prepare_trace_event(event)
        self._sequence += 1
        record = {
            **event,
            "schema_version": TRACE_SCHEMA_VERSION,
            "sequence": self._sequence,
            "timestamp": datetime.now(BEIJING_TIMEZONE).isoformat(),
            "conversation_id": event.get("conversation_id") or self.conversation_id,
            "run_id": event.get("run_id"),
            "step_id": event.get("step_id"),
            "action_id": event.get("action_id"),
            "observation_id": event.get("observation_id"),
            "phase": event.get("phase") or self._phase_for(event),
            "duration_ms": event.get("duration_ms"),
        }
        if self.retain_events:
            self.events.append(record)
        if self.event_sink is not None:
            # 实时轨迹只发布已经过脱敏和压缩的记录，回调异常不能打断任务。
            try:
                self.event_sink(record.copy())
            except Exception:
                pass
        self._write_jsonl(record)
        self._write_markdown(record)

    @staticmethod
    def _phase_for(event: dict[str, Any]) -> str:
        return {
            "llm_call": "llm_request",
            "llm_attempt": "provider_repair",
            "tool_call": "tool_dispatch",
            "tool_result": "tool_dispatch",
            "browser_transport": event.get("phase") or "mcp_response",
            "token_usage": "token_usage",
            "browser_session": "runtime",
            "mutation_intent": "mutation",
            "step_failure": "step_failure",
        }.get(str(event.get("type")), "conversation")

    def _write_jsonl(self, record: dict[str, Any]) -> None:
        if self.jsonl_file is None:
            return
        payload = (
            json.dumps(record, ensure_ascii=False, default=str, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        if self._append_bounded(self.jsonl_file, payload):
            return
        marker = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "sequence": record["sequence"],
            "timestamp": record["timestamp"],
            "conversation_id": record.get("conversation_id"),
            "type": "trace_event_dropped",
            "dropped_type": record.get("type"),
        }
        marker_payload = (
            json.dumps(marker, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if not self.jsonl_file.exists() or self.jsonl_file.stat().st_size == 0:
            self._append_bounded(self.jsonl_file, marker_payload)

    def _write_markdown(self, record: dict[str, Any]) -> None:
        if self.trace_file is None:
            return
        event_type = record["type"]
        if event_type == "message":
            title = "用户消息" if record.get("role") == "user" else "助手消息"
        elif event_type == "tool_call":
            title = f"工具调用：{record.get('name', 'unknown')}"
        elif event_type == "tool_result":
            title = f"工具结果：{record.get('name', 'unknown')}"
        elif event_type == "llm_call":
            title = "LLM 输入"
        elif event_type == "llm_result":
            title = "LLM 输出"
        elif event_type == "llm_attempt":
            title = "LLM 尝试"
        elif event_type == "token_usage":
            title = "Token 使用"
        elif event_type == "browser_session":
            title = "浏览器会话"
        elif event_type == "browser_transport":
            title = "浏览器传输"
        elif event_type == "mutation_intent":
            title = "写操作意图"
        elif event_type == "step_failure":
            title = "步骤失败"
        elif event_type == "strategy_nudge":
            title = "策略提示"
        elif event_type == "completion_blocked":
            title = "完成被拦截"
        else:
            title = "错误"
        payload = (
            f"## {title}\n\n```json\n"
            + json.dumps(record, ensure_ascii=False, default=str, indent=2)
            + "\n```\n\n"
        ).encode("utf-8")
        if self._append_bounded(self.trace_file, payload):
            return
        if not self.trace_file.exists() or self.trace_file.stat().st_size == 0:
            self._append_bounded(
                self.trace_file,
                "## 日志已达到容量上限\n\n".encode("utf-8"),
            )

    def _append_bounded(self, path: Path, payload: bytes) -> bool:
        current_size = path.stat().st_size if path.exists() else 0
        if current_size + len(payload) > self.max_bytes:
            return False
        with path.open("ab") as file:
            file.write(payload)
        return True

    def _prepare_trace_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = event.get("type")
        if event_type == "llm_call":
            return {
                "type": "llm_call",
                "run_id": event.get("run_id"),
                "step_id": event.get("step_id"),
                "observation_id": event.get("observation_id"),
                "browser_session_id": event.get("browser_session_id"),
                "endpoint_id": event.get("endpoint_id"),
                "model": event.get("model"),
                "timeout_seconds": event.get("timeout_seconds"),
                "observation": self._snapshot_summary(
                    event.get("observation"),
                    include_preview=False,
                ),
                "input_metrics": event.get("input_metrics") or {},
                "message_count": len(event.get("messages") or []),
                "task_context": [
                    self._task_context_summary(item)
                    for item in (event.get("task_context") or [])
                ],
            }

        if event_type == "tool_result":
            return self._prepare_tool_result_trace(event)

        if event_type == "tool_call":
            safe_event = dict(event)
            safe_event["arguments"] = redact_tool_arguments(
                str(event.get("name", "")),
                event.get("arguments") or {},
            )
            return self._compact_trace_value(safe_event)

        if event_type == "mutation_intent":
            safe_event = dict(event)
            safe_event["arguments"] = redact_tool_arguments(
                str(event.get("tool_name") or event.get("name") or ""),
                event.get("arguments") or {},
            )
            return self._compact_trace_value(safe_event)

        safe_event = redact_value(event)
        return self._compact_trace_value(safe_event)

    def _task_context_summary(
        self,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        keys = (
            "type",
            "run_id",
            "step_id",
            "action_id",
            "name",
            "status",
            "evaluation_previous_goal",
            "memory",
            "next_goal",
            "effect",
            "error",
            "data_meta",
        )
        summary = {
            key: redact_value(item.get(key))
            for key in keys
            if item.get(key) is not None
        }
        if item.get("data") is not None:
            data = redact_value(item["data"])
            text = json.dumps(
                data,
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            )
            summary["data"] = {
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "characters": len(text),
            }
        return self._compact_trace_value(summary)

    def _prepare_tool_result_trace(
        self,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        """日志中的所有工具结果使用同一 envelope，页面快照只保留摘要。"""
        if "status" in event:
            status = event.get("status")
            data = event.get("data")
            error = event.get("error")
            effect = event.get("effect")
        elif "result" in event:
            if self._tool_outcome is None:
                raise RuntimeError("tool_outcome is required for tool results")
            outcome = self._tool_outcome(
                name=str(event.get("name", "")),
                arguments=event.get("arguments") or {},
                result=event.get("result"),
            )
            status = outcome["status"]
            data = outcome["data"]
            error = outcome["error"]
            effect = outcome["effect"]
        else:
            status = "failed"
            data = None
            error = event.get("error") or "unknown tool error"
            effect = {
                "dispatched": False,
                "page_changed": None,
            }

        if error is None:
            structured_error = None
        elif isinstance(error, dict):
            structured_error = error
        else:
            structured_error = {
                "type": event.get("error_type") or "ToolError",
                "message": str(error),
            }

        normalized = {
            "type": "tool_result",
            "run_id": event.get("run_id"),
            "step_id": event.get("step_id"),
            "action_id": event.get("action_id"),
            "browser_session_id": event.get("browser_session_id"),
            "name": event.get("name"),
            "arguments": redact_tool_arguments(
                str(event.get("name", "")),
                event.get("arguments") or {},
            ),
            "status": status,
            "data": data,
            "error": structured_error,
            "effect": effect,
        }
        if event.get("data_meta") is not None:
            normalized["data_meta"] = event["data_meta"]
        normalized = redact_value(normalized)
        if event.get("name") == "agent_browser_snapshot":
            normalized["data"] = self._snapshot_summary(
                data,
                include_preview=True,
            )
        return self._compact_trace_value(normalized)

    def _snapshot_summary(
        self,
        value: Any,
        include_preview: bool,
    ) -> dict[str, Any]:
        safe_value = redact_value(value)
        snapshot = extract_snapshot(safe_value)
        if snapshot is None:
            snapshot = json.dumps(
                safe_value,
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            )
        digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        summary: dict[str, Any] = {
            "sha256": digest,
            "characters": len(snapshot),
        }
        if include_preview:
            if digest == self._last_snapshot_hash:
                summary["duplicate"] = True
            else:
                summary["preview"] = self._snapshot_preview(snapshot)
                self._last_snapshot_hash = digest
        return summary

    @staticmethod
    def _snapshot_preview(snapshot: str) -> str:
        """折叠连续重复行，让首份快照预览可读且体积稳定。"""
        lines = snapshot.splitlines()
        collapsed: list[str] = []
        index = 0
        while index < len(lines) and len("\n".join(collapsed)) < 2_000:
            line = lines[index]
            repeated = 1
            while (
                index + repeated < len(lines)
                and lines[index + repeated] == line
            ):
                repeated += 1
            collapsed.append(
                f"{line} [repeated {repeated} times]"
                if repeated > 1
                else line
            )
            index += repeated
        preview = "\n".join(collapsed)
        return preview[:2_000]

    @classmethod
    def _compact_trace_value(cls, value: Any) -> Any:
        return compact_value(
            value,
            string_limit=4_000,
            list_limit=20,
            label="trace",
        )
