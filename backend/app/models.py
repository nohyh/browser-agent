"""浏览器 Agent 的结构化输入输出模型。"""

from typing import Any

from pydantic import BaseModel, Field, model_validator


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
