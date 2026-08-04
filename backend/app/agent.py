"""浏览器 Agent 的最小执行循环与结构化输出模型。"""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from app.llm import AgentLLM
from app.mcp_client import BrowserService, VISUAL_OVERLAY_CLEANUP_SCRIPT
from app.models import (
    AgentAction,
    AgentDecision,
    AgentResult,
    AgentTokenUsage,
)
from app.trace import TraceRecorder, extract_snapshot, redact_value
from app.utils.tools import (
    REGISTERED_TOOL_NAMES,
    TOOL_GETTER_NAMES,
    get_tool_group,
    select_mcp_tools_for_llm,
)


OBSERVATION_REQUIRED_ACTIONS = {
    "agent_browser_open",
    "agent_browser_back",
    "agent_browser_forward",
    "agent_browser_reload",
    "agent_browser_click",
    "agent_browser_dblclick",
    "agent_browser_hover",
    "agent_browser_scroll",
    "agent_browser_scroll_into_view",
    "agent_browser_drag",
    "agent_browser_press",
    "agent_browser_check",
    "agent_browser_uncheck",
    "agent_browser_select",
    "agent_browser_tab_switch",
    "agent_browser_tab_new",
    "agent_browser_tab_close",
    "agent_browser_window_new",
    "agent_browser_frame_switch",
    "agent_browser_frame_main",
    "agent_browser_dialog_accept",
    "agent_browser_dialog_dismiss",
    "agent_browser_wait_for_selector",
    "agent_browser_wait_for_text",
    "agent_browser_wait_for_url",
    "agent_browser_wait_for_load",
    "agent_browser_wait_for_function",
    "agent_browser_eval",
    "agent_browser_batch",
    "agent_browser_pushstate",
    "agent_browser_auth_login",
    "agent_browser_state_load",
    "agent_browser_connect",
}
#压缩时保留最近两轮的原始对话记录不被压缩
CONVERSATION_KEEP_RECENT_TURNS = 2
BARE_REF_SELECTOR_PATTERN = re.compile(r"^\s*@?(e\d+)\s*$", re.IGNORECASE)
ATTRIBUTE_REF_SELECTOR_PATTERN = re.compile(
    r"""^\s*\[\s*ref\s*=\s*['"]?(e\d+)['"]?\s*\]\s*$""",
    re.IGNORECASE,
)
VISUAL_TARGET_ACTIONS = {
    "agent_browser_click",
    "agent_browser_dblclick",
    "agent_browser_fill",
    "agent_browser_type",
    "agent_browser_focus",
    "agent_browser_hover",
    "agent_browser_press",
    "agent_browser_check",
    "agent_browser_uncheck",
    "agent_browser_select",
}
VISUAL_CLICK_ACTIONS = {
    "agent_browser_click",
    "agent_browser_dblclick",
    "agent_browser_check",
    "agent_browser_uncheck",
    "agent_browser_select",
}
VISUAL_OVERLAY_SCRIPT = r"""
(() => {
  const layerId = 'browser-agent-visual-layer';
  const existing = document.getElementById(layerId);
  if (existing && window.__browserAgentVisual) return true;
  if (existing) existing.remove();

  const host = document.createElement('div');
  host.id = layerId;
  host.setAttribute('aria-hidden', 'true');
  Object.assign(host.style, {
    position: 'fixed',
    inset: '0',
    zIndex: '2147483647',
    pointerEvents: 'none',
    contain: 'strict'
  });

  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `
    <style>
      :host { all: initial; }
      .edge {
        position: fixed;
        inset: 0;
        border-radius: 6px;
        box-shadow:
          inset 0 0 0 1px rgba(226, 109, 90, .52),
          inset 0 0 22px rgba(226, 109, 90, .16);
        animation: browser-agent-edge-pulse 2.4s ease-in-out infinite;
      }
      .cursor {
        position: fixed;
        left: 0;
        top: 0;
        width: 20px;
        height: 24px;
        opacity: 0;
        transform: translate3d(-32px, -32px, 0);
        transition: transform 180ms cubic-bezier(.2,.8,.2,1), opacity 120ms ease;
        will-change: transform;
      }
      .cursor::before {
        content: '';
        position: absolute;
        inset: 0;
        background: #fff;
        clip-path: polygon(0 0, 0 20px, 5px 15px, 9px 24px, 13px 22px, 9px 14px, 17px 14px);
        filter: drop-shadow(0 1px 1px rgba(20, 18, 16, .75));
      }
      .cursor::after {
        content: '';
        position: absolute;
        left: -10px;
        top: -10px;
        width: 38px;
        height: 38px;
        border: 1.5px solid rgba(226, 109, 90, .82);
        border-radius: 50%;
        opacity: 0;
        transform: scale(.35);
      }
      .cursor.is-clicking::after {
        animation: browser-agent-click 420ms ease-out;
      }
      @keyframes browser-agent-edge-pulse {
        0%, 100% { opacity: .65; }
        50% { opacity: 1; }
      }
      @keyframes browser-agent-click {
        0% { opacity: .9; transform: scale(.35); }
        100% { opacity: 0; transform: scale(1.15); }
      }
      @media (prefers-reduced-motion: reduce) {
        .edge { animation: none; }
        .cursor { transition: none; }
      }
    </style>
    <div class="edge"></div>
    <div class="cursor"></div>
  `;
  (document.documentElement || document.body).appendChild(host);

  const cursor = root.querySelector('.cursor');
  window.__browserAgentVisual = {
    host,
    cursor,
    move(x, y, clicking) {
      cursor.style.opacity = '1';
      cursor.style.transform = `translate3d(${x}px, ${y}px, 0)`;
      cursor.classList.remove('is-clicking');
      if (clicking) {
        void cursor.offsetWidth;
        cursor.classList.add('is-clicking');
      }
    }
  };
  return true;
})()
"""
class Agent:
    """负责观察页面、请求 LLM 决策并执行浏览器动作。"""

    FRESH_RESULT_TEXT_LIMIT = 12_000
    FRESH_RESULT_COUNT_LIMIT = 3
    RESULT_SUMMARY_PREVIEW_LIMIT = 800
    REPEATED_ACTION_LIMIT = 3

    def __init__(
        self,
        task: str,
        browser: BrowserService,
        llm: AgentLLM,
        max_steps: int = 20,
        trace_file: Path | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ):
        initial_message = {"role": "user", "content": task}
        # messages 保存完整对话；task_context 只保存当前任务的运行信息。
        self.messages: list[dict[str, str]] = [initial_message]
        self.task_context: list[dict[str, Any]] = []
        self._current_pages: list[dict[str, str]] = []
        #之前对话的概括
        self._conversation_summary: str | None = None
        #前面被压缩的消息总数，作为指针继续压缩未压缩的部分
        self._summary_message_count = 0
        self._conversation_compaction_attempted = False
        self.tracer = TraceRecorder(
            trace_file,
            self._tool_outcome,
            event_sink=event_sink,
        )
        self._record({"type": "message", **initial_message})
        self.browser = browser
        self.llm = llm
        self.max_steps = max_steps
        self._visual_overlay_started = False
        self._run_count = 0
        self._last_action_signature: str | None = None
        self._repeated_action_count = 0

    @property
    def trace(self) -> list[dict[str, Any]]:
        return self.tracer.events

    def _record(self, event: dict[str, Any]) -> None:
        self.tracer.record(event)

    async def observe(
        self,
        browser_session_id: str,
        trace_context: dict[str, str] | None = None,
    ) -> Any:
        """获取当前页面的交互元素快照，作为本轮唯一页面状态。"""
        return await self._call_tool(
            browser_session_id=browser_session_id,
            name="agent_browser_snapshot",
            arguments={"interactive": True, "compact": True},
            trace_context=trace_context,
        )

    def add_user_message(self, content: str) -> None:
        """追加用户消息，并同步写入不参与模型上下文的完整记录。"""
        message = {"role": "user", "content": content}
        self.messages.append(message)
        self._conversation_compaction_attempted = False
        self._record({"type": "message", **message})

    async def run(self, browser_session_id: str) -> AgentResult:
        """循环执行“观察、决策、动作”，直到完成或达到最大步数。"""
        self._visual_overlay_started = False
        self._last_action_signature = None
        self._repeated_action_count = 0
        self._run_count += 1
        run_id = f"run-{self._run_count}"
        try:
            return await self._run_loop(browser_session_id, run_id)
        finally:
            if self._visual_overlay_started:
                await self._remove_visual_overlay(
                    browser_session_id,
                    {
                        "run_id": run_id,
                        "step_id": f"{run_id}:cleanup",
                        "action_id": f"{run_id}:cleanup:visual-overlay",
                    },
                )

    async def _run_loop(
        self,
        browser_session_id: str,
        run_id: str,
    ) -> AgentResult:
        """执行 Agent 主循环，由调用方统一管理可视化层生命周期。"""
        observation: Any = None
        observation_id: str | None = None
        observation_fingerprint: tuple[str | None, str] | None = None
        observation_required = True
        pending_outcome: dict[str, Any] | None = None
        pending_fingerprint: tuple[str | None, str] | None = None
        token_usage = await self._maybe_compact_conversation()

        for step_number in range(1, self.max_steps + 1):
            step_id = f"{run_id}:step-{step_number}"
            try:
                if observation_required:
                    observation_id = f"{step_id}:observation"
                    observation = await self.observe(
                        browser_session_id,
                        {
                            "run_id": run_id,
                            "step_id": step_id,
                            "action_id": observation_id,
                        },
                    )
                    stabilization_retried = False
                    if (
                        pending_outcome is not None
                        and self._observation_is_empty(observation)
                    ):
                        stabilization_retried = True
                        await asyncio.sleep(0.2)
                        observation_id = f"{step_id}:observation-retry"
                        observation = await self.observe(
                            browser_session_id,
                            {
                                "run_id": run_id,
                                "step_id": step_id,
                                "action_id": observation_id,
                            },
                        )
                    self._record_page_visit(observation)
                    new_fingerprint = self._page_fingerprint(observation)
                    if pending_outcome is not None:
                        self._apply_page_effect(
                            pending_outcome,
                            before=pending_fingerprint,
                            after=new_fingerprint,
                        )
                        pending_outcome["effect"][
                            "observation_id"
                        ] = observation_id
                        pending_outcome["effect"].update(
                            {
                                "stabilization_retried": (
                                    stabilization_retried
                                ),
                                "stabilized": not self._observation_is_empty(
                                    observation
                                ),
                            }
                        )
                        self._record(pending_outcome.copy())
                    observation_fingerprint = new_fingerprint
                    observation_required = False
                    pending_outcome = None
                    pending_fingerprint = None

                visible_tools = select_mcp_tools_for_llm(self.browser.tools)
                allowed_names = REGISTERED_TOOL_NAMES | TOOL_GETTER_NAMES
                llm_task_context = self._llm_task_context()
                llm_messages = self._llm_messages()
                input_metrics = self.llm.input_metrics(
                    observation,
                    llm_task_context,
                )
                self._record(
                    {
                        "type": "llm_call",
                        "run_id": run_id,
                        "step_id": step_id,
                        "observation_id": observation_id,
                        "browser_session_id": browser_session_id,
                        "observation": observation,
                        "messages": llm_messages,
                        "task_context": llm_task_context,
                        "input_metrics": input_metrics,
                    }
                )
                # 调用 LLM 获取下一步动作决策及本轮 Token 消耗
                decision, call_usage = await self.llm.decide(
                    observation=observation,
                    messages=llm_messages,
                    task_context=llm_task_context,
                    tools=visible_tools,
                    conversation_summary=self._conversation_summary,
                )
                if call_usage is not None:
                    token_usage = (
                        call_usage
                        if token_usage is None
                        else token_usage.merged(call_usage)
                    )
                llm_result = {
                    "type": "llm_result",
                    "run_id": run_id,
                    "step_id": step_id,
                    "output": decision.model_dump(),
                }
                if call_usage is not None:
                    llm_result["token_usage"] = call_usage.model_dump()
                self._record(llm_result)
                # 大型工具正文只完整进入紧邻的一轮，之后收敛为摘要和哈希。
                self._consume_fresh_results()
            except Exception as exc:
                failed_usage = getattr(exc, "token_usage", None)
                if isinstance(failed_usage, AgentTokenUsage):
                    token_usage = (
                        failed_usage
                        if token_usage is None
                        else token_usage.merged(failed_usage)
                    )
                error_type = getattr(
                    exc,
                    "error_type",
                    type(exc).__name__,
                )
                error_event = {
                    "type": "error",
                    "stage": "loop",
                    "error_type": error_type,
                    "error": str(exc) or error_type,
                }
                provider_details = getattr(exc, "details", None)
                if provider_details is not None:
                    error_event["provider_details"] = provider_details
                self._record(error_event)
                if str(error_type).startswith("provider_output_"):
                    answer = (
                        "模型返回格式异常，未能生成可靠的最终结果，请重试。"
                    )
                else:
                    answer = f"Agent decision failed: {exc}"
                return self._finish(
                    success=False,
                    answer=answer,
                    token_usage=token_usage,
                )

            if decision.status in {"completed", "blocked"}:
                return self._finish(
                    success=decision.status == "completed",
                    answer=decision.final_answer,
                    token_usage=token_usage,
                    status=decision.status,
                )

            # 将模型确认的进度放入下一轮上下文，避免长任务只依赖工具结果。
            self._append_task_context(
                {
                    "type": "agent_progress",
                    "evaluation_previous_goal": (
                        decision.evaluation_previous_goal
                    ),
                    "memory": decision.memory,
                    "next_goal": decision.next_goal,
                }
            )

            # 限制单轮动作数量，避免模型一次生成过长且难以验证的操作链。
            for action_number, action in enumerate(decision.actions, start=1):
                action = self._normalize_action(action)
                action_context = {
                    "run_id": run_id,
                    "step_id": step_id,
                    "action_id": f"{step_id}:action-{action_number}",
                    "browser_session_id": browser_session_id,
                }
                if action.name not in allowed_names:
                    rejected_result = self._tool_outcome(
                        name=action.name,
                        arguments=action.arguments,
                        error="tool is not registered",
                        trace_context=action_context,
                    )
                    self._append_task_context(rejected_result)
                    self._record(rejected_result.copy())
                    break

                if self._register_action(
                    action,
                    observation_fingerprint,
                ) >= self.REPEATED_ACTION_LIMIT:
                    message = (
                        "RepeatedAction: identical action made no page "
                        "progress twice"
                    )
                    repeated_result = self._tool_outcome(
                        name=action.name,
                        arguments=action.arguments,
                        error={
                            "type": "RepeatedAction",
                            "message": message,
                        },
                        trace_context=action_context,
                    )
                    stored_result = self._append_task_context(
                        repeated_result
                    )
                    self._record(stored_result.copy())
                    return self._finish(
                        success=False,
                        answer=(
                            "Agent stopped because the same action made "
                            "no page progress twice"
                        ),
                        token_usage=token_usage,
                    )

                if action.name in TOOL_GETTER_NAMES:
                    self._record(
                        {
                            "type": "tool_call",
                            **action_context,
                            "name": action.name,
                            "arguments": action.arguments,
                        }
                    )
                    try:
                        group_tools = get_tool_group(
                            self.browser.tools,
                            action.name,
                        )
                        outcome = self._tool_outcome(
                            name=action.name,
                            arguments=action.arguments,
                            result=group_tools,
                            trace_context=action_context,
                        )
                    except Exception as exc:
                        outcome = self._tool_outcome(
                            name=action.name,
                            arguments=action.arguments,
                            error=self._error_details(exc),
                            trace_context=action_context,
                        )
                    self._append_task_context(outcome)
                    self._record(outcome.copy())
                    # 工具组结果要先回到模型，且不需要重新抓取页面。
                    break

                requires_observation = (
                    action.name in OBSERVATION_REQUIRED_ACTIONS
                )
                await self._prepare_visual_action(
                    browser_session_id,
                    action,
                    action_context,
                )
                try:
                    result = await self._call_tool(
                        browser_session_id=browser_session_id,
                        name=action.name,
                        arguments=action.arguments,
                        trace_context=action_context,
                        record_result=False,
                    )
                    outcome = self._tool_outcome(
                        name=action.name,
                        arguments=action.arguments,
                        result=result,
                        trace_context=action_context,
                    )
                except Exception as exc:
                    outcome = self._tool_outcome(
                        name=action.name,
                        arguments=action.arguments,
                        error=self._error_details(exc),
                        uncertain=(
                            requires_observation
                            and isinstance(exc, TimeoutError)
                        ),
                        trace_context=action_context,
                    )
                    stored_outcome = self._append_task_context(outcome)
                    if requires_observation and isinstance(exc, TimeoutError):
                        pending_outcome = stored_outcome
                        pending_fingerprint = observation_fingerprint
                        observation_required = True
                    else:
                        self._record(stored_outcome.copy())
                    break

                stored_outcome = self._append_task_context(outcome)

                # 可能改变页面的动作在下一轮只观察一次，并据此补充实际效果。
                if requires_observation:
                    pending_outcome = stored_outcome
                    pending_fingerprint = observation_fingerprint
                    observation_required = True
                    break
                self._record(stored_outcome.copy())

        return self._finish(
            success=False,
            answer="Agent reached the maximum number of steps without finishing",
            token_usage=token_usage,
        )

    @classmethod
    def _normalize_action(cls, action: AgentAction) -> AgentAction:
        """把模型常见的 ref 写法收敛为 agent-browser 接受的 @eN。"""
        selector = action.arguments.get("selector")
        if not isinstance(selector, str):
            return action
        match = (
            BARE_REF_SELECTOR_PATTERN.fullmatch(selector)
            or ATTRIBUTE_REF_SELECTOR_PATTERN.fullmatch(selector)
        )
        if match is None:
            return action
        arguments = {
            **action.arguments,
            "selector": f"@{match.group(1).lower()}",
        }
        return action.model_copy(update={"arguments": arguments})

    def _register_action(
        self,
        action: AgentAction,
        page_fingerprint: tuple[str | None, str] | None,
    ) -> int:
        signature = json.dumps(
            [action.name, action.arguments, page_fingerprint],
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        )
        if signature == self._last_action_signature:
            self._repeated_action_count += 1
        else:
            self._last_action_signature = signature
            self._repeated_action_count = 1
        return self._repeated_action_count

    @staticmethod
    def _observation_is_empty(observation: Any) -> bool:
        snapshot = extract_snapshot(observation)
        if snapshot is None:
            return True
        normalized = snapshot.strip().lower()
        return normalized in {
            "",
            "(no interactive elements)",
            "(empty snapshot)",
        }

    @staticmethod
    def _error_details(exc: Exception) -> dict[str, Any]:
        serializer = getattr(exc, "as_dict", None)
        if callable(serializer):
            return serializer()
        return {
            "type": type(exc).__name__,
            "message": str(exc) or type(exc).__name__,
            "retryable": isinstance(exc, TimeoutError),
        }

    async def _prepare_visual_action(
        self,
        browser_session_id: str,
        action: AgentAction,
        trace_context: dict[str, str],
    ) -> None:
        """在真实操作前用一次轻量位置读取更新模拟指针。"""
        selector = action.arguments.get("selector")
        if (
            action.name not in VISUAL_TARGET_ACTIONS
            or not isinstance(selector, str)
            or not selector
        ):
            return
        if not self._has_browser_tool("agent_browser_eval"):
            return
        installed = await self._visual_eval(
            browser_session_id,
            VISUAL_OVERLAY_SCRIPT,
            purpose="visual_overlay_install",
            trace_context=trace_context,
        )
        if not installed:
            return
        self._visual_overlay_started = True

        if not self._has_browser_tool("agent_browser_get_box"):
            return
        box_succeeded, raw_box = await self._call_visual_tool(
            browser_session_id,
            "agent_browser_get_box",
            {"selector": selector},
            purpose="visual_target_box",
            trace_arguments={"selector": selector},
            trace_context=trace_context,
        )
        if not box_succeeded:
            return
        box = self._extract_visual_box(raw_box)
        if box is None:
            return
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        clicking = action.name in VISUAL_CLICK_ACTIONS
        await self._visual_eval(
            browser_session_id,
            self._visual_pointer_script(x, y, clicking),
            purpose="visual_pointer_move",
            trace_context=trace_context,
        )

    async def _remove_visual_overlay(
        self,
        browser_session_id: str,
        trace_context: dict[str, str] | None = None,
    ) -> None:
        """任务正常、失败或异常结束时都移除注入的显示层。"""
        await self._visual_eval(
            browser_session_id,
            VISUAL_OVERLAY_CLEANUP_SCRIPT,
            purpose="visual_overlay_cleanup",
            trace_context=trace_context,
        )
        self._visual_overlay_started = False

    async def _visual_eval(
        self,
        browser_session_id: str,
        script: str,
        *,
        purpose: str,
        trace_context: dict[str, str] | None = None,
    ) -> bool:
        """可视化是附加能力，失败不得影响 Agent 主任务。"""
        succeeded, _ = await self._call_visual_tool(
            browser_session_id,
            "agent_browser_eval",
            {"script": script},
            purpose=purpose,
            trace_arguments={"script_characters": len(script)},
            trace_context=trace_context,
        )
        return succeeded

    async def _call_visual_tool(
        self,
        browser_session_id: str,
        name: str,
        arguments: dict[str, Any],
        *,
        purpose: str,
        trace_arguments: dict[str, Any],
        trace_context: dict[str, str] | None = None,
    ) -> tuple[bool, Any]:
        """统一记录内部可视化调用，避免辅助失败成为不可见的等待。"""
        context = dict(trace_context or {})
        parent_action_id = context.get("action_id")
        if parent_action_id:
            context["action_id"] = (
                f"{parent_action_id}:internal:{purpose}"
            )
        context["browser_session_id"] = browser_session_id
        summary = {
            "internal": True,
            "purpose": purpose,
            **trace_arguments,
        }
        self._record(
            {
                "type": "tool_call",
                **context,
                "name": name,
                "arguments": summary,
            }
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        error = None
        result = None
        try:
            result = await self.browser.call_tool(
                browser_session_id=browser_session_id,
                name=name,
                arguments=arguments,
            )
        except Exception as exc:
            error = self._error_details(exc)
        succeeded = error is None
        self._record(
            {
                "type": "tool_result",
                **context,
                "name": name,
                "arguments": summary,
                "status": "succeeded" if succeeded else "failed",
                "data": {
                    "duration_ms": round((loop.time() - started) * 1000)
                },
                "error": error,
                "effect": {
                    "dispatched": succeeded,
                    "page_changed": None,
                },
            }
        )
        return succeeded, result

    def _has_browser_tool(self, name: str) -> bool:
        return any(tool.name == name for tool in self.browser.tools)

    @classmethod
    def _extract_visual_box(
        cls,
        value: Any,
    ) -> dict[str, float] | None:
        if isinstance(value, dict):
            keys = ("x", "y", "width", "height")
            if all(
                isinstance(value.get(key), (int, float))
                and not isinstance(value.get(key), bool)
                for key in keys
            ):
                return {key: float(value[key]) for key in keys}
            for nested in value.values():
                box = cls._extract_visual_box(nested)
                if box is not None:
                    return box
        return None

    @staticmethod
    def _visual_pointer_script(
        x: float,
        y: float,
        clicking: bool,
    ) -> str:
        click_literal = "true" if clicking else "false"
        return f"""
(() => {{
  const visual = window.__browserAgentVisual;
  if (!visual || !visual.cursor) return false;
  visual.move({x:.1f}, {y:.1f}, {click_literal});
  return true;
}})()
"""

    def _append_task_context(
        self,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        """保存当前任务的最新进度或完整工具结果，任务结束后再精简。"""
        safe_item = redact_value(item)
        if safe_item.get("type") == "tool_result":
            fresh_items = [
                existing
                for existing in self.task_context
                if existing.get("_fresh") is True
            ]
            if len(fresh_items) >= self.FRESH_RESULT_COUNT_LIMIT:
                self._summarize_tool_result(fresh_items[0])
            stored_item = self._compact_fresh_result_value(safe_item)
            source_text = json.dumps(
                safe_item.get("data"),
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            )
            retained_text = json.dumps(
                stored_item.get("data"),
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            )
            stored_item["data_meta"] = {
                "sha256": hashlib.sha256(
                    source_text.encode("utf-8")
                ).hexdigest(),
                "source_characters": len(source_text),
                "retained_characters": len(retained_text),
                "truncated": len(retained_text) < len(source_text),
            }
            stored_item["_fresh"] = True
        else:
            stored_item = self._compact_task_value(safe_item)
        if stored_item.get("type") == "agent_progress":
            self.task_context[:] = [
                existing
                for existing in self.task_context
                if existing.get("type") != "agent_progress"
            ]
        self.task_context.append(stored_item)
        return stored_item

    def _consume_fresh_results(self) -> None:
        for item in self.task_context:
            if item.get("_fresh") is True:
                self._summarize_tool_result(item)

    @classmethod
    def _summarize_tool_result(cls, item: dict[str, Any]) -> None:
        item.pop("_fresh", None)
        data = item.get("data")
        text = json.dumps(
            data,
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        )
        data_meta = item.get("data_meta") or {}
        source_characters = data_meta.get("source_characters", len(text))
        if source_characters <= 2_000:
            return
        item["data"] = {
            "sha256": data_meta.get("sha256")
            or hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "characters": source_characters,
            "preview": text[: cls.RESULT_SUMMARY_PREVIEW_LIMIT],
        }
        item["data_compacted"] = True

    def _llm_task_context(self) -> list[dict[str, Any]]:
        """组合当前任务页面、工具结果和最新进度。"""
        context: list[dict[str, Any]] = []
        if self._current_pages:
            context.append(
                {
                    "type": "current_task_pages",
                    "pages": [dict(page) for page in self._current_pages],
                }
            )
        context.extend(dict(item) for item in self.task_context)
        return context

    def _llm_messages(self) -> list[dict[str, str]]:
        """只把尚未进入历史摘要的完整对话轮次交给决策模型。"""
        return [
            dict(message)
            for message in self.messages[self._summary_message_count :]
        ]

    async def _maybe_compact_conversation(
        self,
    ) -> AgentTokenUsage | None:
        """对话过长时压缩较早完整轮次，同时保留原始消息供展示。"""
        if self._conversation_compaction_attempted:
            return None
        self._conversation_compaction_attempted = True
        unsummarized = self.messages[self._summary_message_count :]
        context_size = len(self._conversation_summary or "") + sum(
            len(message.get("content", ""))
            for message in unsummarized
        )
        if (
            context_size <= self.llm.CONVERSATION_CONTEXT_LIMIT
            and len(unsummarized) <= self.llm.MESSAGE_LIMIT
        ):
            return None

        completed_end = len(self.messages)
        if (
            completed_end > self._summary_message_count
            and self.messages[-1].get("role") == "user"
        ):
            completed_end -= 1
        compact_end = max(
            self._summary_message_count,
            completed_end - (CONVERSATION_KEEP_RECENT_TURNS * 2),
        )
        if compact_end <= self._summary_message_count:
            return None

        old_messages = self.messages[
            self._summary_message_count : compact_end
        ]
        try:
            summary, usage = await self.llm.compact_conversation_history(
                previous_summary=self._conversation_summary,
                messages=old_messages,
            )
        except Exception as exc:
            self._record(
                {
                    "type": "error",
                    "stage": "conversation_compaction",
                    "error_type": type(exc).__name__,
                    "error": str(exc) or type(exc).__name__,
                }
            )
            return None

        self._conversation_summary = summary
        self._summary_message_count = compact_end
        return usage

    def _record_page_visit(self, observation: Any) -> None:
        """从最新观察提取 URL/title，并按访问顺序去除连续重复页面。"""
        page = self._extract_page_visit(observation)
        if page is None or (
            self._current_pages and self._current_pages[-1] == page
        ):
            return
        self._current_pages.append(page)

    @classmethod
    def _extract_page_visit(cls, value: Any) -> dict[str, str] | None:
        if not isinstance(value, dict):
            return None
        url = value.get("url") or value.get("origin")
        title = value.get("title")
        if isinstance(url, str) or isinstance(title, str):
            page: dict[str, str] = {}
            if isinstance(url, str) and url:
                page["url"] = str(redact_value(url))
            if isinstance(title, str) and title:
                page["title"] = str(redact_value(title))
            return page or None
        for key in ("data", "response"):
            page = cls._extract_page_visit(value.get(key))
            if page is not None:
                return page
        return None

    @classmethod
    def _completed_action(
        cls,
        tool_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """把运行期工具结果收敛为历史对话中的动作、参数和状态。"""
        if tool_result.get("type") != "tool_result":
            return None
        name = tool_result.get("name")
        if (
            not isinstance(name, str)
            or name == "agent_browser_snapshot"
            or name in TOOL_GETTER_NAMES
        ):
            return None
        action = {
            "name": name,
            "arguments": cls._compact_task_value(
                redact_value(tool_result.get("arguments") or {})
            ),
            "status": tool_result.get("status") or "unknown",
        }
        error = tool_result.get("error")
        if error:
            action["error"] = cls._compact_task_value(error)
        return action

    def _assistant_history_content(
        self,
        status: str,
        answer: str,
    ) -> str:
        """把当前 task_context 过滤为执行过程，并与最终回答交错保存。"""
        actions = [
            action
            for tool_result in self.task_context
            if (action := self._completed_action(tool_result)) is not None
        ]
        sections = []
        if self._current_pages or actions:
            process = {
                "pages": [dict(page) for page in self._current_pages],
                "actions": actions,
            }
            sections.append(
                "<执行过程>\n"
                + json.dumps(
                    process,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n</执行过程>"
            )
        sections.append(
            f'<最终回答 status="{status}">{answer}</最终回答>'
        )
        return "\n\n".join(sections)

    def _clear_current_task_context(self) -> None:
        self.task_context.clear()
        self._current_pages.clear()

    @classmethod
    def _compact_task_value(cls, value: Any) -> Any:
        """在进入内存上下文前限制工具正文，避免大结果占满进程内存。"""
        if isinstance(value, str):
            if len(value) <= 4_000:
                return value
            return value[:3_970] + "\n... [result truncated]"
        if isinstance(value, dict):
            return {
                key: cls._compact_task_value(item)
                for key, item in value.items()
                if key not in {"refs", "snapshot"}
            }
        if isinstance(value, list):
            return [
                cls._compact_task_value(item)
                for item in value[-20:]
            ]
        return value

    @classmethod
    def _compact_fresh_result_value(cls, value: Any) -> Any:
        """保留下一轮需要的工具正文，同时移除页面树的重复副本。"""
        if isinstance(value, str):
            if len(value) <= cls.FRESH_RESULT_TEXT_LIMIT:
                return value
            suffix = "\n... [result truncated]"
            return value[: cls.FRESH_RESULT_TEXT_LIMIT - len(suffix)] + suffix
        if isinstance(value, dict):
            return {
                key: cls._compact_fresh_result_value(item)
                for key, item in value.items()
                if key not in {"refs", "snapshot"}
            }
        if isinstance(value, list):
            return [
                cls._compact_fresh_result_value(item)
                for item in value[-50:]
            ]
        return value

    @classmethod
    def _tool_outcome(
        cls,
        name: str,
        arguments: dict[str, Any],
        result: Any = None,
        error: Any = None,
        uncertain: bool = False,
        trace_context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """把不同 MCP 返回统一成模型可稳定判断的工具结果。"""
        if error is not None:
            outcome = {
                "type": "tool_result",
                "name": name,
                "arguments": arguments,
                "status": "uncertain" if uncertain else "failed",
                "data": None,
                "error": error,
                "effect": {
                    "dispatched": uncertain,
                    "page_changed": None,
                },
            }
        else:
            status = "succeeded"
            data = result
            result_error = None
            if isinstance(result, dict):
                if result.get("success") is False:
                    status = "failed"
                    result_error = (
                        result.get("error")
                        or result.get("message")
                        or "tool reported failure"
                    )
                if "data" in result:
                    data = result.get("data")
                else:
                    data = {
                        key: value
                        for key, value in result.items()
                        if key not in {"success", "error", "message"}
                    }
            outcome = {
                "type": "tool_result",
                "name": name,
                "arguments": arguments,
                "status": status,
                "data": data,
                "error": result_error,
                "effect": {
                    "dispatched": status == "succeeded",
                    "page_changed": None,
                },
            }
        if trace_context:
            outcome.update(trace_context)
        return outcome

    @classmethod
    def _page_fingerprint(
        cls,
        observation: Any,
    ) -> tuple[str | None, str]:
        payload = observation
        if isinstance(observation, dict) and isinstance(
            observation.get("data"),
            dict,
        ):
            payload = observation["data"]
        url = None
        if isinstance(payload, dict):
            candidate_url = payload.get("url") or payload.get("origin")
            if isinstance(candidate_url, str):
                url = candidate_url
        snapshot = extract_snapshot(payload)
        if snapshot is None:
            snapshot = json.dumps(
                payload,
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            )
        return url, hashlib.sha256(snapshot.encode("utf-8")).hexdigest()

    @staticmethod
    def _apply_page_effect(
        outcome: dict[str, Any],
        before: tuple[str | None, str] | None,
        after: tuple[str | None, str],
    ) -> None:
        if before is None:
            return
        url_changed = before[0] != after[0]
        snapshot_changed = before[1] != after[1]
        page_changed = url_changed or snapshot_changed
        outcome["effect"] = {
            "dispatched": outcome["effect"]["dispatched"],
            "page_changed": page_changed,
            "url_changed": url_changed,
            "snapshot_changed": snapshot_changed,
        }
        if page_changed and outcome["status"] == "uncertain":
            outcome["status"] = "succeeded"
            outcome["error"] = None
        elif not page_changed and outcome["status"] == "succeeded":
            outcome["status"] = "uncertain"

    async def _call_tool(
        self,
        browser_session_id: str,
        name: str,
        arguments: dict[str, Any],
        trace_context: dict[str, str] | None = None,
        record_result: bool = True,
    ) -> Any:
        """调用浏览器工具，并将完整调用过程写入 trace。"""
        self._record(
            {
                "type": "tool_call",
                **(trace_context or {}),
                "browser_session_id": browser_session_id,
                "name": name,
                "arguments": arguments,
            }
        )
        try:
            result = await self.browser.call_tool(
                browser_session_id=browser_session_id,
                name=name,
                arguments=arguments,
            )
        except Exception as exc:
            if record_result:
                self._record(
                    {
                        "type": "tool_result",
                        **(trace_context or {}),
                        "browser_session_id": browser_session_id,
                        "name": name,
                        "arguments": arguments,
                        "error_type": type(exc).__name__,
                        "error": str(exc) or type(exc).__name__,
                    }
                )
            raise
        if record_result:
            self._record(
                {
                    "type": "tool_result",
                    **(trace_context or {}),
                    "browser_session_id": browser_session_id,
                    "name": name,
                    "arguments": arguments,
                    "result": result,
                }
            )
        return result

    def _finish(
        self,
        success: bool,
        answer: str,
        token_usage: AgentTokenUsage | None = None,
        status: str | None = None,
    ) -> AgentResult:
        """把执行过程与最终回答写入对话，并清理当前任务上下文。"""
        final_status = status or ("completed" if success else "failed")
        message = {
            "role": "assistant",
            "content": self._assistant_history_content(
                status=final_status,
                answer=answer,
            ),
        }
        self.messages.append(message)
        self._record({"type": "message", **message})
        if token_usage is not None:
            self._record(
                {
                    "type": "token_usage",
                    "usage": token_usage.model_dump(),
                }
            )
        self._clear_current_task_context()
        return AgentResult(
            success=success,
            answer=answer,
            token_usage=token_usage,
        )
