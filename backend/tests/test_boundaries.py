import asyncio
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, Mock, call, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.agent import Agent
from app.browser_process import (
    get_chrome_cdp_candidates,
    get_server_parameters,
    run_agent_browser_cli,
)
from app.llm import AgentLLM
from app.llm_provider import ProviderDecision
from app.mcp_client import BrowserService, ManagedBrowserSession
from app.models import AgentAction, AgentDecision, AgentResult
from app.utils.tools import format_mcp_tools


from tests.support import mcp_tool

class ModuleBoundaryTests(unittest.TestCase):
    def test_test_suites_follow_runtime_domain_boundaries(self):
        from tests import support
        from tests.test_api import AgentApiTests
        from tests.test_boundaries import ToolFormattingTests
        from tests.test_browser_service import BrowserServiceTests

        self.assertTrue(callable(support.completed_decision))
        self.assertEqual(AgentApiTests.__module__, "tests.test_api")
        self.assertEqual(
            BrowserServiceTests.__module__,
            "tests.test_browser_service",
        )
        self.assertEqual(
            ToolFormattingTests.__module__,
            "tests.test_boundaries",
        )

    def test_api_contracts_and_services_have_dedicated_modules(self):
        from app.api import agent as agent_api
        from app.api import browser as browser_api
        from app.api import llm as llm_api
        from app.api.schemas import AgentRunRequest, BrowserSessionStartRequest

        self.assertEqual(AgentRunRequest.__module__, "app.api.schemas")
        self.assertEqual(BrowserSessionStartRequest.__module__, "app.api.schemas")
        self.assertTrue(callable(agent_api.execute_agent))
        self.assertTrue(callable(browser_api.start_browser_session))
        self.assertTrue(callable(llm_api.configure_llm))

    def test_shared_value_and_error_helpers_have_dedicated_modules(self):
        from app.utils.errors import is_transient_error
        from app.utils.values import compact_value, extract_snapshot

        self.assertTrue(is_transient_error(SimpleNamespace(status_code=503)))
        self.assertEqual(
            compact_value(
                {"snapshot": "ignored", "items": ["a", "b"]},
                string_limit=20,
                list_limit=1,
                exclude_keys={"snapshot"},
            ),
            {"items": ["b"]},
        )
        self.assertEqual(
            extract_snapshot({"response": {"snapshot": "page tree"}}),
            "page tree",
        )

    def test_visual_browser_policy_has_a_dedicated_module(self):
        from app.browser.visual import (
            BrowserVisualController,
            VISUAL_CLICK_ACTIONS,
            VISUAL_OVERLAY_CLEANUP_SCRIPT,
            VISUAL_OVERLAY_SCRIPT,
            VISUAL_TARGET_ACTIONS,
        )
        from app.utils.tools import REGISTERED_TOOL_NAMES

        self.assertTrue(VISUAL_CLICK_ACTIONS <= VISUAL_TARGET_ACTIONS)
        self.assertTrue(callable(BrowserVisualController))
        self.assertTrue(VISUAL_TARGET_ACTIONS <= REGISTERED_TOOL_NAMES)
        self.assertIn("browser-agent-visual-layer", VISUAL_OVERLAY_SCRIPT)
        self.assertIn("browser-agent-visual-layer", VISUAL_OVERLAY_CLEANUP_SCRIPT)

    def test_agent_models_live_in_models_module(self):
        from app.models import AgentAction as ModelAgentAction
        from app.models import AgentDecision as ModelAgentDecision
        from app.models import AgentResult, AgentTokenUsage

        self.assertEqual(ModelAgentAction.__module__, "app.models")
        self.assertEqual(ModelAgentDecision.__module__, "app.models")
        self.assertEqual(AgentResult.__module__, "app.models")
        self.assertEqual(AgentTokenUsage.__module__, "app.models")

    def test_agent_composes_trace_recorder(self):
        from app.trace import TraceRecorder

        agent = Agent(
            task="inspect",
            browser=SimpleNamespace(),
            llm=SimpleNamespace(),
        )

        self.assertNotIsInstance(agent, TraceRecorder)
        self.assertIsInstance(agent.tracer, TraceRecorder)
        self.assertIs(agent.trace, agent.tracer.events)
        self.assertIn("_compact_task_value", Agent.__dict__)
        self.assertFalse(hasattr(TraceRecorder, "_compact_task_value"))

    def test_trace_recorder_can_publish_prepared_events(self):
        from app.trace import TraceRecorder

        published = []
        recorder = TraceRecorder(
            None,
            Mock(return_value={}),
            event_sink=published.append,
        )

        recorder.record(
            {
                "type": "tool_call",
                "name": "agent_browser_click",
                "arguments": {"token": "secret", "selector": "@e1"},
            }
        )

        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["arguments"]["token"], "[REDACTED]")
        self.assertIn("timestamp", published[0])

    def test_browser_process_helpers_live_in_browser_process_module(self):
        from app.browser_process import (
            get_agent_browser_env,
            get_server_parameters as process_server_parameters,
            run_agent_browser_cli as process_run_agent_browser_cli,
        )

        self.assertEqual(get_agent_browser_env.__module__, "app.browser_process")
        self.assertEqual(
            process_server_parameters.__module__,
            "app.browser_process",
        )
        self.assertEqual(
            process_run_agent_browser_cli.__module__,
            "app.browser_process",
        )

    def test_runtime_events_have_dedicated_trace_headings(self):
        from app.trace import TraceRecorder

        with TemporaryDirectory() as temp_dir:
            trace_file = Path(temp_dir) / "browser-sessions.md"
            recorder = TraceRecorder(
                trace_file,
                tool_outcome=lambda **kwargs: {},
            )
            recorder.record(
                {
                    "type": "browser_session",
                    "event": "ready",
                    "browser_session_id": "test",
                    "runtime_session_id": "browser-agent-runtime",
                    "status": "ready",
                    "page_count": 1,
                }
            )
            recorder.record(
                {
                    "type": "llm_attempt",
                    "endpoint_id": "openai",
                    "model": "gpt-test",
                    "attempt": 1,
                    "status": "succeeded",
                    "duration_ms": 100,
                }
            )

            trace_text = trace_file.read_text(encoding="utf-8")

        self.assertIn("## LLM 尝试", trace_text)
        self.assertIn("## 浏览器会话", trace_text)
        self.assertNotIn("## 错误", trace_text)



