"""agent-browser CLI/CDP 启动能力的窄适配层。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp import StdioServerParameters


class BrowserLauncher:
    """把进程启动、调试端点发现和 MCP 参数构造隔离出会话服务。"""

    def __init__(
        self,
        *,
        cli: Callable[..., Awaitable[dict[str, Any] | None]] | None = None,
        cdp_candidates: Callable[[], list[str]] | None = None,
        server_parameters: Callable[[], StdioServerParameters] | None = None,
    ) -> None:
        if cli is None or cdp_candidates is None or server_parameters is None:
            from app.browser_process import (
                get_chrome_cdp_candidates,
                get_server_parameters,
                run_agent_browser_cli,
            )

            cli = cli or run_agent_browser_cli
            cdp_candidates = cdp_candidates or get_chrome_cdp_candidates
            server_parameters = server_parameters or get_server_parameters
        self._cli = cli
        self._cdp_candidates = cdp_candidates
        self._server_parameters = server_parameters

    async def run(self, *arguments: str) -> dict[str, Any] | None:
        return await self._cli(*arguments)

    def cdp_candidates(self) -> list[str]:
        return self._cdp_candidates()

    def server_parameters(self) -> StdioServerParameters:
        return self._server_parameters()
