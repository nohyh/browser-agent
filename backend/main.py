"""Browser Agent 后端入口和应用级依赖管理。"""

from fastapi import Depends
from typing import Annotated
from fastapi import HTTPException
from fastapi import Request
from contextlib import asynccontextmanager
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from app.mcp_client import get_server_parameters, BrowserService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """在 FastAPI 生命周期内维护一个长连接 MCP 客户端。"""
    params = get_server_parameters()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            browser = BrowserService(session)
            await browser.cache_tools()
            app.state.browser_service = browser
            yield


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

@app.post("agent/run")
async def run_agent(task:str,session_id:str,llm,browser:BrowserDep):
    """接收用户任务并启动 Agent；具体接线仍待实现。"""
    # 每次用户发送新的任务消息时调用该接口。

    
