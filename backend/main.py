"""Browser Agent 后端入口和应用级依赖管理。"""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncGenerator, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, model_validator

from app.agent import Agent
from app.browser_process import discover_cdp_url, get_server_parameters
from app.llm import AgentLLM
from app.mcp_client import BrowserService, ManagedBrowserSession
from app.models import AgentResult


CONVERSATION_TRACE_DIR = Path(__file__).parent / "logs" / "conversations"
BROWSER_SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"

# 会话空闲时长超过此值后在下次请求时被回收。
AGENT_TTL_SECONDS = 24 * 60 * 60
# 最多同时保留的 Agent 会话数，超出后淘汰最久未活动的。
MAX_AGENTS = 100


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


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

    browser_session_id: str = Field(pattern=BROWSER_SESSION_ID_PATTERN)
    mode: Literal["isolated", "existing"] = "isolated"
    cdp_url: str | None = None

    @model_validator(mode="after")
    def validate_mode_settings(self):
        if self.mode == "existing" and not self.cdp_url:
            raise ValueError("cdp_url is required when mode is 'existing'")
        if self.mode == "isolated" and self.cdp_url is not None:
            raise ValueError("cdp_url is only valid when mode is 'existing'")
        return self


class BrowserSessionResult(BaseModel):
    """浏览器会话完成启动和探测后的状态。"""

    browser_session_id: str
    mode: Literal["isolated", "existing"]
    ready: bool
    url: str | None


class AgentEntry:
    """Agent 实例及其运行状态，驻留在 app.state.agents 中。"""

    __slots__ = ("agent", "last_active", "running_task")

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.last_active: float = time.monotonic()
        self.running_task: asyncio.Task | None = None

    def touch(self) -> None:
        self.last_active = time.monotonic()


# ---------------------------------------------------------------------------
# 应用生命周期
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """在应用生命周期内复用 OpenAI 和 MCP 客户端，并启动 TTL 清理任务。"""
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL")
    if not api_key or not model:
        raise RuntimeError(
            "OPENAI_API_KEY and OPENAI_MODEL must be set before startup"
        )

    openai_client = AsyncOpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )
    app.state.agent_llm = AgentLLM(openai_client, model=model)
    app.state.agents: dict[str, AgentEntry] = {}
    app.state.agents_lock = asyncio.Lock()

    params = get_server_parameters()
    cleanup_task: asyncio.Task | None = None
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                browser = BrowserService(session)
                await browser.cache_tools()
                app.state.browser_service = browser
                cleanup_task = asyncio.create_task(
                    _ttl_cleanup_loop(app),
                    name="agent-ttl-cleanup",
                )
                yield
    finally:
        if cleanup_task is not None:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
        await openai_client.close()


async def _ttl_cleanup_loop(app: FastAPI) -> None:
    """每分钟扫描并移除空闲超时的 Agent 会话。"""
    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        async with app.state.agents_lock:
            expired = [
                cid
                for cid, entry in app.state.agents.items()
                if entry.running_task is None
                and now - entry.last_active > AGENT_TTL_SECONDS
            ]
            for cid in expired:
                del app.state.agents[cid]
        if expired:
            import logging
            logging.getLogger("browser_agent").info(
                "TTL cleanup removed %d idle agent(s): %s",
                len(expired),
                expired,
            )


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(title="Browser Agent Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # 允许本地扩展和开发服务器访问；生产环境收紧到实际扩展 ID。
    allow_origin_regex=r"(chrome-extension://.*|http://localhost(:\d+)?)",
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)


# ---------------------------------------------------------------------------
# 依赖
# ---------------------------------------------------------------------------


def get_browser_service(request: Request) -> BrowserService:
    service = getattr(request.app.state, "browser_service", None)
    if service is None:
        raise HTTPException(status_code=500, detail="MCP session not initialized")
    return service


BrowserDep = Annotated[BrowserService, Depends(get_browser_service)]


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 浏览器 CDP 自动探测
# ---------------------------------------------------------------------------


