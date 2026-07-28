"""Agent 使用的提示词构建和 LLM 调用适配。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agent import AgentDecision
from app.utils import format_mcp_tools

if TYPE_CHECKING:
    from openai import AsyncOpenAI


class AgentLLM:
    def __init__(self, client: AsyncOpenAI, model: str):
        self.client = client
        self.model = model

    async def decide(
        self,
        observation: Any,
        messages: list[dict[str, str]],
        task_context: list[dict[str, Any]],
        tools: list[Any],
    ) -> AgentDecision:
        """构造本轮决策提示词、调用底层客户端并校验结构化结果。"""
        task_context_text = (
            self._stringify(task_context) if task_context else "(none)"
        )
        observation_text = self._stringify(observation, limit=20_000)
        tool_descriptions = format_mcp_tools(tools)
        input_messages = [
            {
                "role": "system",
                "content": (
                    "You are a browser agent. Use the available tools to complete the task. "
                    "Return either one or more actions, or a final answer, but never both. "
                    "When finished, answer the user first. Then, based on the Current task context, "
                    "add a concise Task supplement summarizing key results, artifacts, important "
                    "decisions, or unresolved issues that later conversation may need.\n\n"
                    f"Available browser tools:\n{tool_descriptions}"
                ),
            },
            *messages,
            {
                "role": "user",
                "content": (
                    f"Current task context:\n{task_context_text}\n\n"
                    f"Current browser state:\n{observation_text}"
                ),
            },
        ]
        response = await self.client.responses.parse(
            model=self.model,
            input=input_messages,
            text_format=AgentDecision,
        )
        if response.output_parsed is None:
            raise ValueError("OpenAI response did not contain an AgentDecision")
        return response.output_parsed

    @staticmethod
    def _stringify(value: Any, limit: int | None = None) -> str:
        """将提示词数据转为文本，并在指定上限时截断。"""
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except TypeError:
                text = str(value)
        if limit is None or len(text) <= limit:
            return text
        return text[:limit] + "... [truncated]"