class ToolFormattingTests(unittest.TestCase):
    def test_all_tools_are_formatted_as_compact_signatures(self):
        tools = [
            SimpleNamespace(
                name="agent_browser_click",
                description="Click an element.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "Element @ref or CSS selector.",
                        },
                        "newTab": {"type": "boolean"},
                        "session": {"type": "string"},
                        "timeoutMs": {"type": "integer"},
                    },
                    "required": ["selector"],
                },
            ),
            mcp_tool("agent_browser_custom_tool"),
        ]

        descriptions = format_mcp_tools(tools)

        self.assertIsInstance(descriptions, str)
        self.assertIn(
            "agent_browser_click(selector:string{Element @ref or CSS selector.}; "
            "newTab?:boolean): Click an element.",
            descriptions,
        )
        self.assertIn("agent_browser_custom_tool()", descriptions)
        self.assertNotIn("session", descriptions)
        self.assertNotIn("timeoutMs", descriptions)
        self.assertNotIn('"type": "function"', descriptions)

    def test_common_tools_and_static_getters_are_always_visible(self):
        from app.utils.tools import (
            COMMON_TOOL_NAMES,
            INTERNAL_TOOL_NAMES,
            REGISTERED_TOOL_NAMES,
            TOOL_GROUPS,
            get_tool_group,
            select_mcp_tools_for_llm,
        )

        tools = [
            mcp_tool("agent_browser_snapshot"),
            mcp_tool("agent_browser_open"),
            mcp_tool("agent_browser_read"),
            mcp_tool("agent_browser_click"),
            mcp_tool("agent_browser_wait_for_text"),
            mcp_tool("agent_browser_network_route"),
            mcp_tool("agent_browser_network_requests"),
            mcp_tool("agent_browser_react_tree"),
            mcp_tool("agent_browser_eval"),
            mcp_tool("agent_browser_a11y"),
            mcp_tool("agent_browser_frame_switch"),
            mcp_tool("agent_browser_vitals"),
            mcp_tool("agent_browser_auth_list"),
            mcp_tool("agent_browser_set_viewport"),
        ]

        visible = select_mcp_tools_for_llm(tools)
        visible_names = {tool.name for tool in visible}

        self.assertIn("agent_browser_open", visible_names)
        self.assertIn("agent_browser_read", visible_names)
        self.assertIn("agent_browser_click", visible_names)
        self.assertIn("agent_browser_wait_for_text", visible_names)
        self.assertIn("agent_tools_get_network", visible_names)
        self.assertIn("agent_tools_get_debug", visible_names)
        self.assertIn("agent_tools_get_react", visible_names)
        self.assertNotIn("agent_browser_network_requests", visible_names)
        self.assertNotIn("agent_browser_react_tree", visible_names)
        self.assertNotIn("agent_browser_eval", visible_names)
        self.assertNotIn("agent_browser_snapshot", visible_names)

        network_tools = get_tool_group(
            tools,
            "agent_tools_get_network",
        )
        self.assertEqual(
            [item["name"] for item in network_tools],
            [
                "agent_browser_network_route",
                "agent_browser_network_requests",
            ],
        )
        self.assertTrue(
            all("description" in item for item in network_tools)
        )
        self.assertEqual(
            TOOL_GROUPS["agent_tools_get_network"]["tools"],
            (
                "agent_browser_network_route",
                "agent_browser_network_unroute",
                "agent_browser_network_requests",
                "agent_browser_network_request",
                "agent_browser_network_har_start",
                "agent_browser_network_har_stop",
            ),
        )

        grouped_names = [
            name
            for group in TOOL_GROUPS.values()
            for name in group["tools"]
        ]
        self.assertEqual(len(grouped_names), len(set(grouped_names)))
        self.assertTrue(COMMON_TOOL_NAMES.isdisjoint(grouped_names))
        self.assertTrue(INTERNAL_TOOL_NAMES.isdisjoint(REGISTERED_TOOL_NAMES))
        self.assertEqual(
            len(INTERNAL_TOOL_NAMES | REGISTERED_TOOL_NAMES),
            152,
        )
        self.assertEqual(
            REGISTERED_TOOL_NAMES,
            COMMON_TOOL_NAMES | frozenset(grouped_names),
        )
