"""Agent 任务执行、实时轨迹和取消服务。"""

import asyncio
import inspect
import json
from collections import deque
from collections.abc import AsyncIterator, Callable
from inspect import isawaitable
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agent import Agent
from app.api.schemas import AgentRunRequest
from app.mcp_client import BrowserService
from app.models import AgentResult


STREAM_QUEUE_MAXSIZE = 64


class BoundedTraceQueue:
    """非阻塞、有界的公开轨迹缓冲；慢客户端不会拖住 Agent。"""

    def __init__(self, maxsize: int = STREAM_QUEUE_MAXSIZE):
        self.maxsize = max(1, maxsize)
        self._items: deque[dict[str, Any]] = deque()
        self._ready = asyncio.Event()
        self.dropped_count = 0
        self.merged_count = 0

    def publish(self, event: dict[str, Any]) -> None:
        if self._can_merge(event):
            self._items[-1] = event
            self.merged_count += 1
            self._ready.set()
            return
        if len(self._items) >= self.maxsize:
            self._items.popleft()
            self.dropped_count += 1
        self._items.append(event)
        self._ready.set()

    async def get(self) -> dict[str, Any]:
        while not self._items:
            self._ready.clear()
            await self._ready.wait()
        event = self._items.popleft()
        if not self._items:
            self._ready.clear()
        return event

    def empty(self) -> bool:
        return not self._items

    def qsize(self) -> int:
        return len(self._items)

    @staticmethod
    def _is_mergeable(event: dict[str, Any]) -> bool:
        if not event or event.get("type") != "trace":
            return False
        payload = event.get("event")
        if not isinstance(payload, dict) or payload.get("kind") not in {
            "thinking",
            "action",
        }:
            return False
        return True

    def _same_progress(
        self,
        first: dict[str, Any],
        second: dict[str, Any],
    ) -> bool:
        if not self._is_mergeable(first) or not self._is_mergeable(second):
            return False
        first_event = first["event"]
        second_event = second["event"]
        return (
            first_event.get("kind"),
            first_event.get("status"),
            first_event.get("title"),
            first_event.get("detail"),
        ) == (
            second_event.get("kind"),
            second_event.get("status"),
            second_event.get("title"),
            second_event.get("detail"),
        )

    def _can_merge(self, event: dict[str, Any]) -> bool:  # type: ignore[override]
        if not self._items:
            return False
        return self._same_progress(self._items[-1], event)


async def _execute_agent_unlocked(
    payload: AgentRunRequest,
    request: Request,
    browser: BrowserService,
    *,
    trace_dir: Path,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> AgentResult:
    """执行新任务，或在已有会话中继续对话。"""
    state = request.app.state
    agent_llm = getattr(state, "agent_llm", None)
    if payload.llm_endpoint_id is not None and payload.llm_model is not None:
        registry = getattr(state, "llm_registry", None)
        if registry is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "llm_selection_not_configured",
                    "message": "模型配置尚未同步。",
                },
            )
        try:
            agent_llm = registry.resolve(payload.llm_endpoint_id, payload.llm_model)
        except (AttributeError, KeyError) as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "llm_selection_not_configured",
                    "message": str(exc) or "模型配置尚未同步。",
                },
            ) from exc
    if agent_llm is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "llm_not_configured",
                "message": "尚未配置 LLM，请先在设置中保存模型配置。",
            },
        )

    if not await browser.refresh_session_ready(payload.browser_session_id):
        managed = browser.get_session(payload.browser_session_id)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "browser_session_not_ready",
                "browser_session_id": payload.browser_session_id,
                "status": managed.status if managed is not None else "missing",
                "message": (
                    f"Browser session '{payload.browser_session_id}' "
                    "is not ready or is no longer active; "
                    "start it via POST /browser/session/start first"
                ),
            },
        )

    agents: dict[str, Agent] = state.agents
    agent = agents.get(payload.conversation_id)
    if agent is None:
        agent = Agent(
            task=payload.message,
            browser=browser,
            llm=agent_llm,
            trace_file=trace_dir / f"{payload.conversation_id}.md",
            event_sink=event_sink,
            conversation_id=payload.conversation_id,
        )
        agents[payload.conversation_id] = agent
    else:
        # 模型切换只作用于当前对话的下一轮，不改动其他 Agent。
        agent.llm = agent_llm
        tracer = getattr(agent, "tracer", None)
        if tracer is not None:
            tracer.event_sink = event_sink
        agent.add_user_message(payload.message)

    try:
        run = agent.run
        try:
            run_parameters = inspect.signature(run).parameters.values()
        except (TypeError, ValueError):
            run_parameters = ()
        supports_run_id = any(
            parameter.name == "run_id"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in run_parameters
        )
        if supports_run_id:
            return await run(
                payload.browser_session_id,
                run_id=payload.run_id,
            )
        return await run(payload.browser_session_id)
    finally:
        tracer = getattr(agent, "tracer", None)
        if tracer is not None:
            tracer.event_sink = None


