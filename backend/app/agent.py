"""浏览器 Agent 的最小执行循环与结构化输出模型。"""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.mcp_client import BrowserService


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
        initial_message = {"role": "user", "content": task}
        # messages 保存持续对话；task_context 只服务当前任务；trace 仅用于完整复盘。
        self.messages: list[dict[str, str]] = [initial_message]
        self.task_context: list[dict[str, Any]] = []
        self.trace: list[dict[str, Any]] = [
            {"type": "message", **initial_message}
        ]
        self.session_id = session_id
        self.browser = browser
        self.llm = llm
        self.max_steps = max_steps

    async def observe(self) -> Any:
        """获取当前页面的交互元素快照，作为本轮唯一页面状态。"""
        return await self._call_tool(
            name="agent_browser_snapshot",
            arguments={"interactive": True, "compact": True},
        )

    def add_user_message(self, content: str) -> None:
        """追加用户消息，并同步写入不参与模型上下文的完整记录。"""
        message = {"role": "user", "content": content}
        self.messages.append(message)
        self.trace.append({"type": "message", **message})

    async def run(self) -> AgentResult:
        """循环执行“观察、决策、动作”，直到完成或达到最大步数。"""
        allowed_names = {tool.name for tool in self.browser.tools}

        for _ in range(self.max_steps):
            try:
                # 每轮重新观察，旧页面快照只进入 trace，不进入下一轮任务上下文。
                observation = await self.observe()
                self.trace.append(
                    {
                        "type": "llm_call",
                        "observation": observation,
                        "messages": list(self.messages),
                        "task_context": list(self.task_context),
                        "tools": [tool.name for tool in self.browser.tools],
                    }
                )
                decision = await self.llm.decide(
                    observation=observation,
                    messages=self.messages,
                    task_context=self.task_context,
                    tools=self.browser.tools,
                )
                self.trace.append(
                    {"type": "llm_result", "output": decision.model_dump()}
                )
                self.task_context.append(
                    {"type": "llm_result", "output": decision.model_dump()}
                )
            except Exception as exc:
                self.trace.append(
                    {"type": "error", "stage": "loop", "error": str(exc)}
                )
                return self._finish(
                    success=False,
                    answer=f"Agent decision failed: {exc}",
                )

            # 如果llm返回了最终结果，直接结束
            if decision.final_answer:
                return self._finish(success=True, answer=decision.final_answer)

            # 限制单轮动作数量，避免模型一次生成过长且难以验证的操作链。
            for action in decision.actions[:3]:
                if action.name not in allowed_names:
                    rejected_result = {
                        "type": "tool_result",
                        "name": action.name,
                        "arguments": action.arguments,
                        "error": "tool is not allowed",
                    }
                    self.task_context.append(rejected_result)
                    self.trace.append(rejected_result.copy())
                    break

                try:
                    result = await self._call_tool(
                        name=action.name,
                        arguments=action.arguments,
                    )
                    self.task_context.append(
                        {
                            "type": "tool_result",
                            "name": action.name,
                            "arguments": action.arguments,
                            "result": result,
                        }
                    )
                except Exception as exc:
                    self.task_context.append(
                        {
                            "type": "tool_result",
                            "name": action.name,
                            "arguments": action.arguments,
                            "error": str(exc),
                        }
                    )
                    break

                # 页面可能变化后立即结束本轮，下一轮重新获取有效元素引用。
                if action.name in PAGE_CHANGING_ACTIONS:
                    break

        return self._finish(
            success=False,
            answer="Agent reached the maximum number of steps without finishing",
        )

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """调用浏览器工具，并将完整调用过程写入 trace。"""
        self.trace.append(
            {"type": "tool_call", "name": name, "arguments": arguments}
        )
        try:
            result = await self.browser.call_tool(
                session_id=self.session_id,
                name=name,
                arguments=arguments,
            )
        except Exception as exc:
            self.trace.append(
                {
                    "type": "tool_result",
                    "name": name,
                    "arguments": arguments,
                    "error": str(exc),
                }
            )
            raise
        self.trace.append(
            {"type": "tool_result", "name": name, "result": result}
        )
        return result

    def _finish(self, success: bool, answer: str) -> AgentResult:
        """将任务结果写回对话和 trace，并清理当前任务上下文。"""
        message = {"role": "assistant", "content": answer}
        self.messages.append(message)
        self.trace.append({"type": "message", **message})
        self.task_context.clear()
        return AgentResult(success=success, answer=answer)
