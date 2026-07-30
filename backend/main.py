"""Browser Agent 后端入口和应用级依赖管理。"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, model_validator

from app.agent import Agent
from app.browser_process import get_server_parameters
from app.llm import AgentLLM
from app.mcp_client import (
    BrowserService,
    ManagedBrowserSession,
)
from app.models import AgentResult


CONVERSATION_TRACE_DIR = Path(__file__).parent / "logs" / "conversations"
BROWSER_SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


class AgentRunRequest(BaseModel):
    """在同一 conversation_id 下发送一条新的用户消息。"""

    message: str = Field(min_length=1)
    conversation_id: str = Field(
        default="default",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )
    browser_session_id: str = Field(
        default="browser-agent-main",
        min_length=1,
        pattern=BROWSER_SESSION_ID_PATTERN,
    )


class BrowserSessionStartRequest(BaseModel):
    """请求显式启动或接管一个浏览器会话。"""

    browser_session_id: str = Field(
        pattern=BROWSER_SESSION_ID_PATTERN,
    )
    mode: Literal["isolated", "existing"] = "isolated"
    cdp_url: str | None = None

    @model_validator(mode="after")
    def validate_mode_settings(self):
        """要求现有浏览器提供明确地址，避免模糊的自动连接。"""
        if self.mode == "existing" and not self.cdp_url:
            raise ValueError(
                "cdp_url is required when mode is 'existing'"
            )
        if self.mode == "isolated" and self.cdp_url is not None:
            raise ValueError(
                "cdp_url is only valid when mode is 'existing'"
            )
        return self


class BrowserSessionResult(BaseModel):
    """浏览器会话完成启动和探测后的状态。"""

    browser_session_id: str
    mode: Literal["isolated", "existing"]
    ready: bool
    url: str | None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """在应用生命周期内复用 OpenAI 和 MCP 客户端。"""
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL")
    if not api_key or not model:
        raise RuntimeError(
            "OPENAI_API_KEY and OPENAI_MODEL must be set before startup"
        )

    client_options = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        client_options["base_url"] = base_url
    openai_client = AsyncOpenAI(**client_options)
    app.state.agent_llm = AgentLLM(openai_client, model=model)
    app.state.agents = {}

    params = get_server_parameters()
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                browser = BrowserService(session)
                await browser.cache_tools()
                app.state.browser_service = browser
                yield
    finally:
        await openai_client.close()


app = FastAPI(title="Browser Agent Backend", lifespan=lifespan)


def get_browser_service(request: Request) -> BrowserService:
    """从应用状态中取得已初始化的浏览器服务。"""
    service = getattr(request.app.state, "browser_service", None)
    if service is None:
        raise HTTPException(
            status_code=500,
            detail="MCP session not initialized",
        )
    return service

# FastAPI 路由使用的 BrowserService 依赖类型。
BrowserDep = Annotated[
    BrowserService,
    Depends(get_browser_service),
]


@app.get("/health")
async def health():
    """提供不触发浏览器操作的基础健康检查。"""
    return {"status": "ok"}


@app.post(
    "/browser/session/start",
    response_model=BrowserSessionResult,
)
async def start_browser_session(
    payload: BrowserSessionStartRequest,
    browser: BrowserDep,
) -> BrowserSessionResult:
    """显式启动浏览器会话，避免首次 Agent 工具调用承担冷启动。"""
    try:
        managed = await browser.start_session(
            payload.browser_session_id,
            mode=payload.mode,
            cdp_url=payload.cdp_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        raise HTTPException(
            status_code=503,
            detail=(
                f"Browser session '{payload.browser_session_id}' "
                f"failed to start: {error}"
            ),
        ) from exc

    return browser_session_result(managed)


def browser_session_result(
    managed: ManagedBrowserSession,
) -> BrowserSessionResult:
    """只向前端公开会话状态，不泄露外部浏览器的 CDP 地址。"""
    return BrowserSessionResult(
        browser_session_id=managed.browser_session_id,
        mode=managed.mode,
        ready=managed.ready,
        url=managed.url,
    )


@app.get(
    "/browser/sessions",
    response_model=list[BrowserSessionResult],
)
async def list_browser_sessions(
    browser: BrowserDep,
) -> list[BrowserSessionResult]:
    """列出当前后端进程管理的全部浏览器会话。"""
    return [
        browser_session_result(managed)
        for managed in browser.list_sessions()
    ]


@app.get(
    "/browser/sessions/{browser_session_id}",
    response_model=BrowserSessionResult,
)
async def get_browser_session(
    browser_session_id: str,
    browser: BrowserDep,
) -> BrowserSessionResult:
    """查询一个浏览器会话的当前状态。"""
    managed = browser.get_session(browser_session_id)
    if managed is None:
        raise HTTPException(status_code=404, detail="Browser session not found")
    return browser_session_result(managed)


@app.delete(
    "/browser/sessions/{browser_session_id}",
    response_model=BrowserSessionResult,
)
async def close_browser_session(
    browser_session_id: str,
    browser: BrowserDep,
) -> BrowserSessionResult:
    """关闭指定隔离会话，或断开与指定现有浏览器的连接。"""
    try:
        managed = await browser.close_session(browser_session_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Browser session not found",
        ) from exc
    return browser_session_result(managed)


@app.post("/agent/run", response_model=AgentResult)
async def run_agent(
    payload: AgentRunRequest,
    request: Request,
    browser: BrowserDep,
) -> AgentResult:
    """执行新任务，或在已有会话中继续对话。"""
    if (
        not browser.is_session_ready(payload.browser_session_id)
        or not await browser.refresh_session_ready(
            payload.browser_session_id
        )
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Browser session '{payload.browser_session_id}' "
                "is not ready or is no longer active; "
                "start it via POST /browser/session/start first"
            ),
        )

    agents: dict[str, Agent] = request.app.state.agents
    agent = agents.get(payload.conversation_id)
    if agent is None:
        agent = Agent(
            task=payload.message,
            browser=browser,
            llm=request.app.state.agent_llm,
            trace_file=(
                CONVERSATION_TRACE_DIR
                / f"{payload.conversation_id}.md"
            ),
        )
        agents[payload.conversation_id] = agent
    else:
        agent.add_user_message(payload.message)

    return await agent.run(payload.browser_session_id)
