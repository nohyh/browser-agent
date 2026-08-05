import asyncio
from types import SimpleNamespace


from app.llm_provider import ProviderDecision
from app.models import AgentDecision


def mcp_tool(name: str):
    return SimpleNamespace(
        name=name,
        description=f"{name} description",
        inputSchema={"type": "object", "properties": {}},
    )


def mcp_tool_v2(
    name: str,
    *,
    properties: dict | None = None,
    required: list | None = None,
    read_only_hint: bool | None = None,
):
    """按 MCP 2.0 snake_case 命名构造工具，模拟真实 agent-browser 返回。"""
    annotations = None
    if read_only_hint is not None:
        annotations = SimpleNamespace(
            title=None,
            read_only_hint=read_only_hint,
            destructive_hint=None,
            idempotent_hint=None,
            open_world_hint=True,
        )
    return SimpleNamespace(
        name=name,
        description=f"{name} description",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": properties or {},
            "required": required or [],
        },
        annotations=annotations,
    )


def mcp_result(data=None, *, success=True, error=None):
    return SimpleNamespace(
        structuredContent={
            "response": {
                "success": success,
                "data": data,
                "error": error,
            }
        },
        isError=False,
        content=[],
    )


def ready_session_info():
    return mcp_result(
        {
            "active": True,
            "runtime": {
                "browserLaunched": True,
                "pageCount": 1,
            },
        }
    )



def continue_decision(
    actions,
    *,
    evaluation="需要继续执行当前任务。",
    memory="当前任务尚未完成。",
    next_goal="执行下一项浏览器动作。",
):
    """构造测试使用的继续执行决策。"""
    return AgentDecision(
        status="continue",
        evaluation_previous_goal=evaluation,
        memory=memory,
        next_goal=next_goal,
        actions=actions,
    )


def completed_decision(
    answer,
    *,
    evaluation="用户要求已经完成。",
    memory="当前任务已经完成。",
    evidence=None,
):
    """构造测试使用的完成决策。"""
    return AgentDecision(
        status="completed",
        evaluation_previous_goal=evaluation,
        memory=memory,
        completion_evidence=evidence or ["测试状态确认任务完成。"],
        final_answer=answer,
    )


class TimedProviderAdapter:
    output_instructions = ""

    def __init__(self, succeed_on: int | None):
        self.succeed_on = succeed_on
        self.calls = 0

    async def decide(self, **kwargs):
        self.calls += 1
        if self.calls != self.succeed_on:
            await asyncio.Event().wait()
        return ProviderDecision(
            decision=completed_decision("done"),
            raw_response=SimpleNamespace(usage=None),
        )


class FakeBrowser:
    def __init__(self, snapshot_values=None):
        self.tools = [
            mcp_tool("agent_browser_snapshot"),
            mcp_tool("agent_browser_open"),
            mcp_tool("agent_browser_fill"),
            mcp_tool("agent_browser_click"),
            mcp_tool("agent_browser_scroll"),
            mcp_tool("agent_browser_wait_for_text"),
            mcp_tool("agent_browser_get_title"),
            mcp_tool("agent_browser_custom_tool"),
        ]
        self.calls = []
        self.snapshot_count = 0
        self.snapshot_values = list(snapshot_values or [])
        self.ready_sessions = set()
        self.sessions = {}

    async def start_session(
        self,
        browser_session_id,
        mode="isolated",
        cdp_url=None,
        expected_url=None,
    ):
        self.ready_sessions.add(browser_session_id)
        session = SimpleNamespace(
            browser_session_id=browser_session_id,
            mode=mode,
            ownership="external" if mode in {"current", "existing"} else "backend",
            status="ready",
            ready=True,
            url=expected_url or ("https://x.com/" if cdp_url else "about:blank"),
            last_error=None,
        )
        self.sessions[browser_session_id] = session
        return session

    def list_sessions(self):
        return list(self.sessions.values())

    def get_session(self, browser_session_id):
        return self.sessions.get(browser_session_id)

    async def close_session(self, browser_session_id):
        session = self.sessions.pop(browser_session_id, None)
        if session is None:
            raise KeyError(browser_session_id)
        self.ready_sessions.discard(browser_session_id)
        session.status = "closed"
        session.ready = False
        return session

    def is_session_ready(self, browser_session_id):
        return browser_session_id in self.ready_sessions

    async def refresh_session_ready(self, browser_session_id):
        return self.is_session_ready(browser_session_id)

    async def call_tool(self, browser_session_id, name, arguments):
        self.calls.append((browser_session_id, name, arguments))
        if name == "agent_browser_snapshot":
            self.snapshot_count += 1
            if self.snapshot_values:
                index = min(
                    self.snapshot_count - 1,
                    len(self.snapshot_values) - 1,
                )
                snapshot_value = self.snapshot_values[index]
                if isinstance(snapshot_value, dict):
                    return snapshot_value
                return {"snapshot": snapshot_value}
            return {"snapshot": f"CURRENT-SNAPSHOT-{self.snapshot_count}"}
        return {"success": True, "action": name, "arguments": arguments}


class FakeResponses:
    def __init__(self, decisions, usages=None, summaries=None):
        self.decisions = list(decisions)
        self.usages = list(usages or [])
        self.summaries = list(summaries or [])
        self.calls = []
        self.create_calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        usage = self.usages.pop(0) if self.usages else None
        decision = self.decisions.pop(0)
        if isinstance(decision, Exception):
            raise decision
        return SimpleNamespace(
            output_parsed=decision,
            usage=usage,
        )

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return SimpleNamespace(
            output_text=self.summaries.pop(0),
            usage=None,
        )


class FakeOpenAIClient:
    def __init__(self, decisions, usages=None, summaries=None):
        self.responses = FakeResponses(
            decisions,
            usages=usages,
            summaries=summaries,
        )
