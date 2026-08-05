"""MCP 浏览器进程配置、连接适配和工具调用封装。"""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Literal
from urllib import request
from urllib.parse import quote, urlsplit
from uuid import uuid4

from mcp import ClientSession
from mcp.types import PaginatedRequestParams  # 显式导入 MCP 分页请求参数模型

from app.browser.visual import VISUAL_OVERLAY_CLEANUP_SCRIPT
from app.browser_launcher import BrowserLauncher
from app.browser_process import (
    get_chrome_cdp_candidates,
    run_agent_browser_cli,
)
from app.models import ToolBehavior
from app.session_registry import SessionRegistry
from app.utils.errors import ToolValidationError


__all__ = [
    "BrowserService",
    "BrowserSessionDisconnected",
    "BrowserToolTimeout",
    "ManagedBrowserSession",
    "ToolValidationError",
]
from app.utils.tools import get_tool_behavior, validate_tool_arguments


BROWSER_TOOL_TIMEOUT_SECONDS = 30
BROWSER_SESSION_HEALTH_TIMEOUT_SECONDS = 5
SLOW_BROWSER_TOOL_SECONDS = 5
PROJECT_RUNTIME_SESSION_PREFIX = "browser-agent-"
BROWSER_STARTUP_URL = "about:blank"
RUNTIME_DISCONNECT_MARKERS = (
    "failed to connect",
    "connection refused",
    "connection closed",
    "server disconnected",
    "transport closed",
    "broken pipe",
    "browser not launched",
    "runtime is not ready",
    "runtime is no longer active",
    "session is not active",
    "no active page",
)
class BrowserToolTimeout(TimeoutError):
    """可被 Agent 和 API 稳定识别的 MCP 工具超时。"""

    code = "tool_timeout"
    retryable = True

    def __init__(
        self,
        tool_name: str,
        timeout_seconds: float,
        *,
        phase: Literal["mcp_response", "agent_browser"] = "mcp_response",
        duration_ms: int | None = None,
    ):
        self.tool_name = tool_name
        self.timeout_seconds = timeout_seconds
        self.phase: Literal["mcp_response", "agent_browser"] = phase
        self.duration_ms = duration_ms
        super().__init__(
            f"{tool_name} timed out after {timeout_seconds} seconds"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": type(self).__name__,
            "code": self.code,
            "message": str(self),
            "tool_name": self.tool_name,
            "timeout_seconds": self.timeout_seconds,
            "phase": self.phase,
            "duration_ms": self.duration_ms,
            "retryable": self.retryable,
        }


class BrowserSessionDisconnected(RuntimeError):
    """恢复 runtime 后不再透明重放的浏览器调用。"""

    def __init__(
        self,
        tool_name: str,
        behavior: ToolBehavior,
        *,
        recovered: bool,
        cause: Exception,
    ):
        self.tool_name = tool_name
        self.behavior = behavior
        self.recovered = recovered
        self.uncertain = behavior.potential_write
        self.retryable = behavior.category != "potential_write"
        self.code = (
            "action_uncertain"
            if self.uncertain
            else "session_disconnected"
        )
        self.cause = cause
        super().__init__(
            f"{tool_name} was not replayed after browser runtime recovery: "
            f"{cause}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": type(self).__name__,
            "code": self.code,
            "message": str(self),
            "tool_name": self.tool_name,
            "retryable": self.retryable,
            "uncertain": self.uncertain,
            "recovered": self.recovered,
        }


@dataclass
class ManagedBrowserSession:
    """后端持有的最小浏览器会话状态。"""

    browser_session_id: str
    runtime_session_id: str
    mode: Literal["current", "isolated", "existing"]
    ownership: Literal["backend", "external"]
    status: Literal[
        "starting",
        "recovering",
        "ready",
        "disconnected",
        "error",
        "closed",
    ] = "starting"
    url: str | None = None
    cdp_url: str | None = None
    runtime_cdp_url: str | None = None
    expected_url: str | None = None
    last_error: str | None = None
    page_count: int = 0

    @property
    def ready(self) -> bool:
        return self.status == "ready"


