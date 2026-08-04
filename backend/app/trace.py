"""浏览器 Agent 的诊断事件记录与脱敏。"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.utils.values import compact_value, extract_snapshot


SENSITIVE_KEYS = {
    "authorization",
    "challenge",
    "cookie",
    "js_challenge",
    "jsc_orig_r",
    "solution",
    "token",
}
SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?i)([?&](?:authorization|challenge|cookie|js_challenge|"
    r"jsc_orig_r|solution|token)=)[^&#\s\"']+"
)
BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return SENSITIVE_QUERY_PATTERN.sub(r"\1[REDACTED]", value)
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if str(key).lower() in SENSITIVE_KEYS
                else redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


class TraceRecorder:
    def __init__(
        self,
        trace_file: Path | None,
        tool_outcome: Callable[..., dict[str, Any]] | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        retain_events: bool = True,
    ):
        self.events: list[dict[str, Any]] = []
        self.trace_file = trace_file
        self._tool_outcome = tool_outcome
        self.event_sink = event_sink
        self.retain_events = retain_events
        self._last_snapshot_hash: str | None = None
        if self.trace_file is not None:
            self.trace_file.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict[str, Any]) -> None:
        """写入去重、脱敏后的诊断事件，避免日志反向复制完整上下文。"""
        event = self._prepare_trace_event(event)
        record = {
            "timestamp": datetime.now(BEIJING_TIMEZONE).isoformat(),
            **event,
        }
        if self.retain_events:
            self.events.append(record)
        if self.event_sink is not None:
            # 实时轨迹只发布已经过脱敏和压缩的记录，回调异常不能打断任务。
            try:
                self.event_sink(record.copy())
            except Exception:
                pass
        if self.trace_file is not None:
            event_type = record["type"]
            if event_type == "message":
                title = (
                    "用户消息"
                    if record["role"] == "user"
                    else "助手消息"
                )
            elif event_type == "tool_call":
                title = f"工具调用：{record['name']}"
            elif event_type == "tool_result":
                title = f"工具结果：{record['name']}"
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
            else:
                title = "错误"

            with self.trace_file.open("a", encoding="utf-8") as file:
                file.write(f"## {title}\n\n```json\n")
                file.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        default=str,
                        indent=2,
                    )
                )
                file.write("\n```\n\n")

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
            "arguments": event.get("arguments") or {},
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
