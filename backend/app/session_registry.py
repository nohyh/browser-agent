"""浏览器逻辑会话注册和 CDP 目标占用管理。"""

from __future__ import annotations

import asyncio
from typing import Any


class SessionRegistry:
    """集中管理公开会话、运行会话和外部 CDP 目标的唯一归属。"""

    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.cdp_owners: dict[str, str] = {}
        self.lock = asyncio.Lock()
        self.session_locks: dict[str, asyncio.Lock] = {}

    def get(self, browser_session_id: str) -> Any | None:
        return self.sessions.get(browser_session_id)

    def values(self) -> list[Any]:
        return list(self.sessions.values())

    def session_lock(self, browser_session_id: str) -> asyncio.Lock:
        return self.session_locks.setdefault(
            browser_session_id,
            asyncio.Lock(),
        )

    def claim_cdp_target(self, managed: Any) -> None:
        cdp_url = managed.cdp_url
        if cdp_url is None:
            return
        owner = self.cdp_owners.get(cdp_url)
        if owner is not None and owner != managed.browser_session_id:
            raise ValueError(f"CDP target is already controlled by '{owner}'")
        self.cdp_owners[cdp_url] = managed.browser_session_id

    async def release_cdp_target(self, managed: Any) -> None:
        if managed.cdp_url is None:
            return
        async with self.lock:
            if (
                self.cdp_owners.get(managed.cdp_url)
                == managed.browser_session_id
            ):
                self.cdp_owners.pop(managed.cdp_url, None)

    async def forget(self, managed: Any) -> None:
        """只移除仍指向同一对象的记录，避免旧启动任务误删新会话。"""
        async with self.lock:
            if self.sessions.get(managed.browser_session_id) is managed:
                self.sessions.pop(managed.browser_session_id, None)
            if (
                managed.cdp_url is not None
                and self.cdp_owners.get(managed.cdp_url)
                == managed.browser_session_id
            ):
                self.cdp_owners.pop(managed.cdp_url, None)
        managed.status = "closed"
        managed.page_count = 0