def unwrap(result: Any) -> Any:
    """统一解析 MCP 调用结果，并将协议错误转换为 Python 异常。"""
    # MCP Python SDK 使用 snake_case 字段；保留旧 alias 兼容现有客户端和 fake。
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    structured = structured or {}
    response = structured.get("response")

    is_error = getattr(result, "is_error", None)
    if is_error is None:
        is_error = getattr(result, "isError", False)
    if is_error:
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

    def __init__(
        self,
        session: ClientSession,
        lifecycle_sink: Callable[[dict[str, Any]], None] | None = None,
        launcher: BrowserLauncher | None = None,
    ):
        self.session = session
        self.lifecycle_sink = lifecycle_sink
        self.launcher = launcher or BrowserLauncher(
            cli=lambda *arguments: run_agent_browser_cli(*arguments),
            cdp_candidates=lambda: get_chrome_cdp_candidates(),
        )
        self.registry = SessionRegistry()
        # 完整工具 schema 只缓存于后端，不直接发送给 LLM。
        self.tools: List[Any] = []
        # 保留旧属性，便于现有调用方和测试逐步迁移到 registry。
        self.sessions = self.registry.sessions
        self._cdp_owners = self.registry.cdp_owners
        self._registry_lock = self.registry.lock
        self._session_locks = self.registry.session_locks
        self._tool_locks: dict[str, asyncio.Lock] = {}

    def _emit_lifecycle(
        self,
        managed: ManagedBrowserSession,
        event: str,
        error: Exception | None = None,
    ) -> None:
        """发布最小生命周期状态；诊断失败不能影响浏览器操作。"""
        if self.lifecycle_sink is None:
            return
        payload: dict[str, Any] = {
            "type": "browser_session",
            "event": event,
            "browser_session_id": managed.browser_session_id,
            "runtime_session_id": managed.runtime_session_id,
            "mode": managed.mode,
            "ownership": managed.ownership,
            "status": managed.status,
            "url": managed.url,
            "page_count": managed.page_count,
        }
        if error is not None:
            payload["error"] = {
                "type": type(error).__name__,
                "message": str(error) or type(error).__name__,
            }
        try:
            self.lifecycle_sink(payload)
        except Exception:
            pass

    async def start_session(
        self,
        browser_session_id: str,
        mode: Literal["current", "isolated", "existing"] = "isolated",
        cdp_url: str | None = None,
        expected_url: str | None = None,
    ) -> ManagedBrowserSession:
        """幂等启动显式选择的当前浏览器、隔离浏览器或 CDP 浏览器。"""
        cdp_url = self._validate_session_config(mode, cdp_url)
        expected_url = expected_url.strip() if expected_url else None
        if mode != "current" and expected_url is not None:
            raise ValueError("expected_url is only valid when mode is 'current'")
        session_lock = self._session_locks.setdefault(
            browser_session_id,
            asyncio.Lock(),
        )

        async with session_lock:
            async with self._registry_lock:
                managed = self.sessions.get(browser_session_id)
                if managed is not None:
                    if managed.mode != mode or managed.cdp_url != cdp_url:
                        raise ValueError(
                            f"Browser session '{browser_session_id}' "
                            "already exists with different settings"
                        )
                    if managed.ready:
                        self._emit_lifecycle(managed, "reused")
                        return managed
                    managed.status = "starting"
                    managed.last_error = None
                    managed.expected_url = expected_url
                else:
                    managed = ManagedBrowserSession(
                        browser_session_id=browser_session_id,
                        runtime_session_id=(
                            f"{PROJECT_RUNTIME_SESSION_PREFIX}{uuid4().hex}"
                        ),
                        mode=mode,
                        ownership=(
                            "external" if mode in {"current", "existing"} else "backend"
                        ),
                        cdp_url=cdp_url,
                        expected_url=expected_url,
                    )
                self._claim_cdp_target(managed)
                self.sessions[browser_session_id] = managed
            self._emit_lifecycle(managed, "start_requested")

            try:
                response = await self._start_runtime(managed)
                self._update_session_url(managed, response)
                if not await self._runtime_is_ready(managed):
                    raise RuntimeError(
                        "agent-browser runtime did not become ready"
                    )
            except Exception as exc:
                await self._cleanup_failed_runtime(managed)
                managed.status = "error"
                managed.last_error = str(exc) or type(exc).__name__
                await self._release_cdp_target(managed)
                self._emit_lifecycle(managed, "start_failed", exc)
                raise

            managed.status = "ready"
            self._emit_lifecycle(managed, "ready")
            return managed

    async def _start_runtime(self, managed: ManagedBrowserSession) -> Any:
        if managed.mode == "current":
            # 当前浏览器是用户的明确选择，连接失败必须直接返回，禁止静默换绑。
            response = await self._connect_current_browser(managed)
            managed.ownership = "external"
            return response
        if os.name == "nt":
            # Windows 上 MCP open 的子进程管道会被 Chrome 继承，改用非管道 CLI 适配器。
            if managed.mode == "isolated":
                return await self._start_isolated_runtime(managed)
            else:
                assert managed.cdp_url is not None
                command = ["connect", managed.cdp_url]
            return await self.launcher.run(
                "--session",
                managed.runtime_session_id,
                *command,
                "--json",
            )
        if managed.mode == "existing":
            return await self._call_runtime_tool(
                "agent_browser_connect",
                {
                    "target": managed.cdp_url,
                    "session": managed.runtime_session_id,
                },
            )
        return await self._call_runtime_tool(
            "agent_browser_open",
            {
                "url": BROWSER_STARTUP_URL,
                "session": managed.runtime_session_id,
                "extraArgs": ["--args", BROWSER_STARTUP_URL],
            },
        )

    async def _start_isolated_runtime(self, managed: ManagedBrowserSession) -> Any:
        response = await self.launcher.run(
            "--session",
            managed.runtime_session_id,
            "open",
            BROWSER_STARTUP_URL,
            "--json",
        )
        if os.name == "nt":
            try:
                cdp_response = await self.launcher.run(
                    "--session",
                    managed.runtime_session_id,
                    "get",
                    "cdp-url",
                    "--json",
                )
                data = cdp_response.get("data", {}) if isinstance(cdp_response, dict) else {}
                cdp_url = data.get("cdpUrl")
                if isinstance(cdp_url, str) and cdp_url:
                    await asyncio.to_thread(
                        self._close_internal_new_tab_targets,
                        cdp_url,
                    )
            except Exception:
                # 清理是兼容层；失败不能让已经可用的受控页一起失效。
                pass
        return response

    async def _connect_current_browser(self, managed: ManagedBrowserSession) -> Any:
        """连接整个 Chrome；URL 仅用于选择初始标签页。"""
        selected_cdp_url = None
        tabs: list[dict[str, Any]] = []
        connected = False
        for cdp_url in self.launcher.cdp_candidates():
            try:
                tabs_response = await self.launcher.run(
                    "--session",
                    managed.runtime_session_id,
                    "--cdp",
                    cdp_url,
                    "tab",
                    "list",
                    "--json",
                )
            except Exception:
                continue
            connected = True
            data = (
                tabs_response.get("data", {})
                if isinstance(tabs_response, dict)
                else {}
            )
            raw_tabs = data.get("tabs", []) if isinstance(data, dict) else []
            tabs = [tab for tab in raw_tabs if isinstance(tab, dict)]
            if tabs:
                selected_cdp_url = cdp_url
                break
        if selected_cdp_url is None:
            if connected:
                raise RuntimeError("current Chrome has no debuggable tabs")
            raise RuntimeError(
                "No running Chrome found. Enable remote debugging at "
                "chrome://inspect/#remote-debugging and allow the connection."
            )
        managed.runtime_cdp_url = selected_cdp_url
        managed.page_count = len(tabs)
        # 重连时优先恢复上一次真实页面；启动提示只作为第二选择。
        target = tabs[0]
        for preferred_url in (managed.url, managed.expected_url):
            matched = next(
                (
                    tab
                    for tab in tabs
                    if self._same_page_url(tab.get("url"), preferred_url)
                ),
                None,
            )
            if matched is not None:
                target = matched
                break
        target_id = target.get("tabId")
        if not isinstance(target_id, str):
            raise RuntimeError("current Chrome returned an invalid target tab")
        response = await self.launcher.run(
            "--session",
            managed.runtime_session_id,
            "--cdp",
            selected_cdp_url,
            "tab",
            target_id,
            "--json",
        )
        managed.url = target.get("url") or managed.expected_url
        return response

    @staticmethod
    def _same_page_url(first: Any, second: Any) -> bool:
        if not isinstance(first, str) or not isinstance(second, str):
            return False
        return first.rstrip("/") == second.rstrip("/")

    @staticmethod
    def _close_internal_new_tab_targets(cdp_url: str) -> None:
        """通过 DevTools HTTP 端点关闭 agent-browser 列表看不到的 Chrome 内部页。"""
        parsed = urlsplit(cdp_url)
        scheme = "https" if parsed.scheme == "wss" else "http"
        if not parsed.hostname or not parsed.port:
            return
        origin = f"{scheme}://{parsed.hostname}:{parsed.port}"
        with request.urlopen(f"{origin}/json/list", timeout=2) as response:
            targets = json.loads(response.read().decode("utf-8"))
        internal_ids: set[str] = set()
        for target in targets if isinstance(targets, list) else []:
            if not isinstance(target, dict):
                continue
            url = target.get("url")
            target_id = target.get("id")
            if (
                target.get("type") == "page"
                and isinstance(url, str)
                and url.startswith("chrome://newtab")
                and isinstance(target_id, str)
            ):
                internal_ids.add(target_id)
                with request.urlopen(
                    f"{origin}/json/close/{quote(target_id, safe='')}",
                    timeout=2,
                ):
                    pass
        if not internal_ids:
            return
        # /json/close 是异步关闭，短轮询确认 target 真正消失后再报告 ready。
        for _ in range(20):
            time.sleep(0.05)
            with request.urlopen(f"{origin}/json/list", timeout=2) as response:
                remaining = json.loads(response.read().decode("utf-8"))
            remaining_ids = {
                target.get("id")
                for target in remaining if isinstance(remaining, list)
                if isinstance(target, dict)
            }
            if internal_ids.isdisjoint(remaining_ids):
                return
        raise TimeoutError("Chrome internal new tab did not close in time")

    async def _close_runtime(self, managed: ManagedBrowserSession) -> Any:
        if os.name == "nt":
            return await self.launcher.run(
                "--session",
                managed.runtime_session_id,
                "close",
                "--json",
            )
        return await self._call_runtime_tool(
            "agent_browser_close",
            {"session": managed.runtime_session_id},
        )

    async def _cleanup_failed_runtime(
        self,
        managed: ManagedBrowserSession,
    ) -> None:
        try:
            await self._close_runtime(managed)
        except Exception:
            pass

    def _claim_cdp_target(self, managed: ManagedBrowserSession) -> None:
        """同一 CDP 目标只允许一个后端逻辑会话控制。"""
        self.registry.claim_cdp_target(managed)

    async def _release_cdp_target(
        self,
        managed: ManagedBrowserSession,
    ) -> None:
        await self.registry.release_cdp_target(managed)

    async def _call_runtime_tool(
        self,
        name: str,
        arguments: dict,
        *,
        timeout: float | None = None,
    ) -> Any:
        timeout = (
            BROWSER_TOOL_TIMEOUT_SECONDS
            if timeout is None
            else timeout
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(name, arguments=arguments),
                timeout=timeout,
            )
        except TimeoutError as exc:
            error = BrowserToolTimeout(
                name,
                timeout,
                phase="mcp_response",
                duration_ms=round((loop.time() - started) * 1000),
            )
            self._emit_transport(name, arguments, error=error)
            raise error from exc
        except Exception as exc:
            self._emit_transport(
                name,
                arguments,
                error=exc,
                phase="mcp_response",
                duration_ms=round((loop.time() - started) * 1000),
            )
            raise
        try:
            response = unwrap(result)
        except RuntimeError as exc:
            message = str(exc).lower()
            if "timed out" in message or "timeout" in message:
                error = BrowserToolTimeout(
                    name,
                    timeout,
                    phase="agent_browser",
                    duration_ms=round((loop.time() - started) * 1000),
                )
                self._emit_transport(name, arguments, error=error)
                raise error from exc
            self._emit_transport(
                name,
                arguments,
                error=exc,
                phase="agent_browser",
                duration_ms=round((loop.time() - started) * 1000),
            )
            raise
        duration_ms = round((loop.time() - started) * 1000)
        if duration_ms >= SLOW_BROWSER_TOOL_SECONDS * 1000:
            self._emit_transport(
                name,
                arguments,
                phase="mcp_response",
                duration_ms=duration_ms,
            )
        return response

    def _emit_transport(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        error: Exception | None = None,
        phase: Literal["mcp_response", "agent_browser"] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """只记录慢调用和失败调用，避免传输观测再次制造日志膨胀。"""
        if self.lifecycle_sink is None:
            return
        if isinstance(error, BrowserToolTimeout):
            phase = error.phase
            duration_ms = error.duration_ms
        event: dict[str, Any] = {
            "type": "browser_transport",
            "event": "tool_failed" if error is not None else "tool_slow",
            "tool_name": tool_name,
            "runtime_session_id": arguments.get("session"),
            "status": (
                "timed_out"
                if isinstance(error, BrowserToolTimeout)
                else "failed" if error is not None else "succeeded"
            ),
            "phase": phase,
            "duration_ms": duration_ms,
        }
        if error is not None:
            event["error"] = {
                "type": type(error).__name__,
                "message": str(error) or type(error).__name__,
            }
        try:
            self.lifecycle_sink(event)
        except Exception:
            pass

    @staticmethod
    def _update_session_url(
        managed: ManagedBrowserSession,
        response: Any,
    ) -> None:
        data = response.get("data") if isinstance(response, dict) else None
        if isinstance(data, dict) and isinstance(data.get("url"), str):
            managed.url = data["url"]

    async def _runtime_is_ready(
        self,
        managed: ManagedBrowserSession,
    ) -> bool:
        response = await self._call_runtime_tool(
            "agent_browser_session_info",
            self._runtime_tool_arguments(managed, {}),
            timeout=BROWSER_SESSION_HEALTH_TIMEOUT_SECONDS,
        )
        data = response.get("data", {}) if isinstance(response, dict) else {}
        runtime = data.get("runtime", {})
        page_count = runtime.get("pageCount", 0)
        managed.page_count = (
            page_count
            if isinstance(page_count, int) and not isinstance(page_count, bool)
            else 0
        )
        return (
            data.get("active") is True
            and runtime.get("browserLaunched") is True
            and managed.page_count > 0
            and data.get("runtimeError") is None
        )

    @staticmethod
    def _runtime_tool_arguments(
        managed: ManagedBrowserSession,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """外部浏览器每次调用都重申 CDP 身份，禁止空会话自动启动独立浏览器。"""
        runtime_arguments = {
            **arguments,
            "session": managed.runtime_session_id,
        }
        if managed.mode == "current":
            cdp_url = managed.runtime_cdp_url
        elif managed.mode == "existing":
            cdp_url = managed.cdp_url
        else:
            cdp_url = None
        if cdp_url is not None:
            runtime_arguments["extraArgs"] = ["--cdp", cdp_url]
        return runtime_arguments

    @staticmethod
    def _validate_session_config(
        mode: str,
        cdp_url: str | None,
    ) -> str | None:
        """校验模式组合，并阻止参数进入 Windows cmd 控制字符。"""
        if mode not in {"current", "isolated", "existing"}:
            raise ValueError(f"Unsupported browser session mode: {mode}")
        if mode in {"current", "isolated"}:
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
        await self.registry.forget(managed)

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

        tool_lock = self._tool_locks.setdefault(
            browser_session_id,
            asyncio.Lock(),
        )
        try:
            async with tool_lock:
                ready = await self._runtime_is_ready(managed)
        except Exception as exc:
            ready = False
            managed.last_error = str(exc) or type(exc).__name__

        managed.status = "ready" if ready else "disconnected"
        self._emit_lifecycle(managed, "health_checked")
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

    async def _remove_visual_overlay(
        self,
        managed: ManagedBrowserSession,
    ) -> None:
        """关闭连接前兜底移除页面覆盖层；清理失败不阻止 runtime 回收。"""
        if not any(tool.name == "agent_browser_eval" for tool in self.tools):
            return
        try:
            await self.call_tool(
                managed.browser_session_id,
                "agent_browser_eval",
                {"script": VISUAL_OVERLAY_CLEANUP_SCRIPT},
            )
        except Exception:
            pass

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
            close_error: Exception | None = None
            try:
                await self._remove_visual_overlay(managed)
                await self._close_runtime(managed)
            except Exception as exc:
                close_error = exc
                raise
            finally:
                await self._forget_session(managed)
                self._emit_lifecycle(managed, "closed", close_error)
            return managed

    async def close_all_sessions(self) -> dict[str, Exception]:
        """退出前关闭全部托管会话，并继续处理单个关闭失败。"""
        failures: dict[str, Exception] = {}
        for managed in self.list_sessions():
            try:
                await self.close_session(managed.browser_session_id)
            except Exception as exc:
                failures[managed.browser_session_id] = exc
        return failures

    async def cleanup_orphaned_sessions(self) -> list[str]:
        """清理上次异常退出遗留的本项目 runtime 会话。"""
        response = await self.launcher.run(
            "session",
            "list",
            "--json",
        )
        data = response.get("data", {}) if isinstance(response, dict) else {}
        sessions = data.get("sessions", [])
        active = {
            managed.runtime_session_id
            for managed in self.sessions.values()
        }
        orphaned = sorted(
            session_id
            for session_id in sessions
            if (
                isinstance(session_id, str)
                and session_id.startswith(PROJECT_RUNTIME_SESSION_PREFIX)
                and session_id not in active
            )
        )
        for session_id in orphaned:
            await self.launcher.run(
                "--session",
                session_id,
                "close",
                "--json",
            )
        return orphaned

    async def list_tools(self) -> List[Any]:
        """按 MCP 游标分页读取全部工具。"""
        tools = []
        cursor = None
        while True:
            page = await self.session.list_tools(
                params=PaginatedRequestParams(cursor=cursor) if cursor else None
            )
            tools.extend(page.tools)
            cursor = getattr(page, "next_cursor", getattr(page, "nextCursor", None))
            if cursor is None:
                return tools

    async def cache_tools(self) -> List[Any]:
        """在应用启动时缓存工具，避免 Agent 每轮重复发现。"""
        self.tools = await self.list_tools()
        return self.tools

    async def observe_page_state(
        self,
        browser_session_id: str,
        *,
        previous_snapshot_hash: str | None = None,
    ) -> dict[str, Any] | None:
        """读取轻量页面状态，并在可用时返回 diff snapshot。"""
        tool_names = {getattr(tool, "name", None) for tool in self.tools}
        if "agent_browser_diff_snapshot" not in tool_names:
            return None
        state: dict[str, Any] = {"observation_kind": "diff"}
        for name, key in (
            ("agent_browser_get_url", "url"),
            ("agent_browser_get_title", "title"),
        ):
            if name not in tool_names:
                continue
            try:
                response = await self.call_tool(
                    browser_session_id,
                    name,
                    {},
                )
            except Exception:
                return None
            value = response.get("data") if isinstance(response, dict) else None
            if isinstance(value, dict):
                value = value.get(key)
            if isinstance(value, str) and value:
                state[key] = value
        try:
            diff = await self.call_tool(
                browser_session_id,
                "agent_browser_diff_snapshot",
                {"interactive": True, "compact": True},
            )
        except Exception:
            return None
        if isinstance(diff, dict):
            state.update(diff)
        if previous_snapshot_hash is not None:
            state["previous_snapshot_hash"] = previous_snapshot_hash
        return state

    async def call_tool(
        self,
        browser_session_id: str,
        name: str,
        arguments: dict,
        behavior: ToolBehavior | None = None,
    ) -> Any:
        """调用浏览器工具，并强制注入当前逻辑浏览器会话。"""
        managed = self.sessions.get(browser_session_id)
        if managed is None:
            raise RuntimeError(
                f"Browser session '{browser_session_id}' is not managed"
            )

        # session 和外部 CDP 均由后端注入，模型不能跨浏览器或触发本地回退。
        tool_arguments = dict(arguments)
        tool = next(
            (
                candidate
                for candidate in self.tools
                if getattr(candidate, "name", None) == name
            ),
            None,
        )
        behavior = behavior or get_tool_behavior(name, tool)
        tool_lock = self._tool_locks.setdefault(
            browser_session_id,
            asyncio.Lock(),
        )
        async with tool_lock:
            validate_tool_arguments(name, tool_arguments, self.tools)
            try:
                response = await self._call_runtime_tool(
                    name,
                    self._runtime_tool_arguments(managed, tool_arguments),
                )
            except Exception as exc:
                if not self._should_recover_runtime(managed, exc):
                    raise
                await self._recover_runtime(managed, exc)
                if behavior.retry_policy != "read_once":
                    raise BrowserSessionDisconnected(
                        name,
                        behavior,
                        recovered=True,
                        cause=exc,
                    ) from exc
                try:
                    response = await self._call_runtime_tool(
                        name,
                        self._runtime_tool_arguments(managed, tool_arguments),
                    )
                except Exception as retry_exc:
                    if self._is_runtime_disconnect(retry_exc):
                        managed.status = "disconnected"
                        managed.last_error = (
                            str(retry_exc) or type(retry_exc).__name__
                        )
                        self._emit_lifecycle(
                            managed,
                            "runtime_recovery_failed",
                            retry_exc,
                        )
                    raise

        self._update_session_url(managed, response)
        if name == "agent_browser_close":
            await self._forget_session(managed)
        return response

    @staticmethod
    def _is_runtime_disconnect(exc: Exception) -> bool:
        """只识别 runtime/传输层失联，页面与元素错误必须原样返回。"""
        if isinstance(exc, BrowserToolTimeout):
            return False
        if isinstance(exc, (ConnectionError, EOFError)):
            return True
        message = str(exc).casefold()
        return any(marker in message for marker in RUNTIME_DISCONNECT_MARKERS)

    def _should_recover_runtime(
        self,
        managed: ManagedBrowserSession,
        exc: Exception,
    ) -> bool:
        # 外部浏览器仍独立存活，适合轻量重连；隔离浏览器不应被静默导航到空白页。
        return (
            managed.mode in {"current", "existing"}
            and self._is_runtime_disconnect(exc)
        )

    async def _recover_runtime(
        self,
        managed: ManagedBrowserSession,
        cause: Exception,
    ) -> None:
        """复用逻辑/runtime Session 与原 CDP，恢复后由调用方仅重试一次。"""
        managed.status = "recovering"
        managed.last_error = str(cause) or type(cause).__name__
        self._emit_lifecycle(managed, "runtime_recovery_started", cause)
        try:
            response = await self._start_runtime(managed)
            self._update_session_url(managed, response)
            if not await self._runtime_is_ready(managed):
                raise RuntimeError("agent-browser runtime did not recover")
        except Exception as exc:
            managed.status = "disconnected"
            managed.last_error = str(exc) or type(exc).__name__
            self._emit_lifecycle(managed, "runtime_recovery_failed", exc)
            raise

        managed.status = "ready"
        managed.last_error = None
        self._emit_lifecycle(managed, "runtime_recovery_succeeded")