@app.get("/browser/discover")
async def discover_browser() -> dict[str, str | None]:
    """探测用户当前开启的 Chrome 调试端口，返回第一个可用的 CDP URL。"""
    cdp_url = await discover_cdp_url()
    return {"cdp_url": cdp_url}


# ---------------------------------------------------------------------------
# 浏览器会话管理
# ---------------------------------------------------------------------------


def browser_session_result(managed: ManagedBrowserSession) -> BrowserSessionResult:
    return BrowserSessionResult(
        browser_session_id=managed.browser_session_id,
        mode=managed.mode,
        ready=managed.ready,
        url=managed.url,
    )


@app.post("/browser/session/start", response_model=BrowserSessionResult)
async def start_browser_session(
    payload: BrowserSessionStartRequest,
    browser: BrowserDep,
) -> BrowserSessionResult:
    """显式启动浏览器会话，避免首次 Agent 工具调用承担冷启动延迟。"""
    try:
        managed = await browser.start_session(
            payload.browser_session_id,
            mode=payload.mode,
            cdp_url=payload.cdp_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        error_msg = str(exc) or type(exc).__name__
        raise HTTPException(
            status_code=503,
            detail=(
                f"Browser session '{payload.browser_session_id}' "
                f"failed to start: {error_msg}"
            ),
        ) from exc
    return browser_session_result(managed)


@app.get("/browser/sessions", response_model=list[BrowserSessionResult])
async def list_browser_sessions(browser: BrowserDep) -> list[BrowserSessionResult]:
    return [browser_session_result(m) for m in browser.list_sessions()]


@app.get(
    "/browser/sessions/{browser_session_id}",
    response_model=BrowserSessionResult,
)
async def get_browser_session(
    browser_session_id: str,
    browser: BrowserDep,
) -> BrowserSessionResult:
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
    try:
        managed = await browser.close_session(browser_session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Browser session not found") from exc
    return browser_session_result(managed)


# ---------------------------------------------------------------------------
# Agent — non-streaming (向后兼容)
# ---------------------------------------------------------------------------


@app.post("/agent/run", response_model=AgentResult)
async def run_agent(
    payload: AgentRunRequest,
    request: Request,
    browser: BrowserDep,
) -> AgentResult:
    """执行新任务，或在已有会话中继续对话（阻塞直到完成）。"""
    if not await browser.refresh_session_ready(payload.browser_session_id):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Browser session '{payload.browser_session_id}' is not ready; "
                "call POST /browser/session/start first"
            ),
        )

    entry = await _get_or_create_agent(request, payload, browser)
    result = await entry.agent.run(payload.browser_session_id)
    entry.touch()
    return result


# ---------------------------------------------------------------------------
# Agent — SSE streaming
# ---------------------------------------------------------------------------


@app.post("/agent/run/stream")
async def run_agent_stream(
    payload: AgentRunRequest,
    request: Request,
    browser: BrowserDep,
) -> StreamingResponse:
    """
    用 SSE 流式推送 Agent 每步事件，直到完成、阻塞或取消。

    事件格式（均为 JSON）：
      {"type": "step",     "step": N, "action": "observe"|"think"}
      {"type": "action",   "name": "...", "arguments": {...}}
      {"type": "progress", "memory": "...", "next_goal": "..."}
      {"type": "done",     "status": "completed"|"blocked"|"cancelled"|"failed",
                           "answer": "...", "success": bool}
      {"type": "error",    "detail": "..."}
    流结束时发送: data: [DONE]
    """
    if not await browser.refresh_session_ready(payload.browser_session_id):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Browser session '{payload.browser_session_id}' is not ready; "
                "call POST /browser/session/start first"
            ),
        )

    entry = await _get_or_create_agent(request, payload, browser)

    # 每个会话同时只允许一个任务在运行。
    if entry.running_task is not None and not entry.running_task.done():
        raise HTTPException(
            status_code=409,
            detail="Another task is already running for this conversation",
        )

    return StreamingResponse(
        _sse_generator(entry, payload.browser_session_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关闭 nginx 缓冲，确保实时推送
        },
    )


