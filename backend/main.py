"""Browser Agent 后端入口和应用级依赖装配。"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Callable

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.api import agent as agent_api
from app.api import browser as browser_api
from app.api import llm as llm_api
from app.api.schemas import (
    AgentRunRequest,
    BrowserSessionResult,
    BrowserSessionStartRequest,
    LLMConfigRequest,
    LLMConfigResult,
    LLMEndpointsConfigRequest,
    LLMEndpointsConfigResult,
    LLMModelDiscoveryRequest,
    LLMModelDiscoveryResult,
    PageSuggestionsRequest,
    PageSuggestionsResult,
)
from app.browser_process import get_server_parameters
from app.llm import AgentLLM
from app.llm_registry import LLMRegistry
from app.mcp_client import BrowserService
from app.models import AgentResult
from app.runtime_supervisor import BrowserRuntimeSupervisor
from app.trace import (
    DEFAULT_TRACE_MAX_TOTAL_BYTES,
    DEFAULT_TRACE_RETENTION_DAYS,
    TraceRecorder,
    cleanup_trace_directory,
)


CONVERSATION_TRACE_DIR = Path(__file__).parent / "logs" / "conversations"
BROWSER_SESSION_TRACE_FILE = Path(__file__).parent / "logs" / "browser-sessions.md"
TRACE_RETENTION_DAYS = int(
    os.getenv("BROWSER_AGENT_TRACE_RETENTION_DAYS", DEFAULT_TRACE_RETENTION_DAYS)
)
TRACE_MAX_TOTAL_BYTES = int(
    os.getenv(
        "BROWSER_AGENT_TRACE_MAX_TOTAL_BYTES",
        DEFAULT_TRACE_MAX_TOTAL_BYTES,
    )
)

# 保留这些入口，避免已有脚本因内部模块拆分而失效。
llm_config_fingerprint = llm_api.llm_config_fingerprint
parse_page_suggestions = llm_api.parse_page_suggestions
browser_session_result = browser_api.browser_session_result
public_trace_event = agent_api.public_trace_event
stream_line = agent_api.stream_line


@asynccontextmanager
async def browser_runtime_context():
    """创建一个可由 supervisor 重建的 MCP/browser runtime。"""
    params = get_server_parameters()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            session_tracer = TraceRecorder(
                BROWSER_SESSION_TRACE_FILE,
                retain_events=False,
            )
            browser = BrowserService(
                session,
                lifecycle_sink=session_tracer.record,
            )
            await browser.cleanup_orphaned_sessions()
            await browser.cache_tools()
            yield browser


@asynccontextmanager
async def lifespan(app: FastAPI):
    """API 先存活，再由后台任务初始化 MCP 客户端。"""
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL")
    app.state.agents = {}
    app.state.openai_client = None
    app.state.agent_llm = None
    app.state.llm_config_fingerprint = None
    app.state.llm_registry = LLMRegistry()
    app.state.active_runs = {}
    app.state.agent_locks = {}
    app.state.browser_service = None

    # 清理放在进程启动阶段，避免在用户请求路径同步扫描全部历史日志。
    for trace_directory in {
        CONVERSATION_TRACE_DIR,
        BROWSER_SESSION_TRACE_FILE.parent,
    }:
        try:
            await asyncio.to_thread(
                cleanup_trace_directory,
                trace_directory,
                retention_days=TRACE_RETENTION_DAYS,
                max_total_bytes=TRACE_MAX_TOTAL_BYTES,
            )
        except Exception:
            pass

    # 环境变量仍可作为默认配置；缺失时等待前端通过接口配置。
    if api_key and model:
        initial_config = LLMConfigRequest(
            api_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            api_key=SecretStr(api_key),
            model=model,
        )
        openai_client = AsyncOpenAI(
            api_key=initial_config.api_key.get_secret_value(),
            base_url=initial_config.api_url,
        )
        app.state.openai_client = openai_client
        app.state.agent_llm = AgentLLM(
            openai_client,
            model=initial_config.model,
            endpoint_id="environment",
        )
        app.state.llm_config_fingerprint = llm_config_fingerprint(initial_config)

    supervisor = BrowserRuntimeSupervisor(browser_runtime_context)
    app.state.runtime_supervisor = supervisor

    async def start_runtime() -> None:
        await supervisor.start()
        app.state.browser_service = supervisor.service

    runtime_start_task = asyncio.create_task(start_runtime())
    app.state.runtime_start_task = runtime_start_task
    try:
        yield
    finally:
        active_runs = list(app.state.active_runs.values())
        for task in active_runs:
            task.cancel()
        if active_runs:
            await asyncio.gather(*active_runs, return_exceptions=True)
        if not runtime_start_task.done():
            runtime_start_task.cancel()
        await asyncio.gather(runtime_start_task, return_exceptions=True)
        await supervisor.stop()
        app.state.browser_service = None
        llm_registry = getattr(app.state, "llm_registry", None)
        if llm_registry is not None:
            await llm_registry.close()
        openai_client = getattr(app.state, "openai_client", None)
        if openai_client is not None:
            await openai_client.close()


app = FastAPI(title="Browser Agent Backend", lifespan=lifespan)


def get_browser_service(request: Request) -> BrowserService:
    """从应用状态中取得已初始化的浏览器服务。"""
    supervisor = getattr(request.app.state, "runtime_supervisor", None)
    service = (
        supervisor.service
        if supervisor is not None
        else getattr(request.app.state, "browser_service", None)
    )
    if service is None:
        from fastapi import HTTPException

        snapshot = (
            supervisor.snapshot()
            if supervisor is not None
            else {"status": "degraded", "last_error": "MCP session not initialized"}
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "runtime_not_ready",
                "message": "Browser runtime is not ready.",
                **snapshot,
            },
        )
    return service


BrowserDep = Annotated[BrowserService, Depends(get_browser_service)]


@app.get("/health")
async def health(request: Request):
    supervisor = getattr(request.app.state, "runtime_supervisor", None)
    return {
        "status": "ok",
        "runtime": (
            supervisor.snapshot()
            if supervisor is not None
            else {"status": "unknown", "ready": False}
        ),
    }


@app.get("/health/live")
async def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(request: Request):
    supervisor = getattr(request.app.state, "runtime_supervisor", None)
    snapshot = (
        supervisor.snapshot()
        if supervisor is not None
        else {"status": "degraded", "ready": False, "last_error": "MCP session not initialized"}
    )
    if snapshot.get("ready") is True:
        return snapshot
    from fastapi import HTTPException

    raise HTTPException(
        status_code=503,
        detail={
            "code": "runtime_not_ready",
            "message": "Browser runtime is not ready.",
            **snapshot,
        },
    )


@app.put("/llm/config", response_model=LLMConfigResult)
async def configure_llm(
    payload: LLMConfigRequest,
    request: Request,
) -> LLMConfigResult:
    return await llm_api.configure_llm(
        payload,
        request,
        client_factory=AsyncOpenAI,
    )


@app.post("/llm/models", response_model=LLMModelDiscoveryResult)
async def discover_llm_models(
    payload: LLMModelDiscoveryRequest,
) -> LLMModelDiscoveryResult:
    return await llm_api.discover_llm_models(
        payload,
        client_factory=AsyncOpenAI,
    )


@app.put("/llm/configs", response_model=LLMEndpointsConfigResult)
async def configure_llm_endpoints(
    payload: LLMEndpointsConfigRequest,
    request: Request,
) -> LLMEndpointsConfigResult:
    return await llm_api.configure_llm_endpoints(
        payload,
        request,
        client_factory=AsyncOpenAI,
    )


@app.post("/page/suggestions", response_model=PageSuggestionsResult)
async def generate_page_suggestions(
    payload: PageSuggestionsRequest,
    request: Request,
) -> PageSuggestionsResult:
    return await llm_api.generate_page_suggestions(payload, request)


@app.post("/browser/session/start", response_model=BrowserSessionResult)
async def start_browser_session(
    payload: BrowserSessionStartRequest,
    browser: BrowserDep,
) -> BrowserSessionResult:
    return await browser_api.start_browser_session(payload, browser)


@app.get("/browser/sessions", response_model=list[BrowserSessionResult])
async def list_browser_sessions(
    browser: BrowserDep,
) -> list[BrowserSessionResult]:
    return await browser_api.list_browser_sessions(browser)


@app.get(
    "/browser/sessions/{browser_session_id}",
    response_model=BrowserSessionResult,
)
async def get_browser_session(
    browser_session_id: str,
    browser: BrowserDep,
) -> BrowserSessionResult:
    return await browser_api.get_browser_session(browser_session_id, browser)


@app.delete(
    "/browser/sessions/{browser_session_id}",
    response_model=BrowserSessionResult,
)
async def close_browser_session(
    browser_session_id: str,
    browser: BrowserDep,
) -> BrowserSessionResult:
    return await browser_api.close_browser_session(browser_session_id, browser)


async def _execute_agent(
    payload: AgentRunRequest,
    request: Request,
    browser: BrowserDep,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> AgentResult:
    return await agent_api.execute_agent(
        payload,
        request,
        browser,
        trace_dir=CONVERSATION_TRACE_DIR,
        event_sink=event_sink,
    )


@app.post("/agent/run", response_model=AgentResult)
async def run_agent(
    payload: AgentRunRequest,
    request: Request,
    browser: BrowserDep,
) -> AgentResult:
    return await _execute_agent(payload, request, browser)


@app.post("/agent/run/stream")
async def stream_agent_run(
    payload: AgentRunRequest,
    request: Request,
    browser: BrowserDep,
):
    return await agent_api.stream_agent_run(
        payload,
        request,
        browser,
        trace_dir=CONVERSATION_TRACE_DIR,
    )


@app.delete("/agent/runs/{run_id}")
async def cancel_agent_run(run_id: str, request: Request) -> dict[str, Any]:
    return await agent_api.cancel_agent_run(run_id, request)