async def execute_agent(
    payload: AgentRunRequest,
    request: Request,
    browser: BrowserService,
    *,
    trace_dir: Path,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> AgentResult:
    """同一对话串行执行，避免并发轮次交叉修改消息和轨迹。"""
    state = request.app.state
    locks = getattr(state, "agent_locks", None)
    if locks is None:
        locks = {}
        state.agent_locks = locks
    lock = locks.setdefault(payload.conversation_id, asyncio.Lock())
    async with lock:
        return await _execute_agent_unlocked(
            payload,
            request,
            browser,
            trace_dir=trace_dir,
            event_sink=event_sink,
        )


def public_trace_event(record: dict[str, Any]) -> dict[str, Any] | None:
    """把内部诊断记录转换为可展示轨迹，不暴露模型隐式推理。"""
    event_type = record.get("type")
    common = {
        "timestamp": record.get("timestamp"),
        "step_id": record.get("step_id"),
    }
    if event_type == "llm_call":
        return {
            **common,
            "kind": "thinking",
            "status": "running",
            "title": "正在分析页面并规划下一步",
        }
    if event_type == "llm_result":
        output = record.get("output") or {}
        actions: list[str] = []
        for action in output.get("actions") or []:
            if not isinstance(action, dict):
                continue
            name = action.get("name")
            if isinstance(name, str) and name:
                actions.append(name)
        return {
            **common,
            "kind": "decision",
            "status": "completed",
            "title": output.get("next_goal") or "本轮决策已完成",
            "detail": "、".join(actions) if actions else None,
        }
    if event_type == "tool_call":
        arguments = record.get("arguments") or {}
        detail = json.dumps(arguments, ensure_ascii=False, default=str)
        return {
            **common,
            "kind": "action",
            "status": "running",
            "title": f"执行 {record.get('name') or '浏览器操作'}",
            "detail": detail[:800] if detail != "{}" else None,
        }
    if event_type == "tool_result":
        succeeded = record.get("status") == "succeeded"
        error = record.get("error")
        return {
            **common,
            "kind": "action_result",
            "status": "completed" if succeeded else "failed",
            "title": (
                f"{record.get('name') or '浏览器操作'} 已完成"
                if succeeded
                else f"{record.get('name') or '浏览器操作'} 执行失败"
            ),
            "detail": (
                json.dumps(error, ensure_ascii=False, default=str)[:800]
                if error
                else None
            ),
        }
    if event_type == "token_usage":
        usage = record.get("token_usage") or record.get("usage") or {}
        total = usage.get("total_tokens")
        return {
            **common,
            "kind": "usage",
            "status": "completed",
            "title": (
                f"本次使用 {total} tokens"
                if total is not None
                else "Token 统计已更新"
            ),
        }
    if event_type == "error":
        return {
            **common,
            "kind": "error",
            "status": "failed",
            "title": str(record.get("error") or "任务执行出错"),
        }
    return None


def stream_line(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, default=str) + "\n"


async def _request_is_disconnected(request: Request) -> bool:
    checker = getattr(request, "is_disconnected", None)
    if not callable(checker):
        return False
    try:
        result = checker()
        return bool(await result) if isawaitable(result) else bool(result)
    except Exception:
        # 断线检查本身失败时继续让任务完成，避免误杀正常请求。
        return False


async def _cancel_agent_task(task: asyncio.Task[AgentResult]) -> None:
    if task.done():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def stream_events(
    payload: AgentRunRequest,
    request: Request,
    browser: BrowserService,
    *,
    trace_dir: Path,
    active_runs: dict[str, asyncio.Task[AgentResult]],
) -> AsyncIterator[str]:
    """生成一条任务的 NDJSON 事件流，并负责清理运行登记。"""
    queue = BoundedTraceQueue()
    client_disconnected = False

    def publish(record: dict[str, Any]) -> None:
        event = public_trace_event(record)
        if event is not None:
            queue.publish({"type": "trace", "event": event})

    task = asyncio.create_task(
        execute_agent(
            payload,
            request,
            browser,
            trace_dir=trace_dir,
            event_sink=publish,
        )
    )
    active_runs[payload.run_id] = task
    yield stream_line({"type": "run_started", "run_id": payload.run_id})
    try:
        while not task.done() or not queue.empty():
            if await _request_is_disconnected(request):
                client_disconnected = True
                await _cancel_agent_task(task)
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
            except TimeoutError:
                continue
            yield stream_line(event)
        result = await task
        yield stream_line({"type": "result", "result": result.model_dump()})
    except asyncio.CancelledError:
        await _cancel_agent_task(task)
        if not client_disconnected:
            yield stream_line({"type": "cancelled", "run_id": payload.run_id})
    except HTTPException as exc:
        yield stream_line(
            {"type": "error", "status": exc.status_code, "detail": exc.detail}
        )
    except Exception as exc:
        yield stream_line(
            {
                "type": "error",
                "status": 500,
                "detail": str(exc) or type(exc).__name__,
            }
        )
    finally:
        if client_disconnected:
            await _cancel_agent_task(task)
        active_runs.pop(payload.run_id, None)
    if not client_disconnected:
        yield stream_line({"type": "done", "run_id": payload.run_id})


async def stream_agent_run(
    payload: AgentRunRequest,
    request: Request,
    browser: BrowserService,
    *,
    trace_dir: Path,
) -> StreamingResponse:
    """以 NDJSON 增量返回用户可读轨迹和最终结果。"""
    state = request.app.state
    active_runs = getattr(state, "active_runs", None)
    if active_runs is None:
        active_runs = {}
        state.active_runs = active_runs
    if payload.run_id in active_runs:
        raise HTTPException(status_code=409, detail="run_id is already active")
    return StreamingResponse(
        stream_events(
            payload,
            request,
            browser,
            trace_dir=trace_dir,
            active_runs=active_runs,
        ),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store"},
    )


async def cancel_agent_run(run_id: str, request: Request) -> dict[str, Any]:
    """取消仍在执行的任务；浏览器会话本身保持可复用。"""
    active_runs = getattr(request.app.state, "active_runs", {})
    task = active_runs.get(run_id)
    if task is None or task.done():
        raise HTTPException(status_code=404, detail="Agent run not found")
    task.cancel()
    # 等待 Agent 的 finally 完成，确保页面边框和模拟鼠标已移除。
    await asyncio.gather(task, return_exceptions=True)
    return {"cancelled": True, "run_id": run_id}
