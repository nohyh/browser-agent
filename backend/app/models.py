"""浏览器 Agent 的结构化输入输出模型。"""

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


MutationStatus = Literal[
    "prepared",
    "dispatched",
    "uncertain",
    "confirmed",
    "failed",
]


class ToolBehavior(BaseModel):
    """工具执行前需要知道的最小安全元数据。"""

    name: str
    category: Literal["read_only", "navigation", "potential_write"] = (
        "potential_write"
    )
    changes_page: bool = True
    terminates_sequence: bool = True
    retry_policy: Literal["none", "read_once", "observe"] = "none"
    result_visibility: Literal["context", "hidden"] = "context"

    @property
    def read_only(self) -> bool:
        return self.category == "read_only"

    @property
    def potential_write(self) -> bool:
        return self.category == "potential_write"


class ActionEffect(BaseModel):
    """一次工具动作在页面观察中的可验证效果。"""

    dispatched: bool = False
    page_changed: bool | None = None
    url_changed: bool | None = None
    snapshot_changed: bool | None = None
    observation_id: str | None = None
    observation_revision: int | None = None
    confirmed: bool = False


class ToolOutcome(BaseModel):
    """MCP 工具调用对 Agent 暴露的统一结果 envelope。"""

    type: Literal["tool_result"] = "tool_result"
    action_id: str | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal["succeeded", "failed", "uncertain"]
    data: Any = None
    error: dict[str, Any] | str | None = None
    effect: ActionEffect = Field(default_factory=ActionEffect)


class StepFailure(BaseModel):
    """可供下一轮决策使用的结构化步骤失败。"""

    stage: Literal[
        "provider",
        "browser",
        "tool",
        "validation",
        "completion",
        "runtime",
        "loop",
        "cancelled",
    ]
    code: str
    retryable: bool = False
    uncertain: bool = False
    message: str
    attempt: int = 1
    observation_id: str | None = None
    observation_revision: int | None = None


class MutationIntent(BaseModel):
    """潜在写操作的生命周期记录，防止恢复或重试时重复执行。"""

    mutation_id: str = Field(default_factory=lambda: f"mutation-{uuid4()}")
    action_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: MutationStatus = "prepared"
    page_url: str | None = None
    prepared_at: float | None = None
    dispatched_at: float | None = None
    effect: ActionEffect | None = None
    error: dict[str, Any] | str | None = None


class BrowserObservation(BaseModel):
    """带版本的页面观察；原始 MCP 结构可以继续放在 data 中。"""

    observation_id: str
    revision: int
    url: str | None = None
    title: str | None = None
    tabs: list[dict[str, Any]] = Field(default_factory=list)
    snapshot: str | None = None
    snapshot_hash: str | None = None
    source_characters: int = 0
    sent_characters: int = 0
    stability: Literal["unknown", "stable", "unstable", "empty"] = "unknown"
    data: dict[str, Any] = Field(default_factory=dict)


class AgentAction(BaseModel):
    """LLM 选择的一次浏览器工具调用。"""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    observation_id: str | None = None
    observation_revision: int | None = None


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
    failed_llm_calls: int = 0
    usage_unavailable_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    input_characters: int = 0
    observation_characters: int = 0
    observation_source_characters: int = 0
    observation_sent_snapshot_characters: int = 0
    observation_truncated_characters: int = 0
    task_context_characters: int = 0

    def merged(self, other: "AgentTokenUsage") -> "AgentTokenUsage":
        return AgentTokenUsage(
            llm_calls=self.llm_calls + other.llm_calls,
            failed_llm_calls=(
                self.failed_llm_calls + other.failed_llm_calls
            ),
            usage_unavailable_calls=(
                self.usage_unavailable_calls
                + other.usage_unavailable_calls
            ),
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
            observation_source_characters=(
                self.observation_source_characters
                + other.observation_source_characters
            ),
            observation_sent_snapshot_characters=(
                self.observation_sent_snapshot_characters
                + other.observation_sent_snapshot_characters
            ),
            observation_truncated_characters=(
                self.observation_truncated_characters
                + other.observation_truncated_characters
            ),
            task_context_characters=(
                self.task_context_characters
                + other.task_context_characters
            ),
        )


class AgentResult(BaseModel):
    """一次 Agent 运行对上层接口返回的最终结果。"""

    success: bool
    answer: str
    token_usage: AgentTokenUsage | None = None
