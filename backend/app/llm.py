"""Agent 使用的提示词构建和 LLM 调用适配。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.models import AgentDecision, AgentTokenUsage
from app.utils import format_mcp_tools

if TYPE_CHECKING:
    from openai import AsyncOpenAI


class AgentLLM:
    OBSERVATION_LIMIT = 20_000
    OBSERVATION_SNAPSHOT_LIMIT = 16_000
    TASK_CONTEXT_LIMIT = 6_000
    TASK_CONTEXT_ITEM_LIMIT = 2_500
    MESSAGE_LIMIT = 10

    def __init__(self, client: AsyncOpenAI, model: str):
        self.client = client
        self.model = model

    async def decide(
        self,
        observation: Any,
        messages: list[dict[str, str]],
        task_context: list[dict[str, Any]],
        tools: list[Any],
    ) -> tuple[AgentDecision, AgentTokenUsage | None]:
        """构造本轮决策提示词、调用底层客户端并校验结构化结果，返回决策与 Token 消耗。"""
        task_context_text = (
            self._format_task_context(task_context)
            if task_context
            else "(none)"
        )
        observation_text = self._format_observation(observation)
        tool_descriptions = format_mcp_tools(tools)
        conversation_messages = self._compact_messages(messages)
        state_text = (
            f"Current task context:\n{task_context_text}\n\n"
            f"Current browser state:\n{observation_text}"
        )
        if conversation_messages and conversation_messages[-1]["role"] == "user":
            conversation_messages[-1] = {
                **conversation_messages[-1],
                "content": (
                    f"{conversation_messages[-1]['content']}\n\n"
                    f"{state_text}"
                ),
            }
        else:
            conversation_messages.append(
                {"role": "user", "content": state_text}
            )
        input_messages = [
            {
                "role": "system",
                "content": (
                    "You are a browser agent. Follow the current user's task exactly, "
                    "and reply in the user's language. Use the smallest number of actions needed. "
                    "The snapshot is an ordered accessibility tree: indentation shows hierarchy "
                    "and @refs identify interactable elements. Prefer the primary action inside "
                    "the earliest matching list item. For ordinal requests such as first or second, "
                    "select exactly that ordered item and do not summarize sibling items. Never ask "
                    "the user to restate a task that is already actionable. "
                    "Snapshot refs are tool selectors: use @e107, never [ref='e107'], "
                    "[ref=e107], or bare e107. If the snapshot already contains the requested content, "
                    "return final_answer immediately instead of clicking merely to verify or open it. "
                    "For a summary request, include the content title and a body summary, not only "
                    "the author or link. When identifying the latest discussion from a sorted feed, "
                    "use the first non-pinned item under New sort and state that evidence; do not "
                    "invent a publication time that is not visible. "
                    "The agent refreshes snapshots automatically; "
                    "never request a snapshot tool yourself. Tool status 'succeeded' means the "
                    "tool returned successfully, while 'uncertain' means its intended page effect "
                    "was not verified. Use the matching agent_tools_get_* tool only when no "
                    "directly available tool can perform the task. Return either actions or "
                    "final_answer, never both. "
                    "As soon as the user's requested result is known, return final_answer "
                    "immediately without extra title/text/eval verification or meta supplements. "
                    "Do not call get_title, get_url, or get_text for information already visible "
                    "in the snapshot: get_title returns only the document title, not a post or "
                    "content item's heading.\n\n"
                    f"Available browser tools:\n{tool_descriptions}"
                ),
            },
            *conversation_messages,
        ]
        current_input = input_messages
        for attempt in range(2):
            try:
                response = await self.client.responses.parse(
                    model=self.model,
                    input=current_input,
                    text_format=AgentDecision,
                )
                if response.output_parsed is None:
                    raise ValueError(
                        "OpenAI response did not contain an AgentDecision"
                    )
                return (
                    response.output_parsed,
                    self._extract_token_usage(
                        response,
                        input_characters=sum(
                            len(str(message.get("content", "")))
                            for message in current_input
                        ),
                        observation_characters=len(observation_text),
                    ),
                )
            except (ValidationError, ValueError):
                if attempt == 1:
                    raise
                current_input = [dict(message) for message in input_messages]
                current_input[-1]["content"] = (
                    f"{current_input[-1]['content']}\n\n"
                    "Your previous response was invalid. Return exactly one of: "
                    "non-empty actions, or a non-empty final_answer."
                )
        raise RuntimeError("unreachable")

    @classmethod
    def _extract_token_usage(
        cls,
        response: Any,
        input_characters: int = 0,
        observation_characters: int = 0,
    ) -> AgentTokenUsage | None:
        """兼容 Responses API 对象和常见兼容端点的字典 usage。"""
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        input_tokens = cls._usage_value(
            usage,
            "input_tokens",
            "prompt_tokens",
        )
        output_tokens = cls._usage_value(
            usage,
            "output_tokens",
            "completion_tokens",
        )
        total_tokens = cls._usage_value(usage, "total_tokens")
        input_details = cls._usage_member(usage, "input_tokens_details")
        output_details = cls._usage_member(usage, "output_tokens_details")
        return AgentTokenUsage(
            llm_calls=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=(
                total_tokens
                if total_tokens
                else input_tokens + output_tokens
            ),
            cached_input_tokens=cls._usage_value(
                input_details,
                "cached_tokens",
            ),
            reasoning_tokens=cls._usage_value(
                output_details,
                "reasoning_tokens",
            ),
            input_characters=input_characters,
            observation_characters=observation_characters,
        )

    @staticmethod
    def _usage_member(value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    @classmethod
    def _usage_value(cls, value: Any, *names: str) -> int:
        for name in names:
            candidate = cls._usage_member(value, name)
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                return candidate
        return 0

    @classmethod
    def _format_observation(cls, observation: Any) -> str:
        """优先保留有顺序的 snapshot，移除与其重复且无顺序的 refs。"""
        if isinstance(observation, dict):
            payload = observation
            data = observation.get("data")
            if isinstance(data, dict):
                payload = data

            formatted: dict[str, Any] = {}
            snapshot = payload.get("snapshot")
            if isinstance(snapshot, str):
                formatted["snapshot"] = cls._truncate_text(
                    snapshot,
                    cls.OBSERVATION_SNAPSHOT_LIMIT,
                    "snapshot",
                )

            for key in ("url", "title", "origin", "lifecycle"):
                value = payload.get(key)
                if value is not None:
                    compact_value = cls._compact_value(value)
                    encoded = json.dumps(
                        compact_value,
                        ensure_ascii=False,
                        default=str,
                    )
                    formatted[key] = (
                        compact_value
                        if len(encoded) <= 800
                        else {
                            "summary": cls._truncate_text(
                                encoded,
                                800,
                                key,
                            )
                        }
                    )

            # 兼容不含 snapshot 的简单工具替身，同时明确排除重复 refs。
            if not formatted:
                for key, value in payload.items():
                    if key in {"refs", "success", "error"}:
                        continue
                    formatted[key] = cls._compact_value(value)
            if formatted:
                text = json.dumps(
                    formatted,
                    ensure_ascii=False,
                    default=str,
                )
                while (
                    len(text) > cls.OBSERVATION_LIMIT
                    and isinstance(formatted.get("snapshot"), str)
                    and len(formatted["snapshot"]) > 1_000
                ):
                    current = formatted["snapshot"]
                    excess = len(text) - cls.OBSERVATION_LIMIT
                    new_limit = max(1_000, len(current) - excess - 200)
                    formatted["snapshot"] = cls._truncate_text(
                        current,
                        new_limit,
                        "snapshot",
                    )
                    text = json.dumps(
                        formatted,
                        ensure_ascii=False,
                        default=str,
                    )
                return text

        return json.dumps(
            {"snapshot": cls._truncate_text(str(observation), 16_000, "state")},
            ensure_ascii=False,
        )

    @classmethod
    def _format_task_context(
        cls,
        task_context: list[dict[str, Any]],
    ) -> str:
        """从最近结果向前装入完整 JSON，避免字符串硬截断造成无效结构。"""
        selected: list[Any] = []
        for item in reversed(task_context):
            compact_item = cls._compact_context_item(item)
            candidate = [compact_item, *selected]
            text = json.dumps(
                candidate,
                ensure_ascii=False,
                default=str,
            )
            if len(text) > cls.TASK_CONTEXT_LIMIT:
                if selected:
                    break
                selected = [compact_item]
                break
            selected = candidate
        return json.dumps(selected, ensure_ascii=False, default=str)

    @classmethod
    def _compact_context_item(cls, item: dict[str, Any]) -> Any:
        compact_item = cls._compact_value(item)
        text = json.dumps(
            compact_item,
            ensure_ascii=False,
            default=str,
        )
        if len(text) <= cls.TASK_CONTEXT_ITEM_LIMIT:
            return compact_item

        data = compact_item.get("data") if isinstance(compact_item, dict) else None
        summary = {
            key: compact_item.get(key)
            for key in (
                "type",
                "name",
                "arguments",
                "status",
                "error",
                "effect",
            )
            if isinstance(compact_item, dict) and key in compact_item
        }
        if data is not None:
            summary["data_summary"] = cls._truncate_text(
                json.dumps(data, ensure_ascii=False, default=str),
                1_000,
                "tool data",
            )
        return summary

    @classmethod
    def _compact_messages(
        cls,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """保留首条任务和最近对话，阻止多任务历史无限累积。"""
        if len(messages) <= cls.MESSAGE_LIMIT:
            selected = messages
        else:
            selected = [
                messages[0],
                *messages[-(cls.MESSAGE_LIMIT - 1):],
            ]
        return [
            {
                **message,
                "content": cls._truncate_text(
                    message.get("content", ""),
                    4_000,
                    "message",
                ),
            }
            for message in selected
        ]

    @classmethod
    def _compact_value(cls, value: Any) -> Any:
        """限制上下文字段体积，并剔除页面树的重复副本。"""
        if isinstance(value, str):
            return cls._truncate_text(value, 2_000, "text")
        if isinstance(value, dict):
            return {
                key: cls._compact_value(item)
                for key, item in value.items()
                if key not in {"refs", "snapshot"}
            }
        if isinstance(value, list):
            return [cls._compact_value(item) for item in value[-20:]]
        return value

    @staticmethod
    def _truncate_text(text: str, limit: int, label: str) -> str:
        if len(text) <= limit:
            return text
        suffix = f"\n... [{label} truncated]"
        return text[: limit - len(suffix)] + suffix