async def _sse_generator(
    entry: "AgentEntry",
    browser_session_id: str,
    request: Request,
) -> AsyncGenerator[str, None]:
    """把 Agent 事件队列转换为 SSE 文本流。"""
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)

    entry.agent._emit = emit  # noqa: SLF001

    async def _run() -> AgentResult:
        try:
            return await entry.agent.run(browser_session_id)
        finally:
            entry.agent._emit = None  # noqa: SLF001
            await queue.put(None)  # 哨兵：通知生成器流已结束

    task = asyncio.create_task(_run())
    entry.running_task = task

    try:
        while True:
            # 检测客户端断连，避免持续运行孤立任务。
            if await request.is_disconnected():
                entry.agent.cancel()
                break

            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                # 发送心跳注释，保持连接活跃。
                yield ": heartbeat\n\n"
                continue

            if event is None:
                # 任务结束哨兵
                break

            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            if event.get("type") in {"done", "error"}:
                break

        yield "data: [DONE]\n\n"
    except asyncio.CancelledError:
        entry.agent.cancel()
        raise
    finally:
        entry.touch()
        entry.running_task = None
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# ---------------------------------------------------------------------------
# Agent — 取消
# ---------------------------------------------------------------------------


@app.delete("/agent/{conversation_id}")
async def cancel_agent(
    conversation_id: str,
    request: Request,
) -> dict[str, str]:
    """取消正在运行的 Agent 任务，并从注册表删除该会话。"""
    async with request.app.state.agents_lock:
        entry: AgentEntry | None = request.app.state.agents.get(conversation_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        entry.agent.cancel()
        if entry.running_task is not None and not entry.running_task.done():
            entry.running_task.cancel()
        del request.app.state.agents[conversation_id]

    return {"status": "cancelled", "conversation_id": conversation_id}


# ---------------------------------------------------------------------------
# 列出所有会话（调试用）
# ---------------------------------------------------------------------------


@app.get("/agent/conversations")
async def list_conversations(request: Request) -> list[dict[str, Any]]:
    """返回当前所有活跃 Agent 会话的摘要（不含完整消息体）。"""
    async with request.app.state.agents_lock:
        now = time.monotonic()
        return [
            {
                "conversation_id": cid,
                "running": (
                    entry.running_task is not None
                    and not entry.running_task.done()
                ),
                "idle_seconds": round(now - entry.last_active, 1),
                "message_count": len(entry.agent.messages),
            }
            for cid, entry in request.app.state.agents.items()
        ]


# ---------------------------------------------------------------------------
# 辅助：获取或创建 Agent 入口
# ---------------------------------------------------------------------------


async def _get_or_create_agent(
    request: Request,
    payload: AgentRunRequest,
    browser: BrowserService,
) -> "AgentEntry":
    """线程安全地获取已有 Agent，或为新会话创建并注册一个新 Agent。"""
    async with request.app.state.agents_lock:
        agents: dict[str, AgentEntry] = request.app.state.agents
        entry = agents.get(payload.conversation_id)

        if entry is None:
            # 超出上限时淘汰最旧的空闲会话。
            if len(agents) >= MAX_AGENTS:
                idle = sorted(
                    (
                        (cid, e)
                        for cid, e in agents.items()
                        if e.running_task is None or e.running_task.done()
                    ),
                    key=lambda pair: pair[1].last_active,
                )
                if idle:
                    del agents[idle[0][0]]

            CONVERSATION_TRACE_DIR.mkdir(parents=True, exist_ok=True)
            agent = Agent(
                task=payload.message,
                browser=browser,
                llm=request.app.state.agent_llm,
                trace_file=CONVERSATION_TRACE_DIR / f"{payload.conversation_id}.md",
            )
            entry = AgentEntry(agent)
            agents[payload.conversation_id] = entry
        else:
            entry.agent.add_user_message(payload.message)
            entry.touch()

    return entry
