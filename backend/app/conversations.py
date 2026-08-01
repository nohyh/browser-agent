"""会话级 Agent 的生命周期、并发保护与取消管理。"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.agent import Agent


# 单个后端进程保留的会话上限，超出后淘汰最久未活动的空闲会话。
MAX_CONVERSATIONS = 50
# 会话空闲超过该时长即可被回收，避免长期驻留占用内存。
CONVERSATION_TTL_SECONDS = 24 * 60 * 60


@dataclass
class ConversationEntry:
    """一个前端会话对应的 Agent 及其运行状态。"""

    conversation_id: str
    agent: Agent
    title: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    running: bool = False
    task: asyncio.Task | None = None

    def touch(self) -> None:
        self.updated_at = time.time()

    @property
    def preview(self) -> str:
        """取最后一条助手回答作为列表预览。"""
        for message in reversed(self.agent.messages):
            if message.get("role") != "assistant":
                continue
            content = message.get("content", "")
            marker = '<最终回答'
            index = content.find(marker)
            if index == -1:
                continue
            start = content.find(">", index)
            end = content.find("</最终回答>", start)
            if start != -1 and end != -1:
                return content[start + 1 : end].strip()[:120]
        return ""
