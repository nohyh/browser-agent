"""MCP 浏览器进程配置、连接适配和工具调用封装。"""

import json
import os
from typing import Any, List

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters


def get_server_parameters() -> StdioServerParameters:
    """根据当前操作系统构造 agent-browser MCP 的启动参数。"""
    load_dotenv()
    session_id = os.getenv("AGENT_BROWSER_SESSION", "personal-agent")
    env = dict(os.environ)
    env.setdefault("AGENT_BROWSER_AUTO_CONNECT", "1")
    env.setdefault("AGENT_BROWSER_SESSION", session_id)

    if os.name == "nt":
        return StdioServerParameters(
            command="cmd.exe",
            args=["/d", "/s", "/c", "agent-browser.cmd mcp --tools all"],
            env=env,
        )

    return StdioServerParameters(
        command="agent-browser",
        args=["mcp", "--tools", "all"],
        env=env,
    )

def unwrap(result: Any) -> Any:
    """统一解析 MCP 调用结果，并将协议错误转换为 Python 异常。"""
    structured = getattr(result, "structuredContent", None) or {}
    response = structured.get("response")

    if getattr(result, "isError", False):
        text = "\n".join(
            item.text for item in getattr(result, "content", [])
            if getattr(item, "type", None) == "text"
        )
        raise RuntimeError(text or json.dumps(structured, ensure_ascii=False))

    if isinstance(response, dict) and response.get("success") is False:
        raise RuntimeError(
            response.get("error")
            or response.get("message")
            or json.dumps(response, ensure_ascii=False)
        )

    return response if response is not None else structured


class BrowserService:
    """在长生命周期 MCP 会话之上提供稳定的浏览器工具接口。"""

    def __init__(self, session: ClientSession):
        self.session = session
        # 完整工具 schema 只缓存于后端，不直接发送给 LLM。
        self.tools: List[Any] = []

    async def list_tools(self) -> List[Any]:
        """按 MCP 游标分页读取全部工具。"""
        tools = []
        cursor = None
        while True:
            page = await self.session.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.nextCursor
            if cursor is None:
                return tools

    async def cache_tools(self) -> List[Any]:
        """在应用启动时缓存工具，避免 Agent 每轮重复发现。"""
        self.tools = await self.list_tools()
        return self.tools

    async def call_tool(
        self,
        session_id: str,
        name: str,
        arguments: dict,
    ) -> Any:
        """调用浏览器工具，并强制注入当前逻辑浏览器会话。"""
        # session_id 由后端控制，避免模型遗漏或篡改目标会话。
        arguments = {**arguments, "session": session_id}
        result = await self.session.call_tool(name, arguments=arguments)
        return unwrap(result)
