"""浏览器 Agent 的最小执行循环与结构化输出模型。"""

import json
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.mcp_client import BrowserService
from app.utils import format_mcp_tools


PAGE_CHANGING_ACTIONS = {
    "agent_browser_open",
    "agent_browser_click",
    "agent_browser_press",
    "agent_browser_check",
    "agent_browser_uncheck",
    "agent_browser_select",
    "agent_browser_tab_switch",
    "agent_browser_frame_switch",
    "agent_browser_frame_main",
}


class AgentAction(BaseModel):
    """LLM 选择的一次浏览器工具调用。"""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    """LLM 单轮决策：执行动作或直接给出最终答案。"""

    actions: list[AgentAction] = Field(default_factory=list)
    final_answer: str | None = None

    @model_validator(mode="after")
    def validate_decision(self):
        """保证模型不会同时返回动作和最终答案，也不会两者都不返回。"""
        has_actions = bool(self.actions)
        has_answer = bool(self.final_answer and self.final_answer.strip())
        if has_actions == has_answer:
            raise ValueError("decision must contain actions or final_answer, but not both")
        return self


class AgentResult(BaseModel):
    """一次 Agent 运行对上层接口返回的最终结果。"""

    success: bool
    answer: str


class Agent:
    """负责观察页面、请求 LLM 决策并执行浏览器动作。"""

    def __init__(
        self,
        task: str,
        session_id: str,
        browser: BrowserService,
        llm: Any,
        max_steps: int = 20,
    ):
        self.messages: list[dict[str, str]] = [
            {"role": "user", "content": task}
        ]
        self.session_id = session_id
        self.browser = browser
        self.llm = llm
        self.max_steps = max_steps
        # 这里只保存短动作摘要，不保存旧的 DOM 快照，避免上下文持续膨胀。
        self.history: list[str] = []

    async def observe(self) -> Any:
        """获取当前页面的交互元素快照，作为本轮唯一页面状态。"""
        return await self.browser.call_tool(
            session_id=self.session_id,
            name="agent_browser_snapshot",
            arguments={"interactive": True, "compact": True},
        )

    def build_messages(self, observation: Any) -> list[dict[str, str]]:
        """将对话和当前任务状态组合成本轮上下文，不保存旧页面快照。"""
        recent_history = "\n".join(self.history[-5:]) or "(none)"
        current_state = self._stringify(observation, limit=20_000)
        return [
            {
                "role": "system",
                "content": (
                    "You are a browser agent. Use the available tools to complete the task. "
                    "Return either one or more actions, or a final answer, but never both. "
                    "When finished, answer the user first, then add a concise Task supplement "
                    "only when later conversation may need the result, artifact, important "
                    "decision, or unresolved issue."
                ),
            },
            *self.messages,
            {
                "role": "user",
                "content": (
                    f"Recent action history:\n{recent_history}\n\n"
                    f"Current browser state:\n{current_state}"
                ),
            },
        ]

    async def run(self) -> AgentResult:
        """循环执行“观察、决策、动作”，直到完成或达到最大步数。"""
        # 工具 schema 保留在本地；这里只向 LLM 提供紧凑的参数签名和描述。
        tools = format_mcp_tools(self.browser.tools)
        allowed_names = {tool.name for tool in self.browser.tools}

        for _ in range(self.max_steps):
            try:
                # 每轮重新观察，旧页面快照不会进入下一轮历史。
                observation = await self.observe()
                raw_decision = await self.llm.decide(
                    self.build_messages(observation),
                    tools,
                )
                decision = (
                    raw_decision
                    if isinstance(raw_decision, AgentDecision)
                    else AgentDecision.model_validate(raw_decision)
                )
            except Exception as exc:
                return self._finish(
                    success=False,
                    answer=f"Agent decision failed: {exc}",
                )

            # 最终答案和工具动作由 AgentDecision 保证互斥。
            if decision.final_answer:
                return self._finish(success=True, answer=decision.final_answer)

            # 限制单轮动作数量，避免模型一次生成过长且难以验证的操作链。
            for action in decision.actions[:3]:
                if action.name not in allowed_names:
                    self.history.append(f"{action.name}: tool is not allowed")
                    break

                try:
                    result = await self.browser.call_tool(
                        session_id=self.session_id,
                        name=action.name,
                        arguments=action.arguments,
                    )
                    self.history.append(
                        f"{action.name}: {self._stringify(result, limit=1_000)}"
                    )
                except Exception as exc:
                    self.history.append(f"{action.name}: error: {exc}")
                    break

                # 页面可能变化后立即结束本轮，下一轮重新获取有效元素引用。
                if action.name in PAGE_CHANGING_ACTIONS:
                    break

        return self._finish(
            success=False,
            answer="Agent reached the maximum number of steps without finishing",
        )

    def _finish(self, success: bool, answer: str) -> AgentResult:
        """将任务结果写回对话，并清理只在本次任务内有效的动作历史。"""
        self.messages.append({"role": "assistant", "content": answer})
        self.history.clear()
        return AgentResult(success=success, answer=answer)

    @staticmethod
    def _stringify(value: Any, limit: int) -> str:
        """将工具结果转为有长度上限的文本，防止大结果撑爆上下文。"""
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except TypeError:
                text = str(value)
        if len(text) <= limit:
            return text
        return text[:limit] + "... [truncated]"
