"""MCP 浏览器进程配置、连接适配和工具调用封装。"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, List, Literal

from mcp import ClientSession

from app.browser_process import run_agent_browser_cli


BROWSER_TOOL_TIMEOUT_SECONDS = 30
BROWSER_SESSION_HEALTH_TIMEOUT_SECONDS = 5


@dataclass
class ManagedBrowserSession:
    """后端持有的最小浏览器会话状态。"""

    browser_session_id: str
    mode: Literal["isolated", "existing"]
    ready: bool = False
    url: str | None = None
    cdp_url: str | None = None


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
    """管理浏览器生命周期，并通过长生命周期 MCP 会话执行动作。"""

    def __init__(self, session: ClientSession):
        self.session = session
        # 完整工具 schema 只缓存于后端，不直接发送给 LLM。
        self.tools: List[Any] = []
        self.sessions: dict[str, ManagedBrowserSession] = {}
        self._cdp_owners: dict[str, str] = {}
        self._registry_lock = asyncio.Lock()
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._tool_locks: dict[str, asyncio.Lock] = {}

    async def start_session(
        self,
        browser_session_id: str,
        mode: Literal["isolated", "existing"] = "isolated",
        cdp_url: str | None = None,
    ) -> ManagedBrowserSession:
        """幂等启动隔离浏览器，或通过明确的 CDP 地址连接已有浏览器。"""
        cdp_url = self._validate_session_config(mode, cdp_url)
        session_lock = self._session_locks.setdefault(
            browser_session_id,
            asyncio.Lock(),
        )

        async with session_lock:
            async with self._registry_lock:
                current = self.sessions.get(browser_session_id)
                if current is not None:
                    if current.mode != mode or current.cdp_url != cdp_url:
                        raise ValueError(
                            f"Browser session '{browser_session_id}' "
                            "already exists with different settings"
                        )
                    if current.ready:
                        return current

                if mode == "existing":
                    owner = self._cdp_owners.get(cdp_url)
                    if owner is not None and owner != browser_session_id:
                        raise ValueError(
                            f"CDP target is already controlled by '{owner}'"
                        )
                    self._cdp_owners[cdp_url] = browser_session_id

                managed = ManagedBrowserSession(
                    browser_session_id=browser_session_id,
                    mode=mode,
                    cdp_url=cdp_url,
                )
                self.sessions[browser_session_id] = managed

            arguments = [
                "--session",
                browser_session_id,
            ]
            if cdp_url is not None:
                arguments.extend(["--cdp", cdp_url])
            # get url 可以完成冷启动探测，同时不会重置已有页面。
            arguments.extend(["get", "url", "--json"])

            try:
                await run_agent_browser_cli(*arguments)
                result = await self.call_tool(
                    browser_session_id=browser_session_id,
                    name="agent_browser_get_url",
                    arguments={},
                )
                data = result.get("data", {}) if isinstance(result, dict) else {}
                url = data.get("url")
                if not isinstance(url, str):
                    raise RuntimeError(
                        "agent-browser session probe returned no URL"
                    )
            except Exception:
                await self._forget_session(managed)
                raise

            managed.url = url
            managed.ready = True
            return managed

    @staticmethod
    def _validate_session_config(
        mode: str,
        cdp_url: str | None,
    ) -> str | None:
        """校验模式组合，并阻止参数进入 Windows cmd 控制字符。"""
        if mode not in {"isolated", "existing"}:
            raise ValueError(f"Unsupported browser session mode: {mode}")
        if mode == "isolated":
            if cdp_url is not None:
                raise ValueError(
                    "cdp_url is only valid when mode is 'existing'"
                )
            return None
        if cdp_url is None or not cdp_url.strip():
            raise ValueError(
                "cdp_url is required when mode is 'existing'"
            )

        cdp_url = cdp_url.strip()
        if any(character in cdp_url for character in "&|<>^\r\n"):
            raise ValueError("cdp_url contains unsupported characters")
        if not (
            cdp_url.isdigit()
            or cdp_url.startswith(
                ("http://", "https://", "ws://", "wss://")
            )
        ):
            raise ValueError(
                "cdp_url must be a CDP port or HTTP/WebSocket address"
            )
        return cdp_url

    async def _forget_session(
        self,
        managed: ManagedBrowserSession,
    ) -> None:
        """只清理当前记录，避免失败任务误删后来创建的同名会话。"""
        async with self._registry_lock:
            if self.sessions.get(managed.browser_session_id) is managed:
                self.sessions.pop(managed.browser_session_id, None)
            if (
                managed.cdp_url is not None
                and self._cdp_owners.get(managed.cdp_url)
                == managed.browser_session_id
            ):
                self._cdp_owners.pop(managed.cdp_url, None)
        managed.ready = False

    def is_session_ready(self, browser_session_id: str) -> bool:
        """判断浏览器会话是否已经完成启动和 MCP 探测。"""
        managed = self.sessions.get(browser_session_id)
        return managed is not None and managed.ready

    async def refresh_session_ready(
        self,
        browser_session_id: str,
    ) -> bool:
        """任务开始前读取真实进程状态，识别被用户手动关闭的浏览器。"""
        managed = self.sessions.get(browser_session_id)
        if managed is None or not managed.ready:
            return False

        try:
            result = await asyncio.wait_for(
                self.session.call_tool(
                    "agent_browser_session_info",
                    arguments={"session": browser_session_id},
                ),
                timeout=BROWSER_SESSION_HEALTH_TIMEOUT_SECONDS,
            )
            response = unwrap(result)
            data = (
                response.get("data", {})
                if isinstance(response, dict)
                else {}
            )
            runtime = data.get("runtime", {})
            ready = (
                data.get("active") is True
                and runtime.get("browserLaunched") is True
                and runtime.get("pageCount", 0) > 0
                and data.get("runtimeError") is None
            )
        except Exception:
            ready = False

        managed.ready = ready
        return ready

    def list_sessions(self) -> list[ManagedBrowserSession]:
        """返回当前后端进程管理的浏览器会话。"""
        return sorted(
            self.sessions.values(),
            key=lambda managed: managed.browser_session_id,
        )

    def get_session(
        self,
        browser_session_id: str,
    ) -> ManagedBrowserSession | None:
        """按前端会话标识查询浏览器状态。"""
        return self.sessions.get(browser_session_id)

    async def close_session(
        self,
        browser_session_id: str,
    ) -> ManagedBrowserSession:
        """只关闭指定逻辑会话；existing 模式仅断开外部浏览器连接。"""
        session_lock = self._session_locks.setdefault(
            browser_session_id,
            asyncio.Lock(),
        )
        async with session_lock:
            managed = self.sessions.get(browser_session_id)
            if managed is None:
                raise KeyError(browser_session_id)
            try:
                await run_agent_browser_cli(
                    "--session",
                    browser_session_id,
                    "close",
                    "--json",
                )
            finally:
                await self._forget_session(managed)
            return managed

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
        browser_session_id: str,
        name: str,
        arguments: dict,
    ) -> Any:
        """调用浏览器工具，并强制注入当前逻辑浏览器会话。"""
        managed = self.sessions.get(browser_session_id)
        if managed is None:
            raise RuntimeError(
                f"Browser session '{browser_session_id}' is not managed"
            )

        # session 由后端注入，模型不能跨浏览器操作。
        arguments = {
            **arguments,
            "session": browser_session_id,
        }
        tool_lock = self._tool_locks.setdefault(
            browser_session_id,
            asyncio.Lock(),
        )
        async with tool_lock:
            try:
                result = await asyncio.wait_for(
                    self.session.call_tool(name, arguments=arguments),
                    timeout=BROWSER_TOOL_TIMEOUT_SECONDS,
                )
            except TimeoutError as exc:
                raise TimeoutError(
                    f"{name} timed out after "
                    f"{BROWSER_TOOL_TIMEOUT_SECONDS} seconds"
                ) from exc

        response = unwrap(result)
        if isinstance(response, dict):
            data = response.get("data")
            if isinstance(data, dict) and isinstance(data.get("url"), str):
                managed.url = data["url"]
        if name == "agent_browser_close":
            await self._forget_session(managed)
        return response
