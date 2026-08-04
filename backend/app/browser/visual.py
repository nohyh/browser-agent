"""浏览器操作的可视化提示策略与页面脚本。"""

import asyncio
from typing import Any, Callable, Protocol

from app.models import AgentAction
from app.utils.errors import exception_details

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
VISUAL_OVERLAY_CLEANUP_SCRIPT = r"""
(() => {
  const host = document.getElementById('browser-agent-visual-layer');
  if (host) host.remove();
  delete window.__browserAgentVisual;
  return true;
})()
"""


class VisualBrowser(Protocol):
    tools: list[Any]

    async def call_tool(
        self,
        browser_session_id: str,
        name: str,
        arguments: dict[str, Any],
    ) -> Any: ...


class BrowserVisualController:
    """管理可视化层及其内部工具调用，不让辅助失败打断主任务。"""

    def __init__(
        self,
        browser: VisualBrowser,
        record: Callable[[dict[str, Any]], None],
    ):
        self.browser = browser
        self.record = record
        self.started = False

    def reset(self) -> None:
        self.started = False

    async def prepare(
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
            or not self._has_tool("agent_browser_eval")
        ):
            return
        installed = await self._eval(
            browser_session_id,
            VISUAL_OVERLAY_SCRIPT,
            purpose="visual_overlay_install",
            trace_context=trace_context,
        )
        if not installed:
            return
        self.started = True

        if not self._has_tool("agent_browser_get_box"):
            return
        box_succeeded, raw_box = await self._call_tool(
            browser_session_id,
            "agent_browser_get_box",
            {"selector": selector},
            purpose="visual_target_box",
            trace_arguments={"selector": selector},
            trace_context=trace_context,
        )
        if not box_succeeded:
            return
        box = extract_visual_box(raw_box)
        if box is None:
            return
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        await self._eval(
            browser_session_id,
            visual_pointer_script(
                x,
                y,
                action.name in VISUAL_CLICK_ACTIONS,
            ),
            purpose="visual_pointer_move",
            trace_context=trace_context,
        )

    async def remove(
        self,
        browser_session_id: str,
        trace_context: dict[str, str] | None = None,
    ) -> None:
        """任务正常、失败或异常结束时都移除注入的显示层。"""
        await self._eval(
            browser_session_id,
            VISUAL_OVERLAY_CLEANUP_SCRIPT,
            purpose="visual_overlay_cleanup",
            trace_context=trace_context,
        )
        self.started = False

    async def _eval(
        self,
        browser_session_id: str,
        script: str,
        *,
        purpose: str,
        trace_context: dict[str, str] | None = None,
    ) -> bool:
        succeeded, _ = await self._call_tool(
            browser_session_id,
            "agent_browser_eval",
            {"script": script},
            purpose=purpose,
            trace_arguments={"script_characters": len(script)},
            trace_context=trace_context,
        )
        return succeeded

    async def _call_tool(
        self,
        browser_session_id: str,
        name: str,
        arguments: dict[str, Any],
        *,
        purpose: str,
        trace_arguments: dict[str, Any],
        trace_context: dict[str, str] | None = None,
    ) -> tuple[bool, Any]:
        """统一记录内部调用，让辅助失败保持可观测。"""
        context = dict(trace_context or {})
        parent_action_id = context.get("action_id")
        if parent_action_id:
            context["action_id"] = f"{parent_action_id}:internal:{purpose}"
        context["browser_session_id"] = browser_session_id
        summary = {"internal": True, "purpose": purpose, **trace_arguments}
        self.record(
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
            error = exception_details(exc)
        succeeded = error is None
        self.record(
            {
                "type": "tool_result",
                **context,
                "name": name,
                "arguments": summary,
                "status": "succeeded" if succeeded else "failed",
                "data": {"duration_ms": round((loop.time() - started) * 1000)},
                "error": error,
                "effect": {"dispatched": succeeded, "page_changed": None},
            }
        )
        return succeeded, result

    def _has_tool(self, name: str) -> bool:
        return any(tool.name == name for tool in self.browser.tools)


def extract_visual_box(value: Any) -> dict[str, float] | None:
    """从可能带多层 envelope 的工具结果中提取坐标框。"""
    if isinstance(value, dict):
        keys = ("x", "y", "width", "height")
        if all(
            isinstance(value.get(key), (int, float))
            and not isinstance(value.get(key), bool)
            for key in keys
        ):
            return {key: float(value[key]) for key in keys}
        for nested in value.values():
            box = extract_visual_box(nested)
            if box is not None:
                return box
    return None


def visual_pointer_script(x: float, y: float, clicking: bool) -> str:
    click_literal = "true" if clicking else "false"
    return f"""
(() => {{
  const visual = window.__browserAgentVisual;
  if (!visual || !visual.cursor) return false;
  visual.move({x:.1f}, {y:.1f}, {click_literal});
  return true;
}})()
"""
