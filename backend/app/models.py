"""浏览器 Agent 的结构化输入输出模型。"""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AgentAction(BaseModel):
    """LLM 选择的一次浏览器工具调用。"""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    """LLM 单轮决策：继续执行、确认完成或报告阻塞。"""

    status: Literal["continue", "completed", "blocked"]
    evaluation_previous_goal: str = Field(min_length=1)
    memory: str = Field(min_length=1)
    next_goal: str | None = None
    completion_evidence: list[str] = Field(default_factory=list)
    actions: list[AgentAction] = Field(default_factory=list, max_length=3)
    final_answer: str | None = None

    @model_validator(mode="after")
    def validate_decision(self):
        """按决策状态约束动作、最终答案、下一目标和证据。"""
        has_actions = bool(self.actions)
        has_answer = bool(self.final_answer and self.final_answer.strip())
        has_next_goal = bool(self.next_goal and self.next_goal.strip())
        has_evidence = bool(
            self.completion_evidence
            and all(item.strip() for item in self.completion_evidence)
        )
        if not self.evaluation_previous_goal.strip() or not self.memory.strip():
            raise ValueError("evaluation_previous_goal and memory cannot be blank")
        if self.status == "continue":
            if not has_actions or has_answer or not has_next_goal:
                raise ValueError(
                    "continue decision requires actions and next_goal, "
                    "without final_answer"
                )
            return self
        if has_actions or not has_answer or has_next_goal or not has_evidence:
            raise ValueError(
                "completed or blocked decision requires final_answer and "
                "completion_evidence, without actions or next_goal"
            )
        return self


class AgentTokenUsage(BaseModel):
    """一次 Agent 任务内由模型服务报告的 Token 用量总和。"""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    input_characters: int = 0
    observation_characters: int = 0

    def merged(self, other: "AgentTokenUsage") -> "AgentTokenUsage":
        return AgentTokenUsage(
            llm_calls=self.llm_calls + other.llm_calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cached_input_tokens=(
                self.cached_input_tokens + other.cached_input_tokens
            ),
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            input_characters=(
                self.input_characters + other.input_characters
            ),
            observation_characters=(
                self.observation_characters
                + other.observation_characters
            ),
        )


class AgentResult(BaseModel):
    """一次 Agent 运行对上层接口返回的最终结果。"""

    success: bool
    answer: str
    token_usage: AgentTokenUsage | None = None
