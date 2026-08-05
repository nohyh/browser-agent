"""MCP runtime 的最小异步生命周期监督器。"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable


class BrowserRuntimeSupervisor:
    """让 API 进程和 MCP/browser runtime 的就绪状态解耦。"""

    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._context: Any | None = None
        self._service: Any | None = None
        self._lock = asyncio.Lock()
        self._rebuild_task: asyncio.Task[None] | None = None
        self.status = "stopped"
        self.last_error: str | None = None
        self.generation = 0

    @property
    def service(self) -> Any | None:
        return self._service

    async def start(self) -> None:
        async with self._lock:
            if self.status == "ready" and self._service is not None:
                return
            self.status = "starting"
            self.last_error = None
            try:
                await self._open_locked()
            except asyncio.CancelledError:
                self.status = "stopped"
                raise
            except Exception as exc:
                self.status = "degraded"
                self.last_error = str(exc) or type(exc).__name__

    async def rebuild(self) -> None:
        """合并并发重建请求，避免 transport 故障造成重启风暴。"""
        task = self._rebuild_task
        if task is None or task.done():
            task = asyncio.create_task(self._rebuild_once())
            self._rebuild_task = task
        await task

    async def _rebuild_once(self) -> None:
        async with self._lock:
            self.status = "rebuilding"
            self.last_error = None
            try:
                await self._close_locked()
                await self._open_locked()
            except asyncio.CancelledError:
                self.status = "stopped"
                raise
            except Exception as exc:
                self.status = "degraded"
                self.last_error = str(exc) or type(exc).__name__

    async def _open_locked(self) -> None:
        candidate = self._factory()
        if inspect.isawaitable(candidate):
            candidate = await candidate
        if hasattr(candidate, "__aenter__"):
            self._context = candidate
            service = await candidate.__aenter__()
        else:
            self._context = None
            service = candidate
        self._service = service
        self.generation += 1
        self.status = "ready"

    async def stop(self) -> None:
        async with self._lock:
            try:
                await self._close_locked()
            finally:
                self.status = "stopped"

    async def _close_locked(self) -> None:
        service = self._service
        context = self._context
        self._service = None
        self._context = None
        errors: list[Exception] = []
        if service is not None:
            close_all = getattr(service, "close_all_sessions", None)
            if callable(close_all):
                try:
                    result = close_all()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    errors.append(exc)
        if context is not None:
            try:
                result = context.__aexit__(None, None, None)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[0]

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ready": self.status == "ready" and self._service is not None,
            "last_error": self.last_error,
            "generation": self.generation,
        }
