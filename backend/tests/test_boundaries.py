import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock


from app.agent import Agent
from app.models import (
    AgentResult,
    MutationIntent,
    ToolBehavior,
)
from app.utils.errors import ERROR_CODES, ToolValidationError, exception_details
from app.utils.tools import (
    format_mcp_tools,
    get_tool_behavior,
    validate_tool_arguments,
)


from tests.support import mcp_tool, mcp_tool_v2

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
        from app.models import AgentTokenUsage

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

    def test_trace_recorder_writes_versioned_jsonl_with_linkage_and_redaction(self):
        from app.trace import TraceRecorder

        with TemporaryDirectory() as temp_dir:
            trace_file = Path(temp_dir) / "conversation-1.md"
            recorder = TraceRecorder(
                trace_file,
                conversation_id="conversation-1",
            )
            recorder.record(
                {
                    "type": "tool_call",
                    "run_id": "run-1",
                    "step_id": "run-1:step-1",
                    "action_id": "run-1:step-1:action-1",
                    "observation_id": "run-1:step-1:observation",
                    "name": "agent_browser_fill",
                    "arguments": {
                        "selector": "@e1",
                        "value": "sk-test-secret-value-123456",
                        "url": "https://example.test/reset#access_token=fragment-secret",
                    },
                    "duration_ms": 12,
                }
            )
            recorder.record(
                {
                    "type": "tool_result",
                    "run_id": "run-1",
                    "step_id": "run-1:step-1",
                    "action_id": "run-1:step-1:action-1",
                    "observation_id": "run-1:step-1:observation-2",
                    "name": "agent_browser_snapshot",
                    "status": "succeeded",
                    "data": {
                        "url": "https://example.test/page#secret-fragment",
                        "snapshot": "same page snapshot",
                    },
                    "effect": {"page_changed": False},
                }
            )

            jsonl_file = trace_file.with_suffix(".jsonl")
            records = [
                json.loads(line)
                for line in jsonl_file.read_text(encoding="utf-8").splitlines()
            ]
            markdown = trace_file.read_text(encoding="utf-8")

        self.assertEqual([record["sequence"] for record in records], [1, 2])
        self.assertTrue(all(record["schema_version"] == 1 for record in records))
        self.assertTrue(
            all(record["conversation_id"] == "conversation-1" for record in records)
        )
        self.assertEqual(records[0]["phase"], "tool_dispatch")
        self.assertEqual(records[0]["duration_ms"], 12)
        serialized = json.dumps(records, ensure_ascii=False)
        self.assertNotIn("sk-test-secret-value-123456", serialized)
        self.assertNotIn("fragment-secret", serialized)
        self.assertNotIn("secret-fragment", serialized)
        self.assertIn("## 工具调用：agent_browser_fill", markdown)

    def test_mutation_and_history_arguments_use_tool_aware_redaction(self):
        from app.trace import TraceRecorder

        with TemporaryDirectory() as temp_dir:
            trace_file = Path(temp_dir) / "conversation-1.md"
            recorder = TraceRecorder(trace_file)
            recorder.record(
                {
                    "type": "mutation_intent",
                    "tool_name": "agent_browser_fill",
                    "arguments": {
                        "selector": "@e1",
                        "text": "private form value",
                    },
                    "status": "prepared",
                }
            )
            serialized = trace_file.with_suffix(".jsonl").read_text(
                encoding="utf-8"
            )
            markdown = trace_file.read_text(encoding="utf-8")

        self.assertNotIn("private form value", serialized)
        self.assertNotIn("private form value", markdown)
        self.assertIn("[REDACTED]", serialized)

    def test_trace_recorder_enforces_capacity_and_retention(self):
        from app.trace import TraceRecorder, cleanup_trace_directory

        with TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            trace_file = directory / "conversation-1.md"
            recorder = TraceRecorder(trace_file, max_bytes=512)
            recorder.record(
                {
                    "type": "message",
                    "role": "user",
                    "content": "x" * 10_000,
                }
            )
            old_file = directory / "old.jsonl"
            old_file.write_text("old", encoding="utf-8")
            old_file.touch()
            old_timestamp = datetime.now().timestamp() - 10 * 86400
            import os

            os.utime(old_file, (old_timestamp, old_timestamp))

            cleanup_trace_directory(directory, retention_days=1)

            self.assertLessEqual(trace_file.stat().st_size, 512)
            self.assertLessEqual(trace_file.with_suffix(".jsonl").stat().st_size, 512)
            self.assertFalse(old_file.exists())

    def test_quality_configuration_declares_pinned_runtime_and_checks(self):
        root = Path(__file__).parents[2]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        pyright = (root / "pyrightconfig.json").read_text(encoding="utf-8")

        self.assertIn('requires-python = ">=3.12,<3.13"', pyproject)
        self.assertIn('"ruff==', pyproject)
        self.assertIn('"pyright==', pyproject)
        self.assertIn('"pytest-timeout==', pyproject)
        self.assertIn('typeCheckingMode": "basic"', pyright)

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
            recorder.record(
                {
                    "type": "browser_transport",
                    "event": "tool_failed",
                    "tool_name": "agent_browser_open",
                    "phase": "mcp_response",
                    "status": "timed_out",
                    "duration_ms": 30_000,
                }
            )
            recorder.record(
                {
                    "type": "mutation_intent",
                    "mutation_id": "mutation-1",
                    "action_id": "run-1:step-1:action-1",
                    "tool_name": "agent_browser_click",
                    "arguments": {"selector": "@e1"},
                    "status": "prepared",
                }
            )
            recorder.record(
                {
                    "type": "step_failure",
                    "stage": "completion",
                    "code": "pending_mutation",
                    "retryable": True,
                    "message": "pending mutation",
                }
            )

            trace_text = trace_file.read_text(encoding="utf-8")

        self.assertIn("## LLM 尝试", trace_text)
        self.assertIn("## 浏览器会话", trace_text)
        self.assertIn("## 浏览器传输", trace_text)
        self.assertIn("## 写操作意图", trace_text)
        self.assertIn("## 步骤失败", trace_text)
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


class ToolBehaviorTests(unittest.TestCase):
    def test_tools_have_conservative_behavior_classes(self):
        self.assertEqual(
            get_tool_behavior("agent_browser_get_title").category,
            "read_only",
        )
        self.assertEqual(
            get_tool_behavior("agent_browser_open").category,
            "navigation",
        )
        self.assertEqual(
            get_tool_behavior("agent_browser_click").category,
            "potential_write",
        )
        self.assertTrue(get_tool_behavior("unknown_tool").potential_write)

    def test_cached_mcp_annotations_override_unknown_tool_defaults(self):
        read_only_tool = SimpleNamespace(
            annotations={"readOnlyHint": True},
        )
        navigation_tool = SimpleNamespace(
            annotations={"destructiveHint": False},
        )

        self.assertEqual(
            get_tool_behavior("custom_read", read_only_tool).category,
            "read_only",
        )
        self.assertEqual(
            get_tool_behavior("custom_navigation", navigation_tool).category,
            "potential_write",
        )

    def test_mcp_v2_snake_case_annotations_override_unknown_tool_defaults(self):
        """真实 MCP 2.0 的 Tool 使用 read_only_hint（snake_case）。"""
        read_only_tool = SimpleNamespace(
            annotations=SimpleNamespace(read_only_hint=True),
        )

        self.assertEqual(
            get_tool_behavior("custom_read", read_only_tool).category,
            "read_only",
        )

    def test_mcp_v2_snake_case_schema_blocks_invalid_arguments(self):
        """MCP 2.0 工具用 input_schema 承载参数定义，校验必须仍然生效。"""
        fill_tool = mcp_tool_v2(
            "agent_browser_fill",
            properties={
                "selector": {"type": "string"},
                "text": {"type": "string"},
            },
            required=["selector", "text"],
        )
        with self.assertRaises(ToolValidationError) as context:
            validate_tool_arguments(
                "agent_browser_fill",
                {"selector": "@e122", "value": "hello"},
                [fill_tool],
            )
        self.assertEqual(context.exception.code, "invalid_tool_arguments")
        self.assertEqual(
            context.exception.details["missing"],
            ["text"],
        )
        self.assertEqual(
            context.exception.details["unknown"],
            ["value"],
        )

    def test_mcp_v2_snake_case_schema_is_formatted_with_parameters(self):
        """工具签名必须包含参数，模型才能正确构造调用。"""
        fill_tool = mcp_tool_v2(
            "agent_browser_fill",
            properties={
                "selector": {"type": "string"},
                "text": {"type": "string"},
            },
            required=["selector", "text"],
        )

        descriptions = format_mcp_tools([fill_tool])

        self.assertIn(
            "agent_browser_fill(selector:string; text:string)",
            descriptions,
        )

    def test_input_schema_validation_covers_nested_values(self):
        tool = SimpleNamespace(
            name="agent_browser_custom_tool",
            inputSchema={
                "type": "object",
                "properties": {
                    "options": {
                        "type": "object",
                        "properties": {
                            "mode": {"enum": ["fast", "safe"]},
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["mode"],
                        "additionalProperties": False,
                    },
                },
                "required": ["options"],
            },
        )

        with self.assertRaises(ValueError) as context:
            validate_tool_arguments(
                tool.name,
                {"options": {"mode": "slow", "tags": ["ok", 1]}},
                [tool],
            )

        details = context.exception.details
        self.assertIn("options.mode", {
            item["field"] for item in details["invalid_enums"]
        })
        self.assertIn("options.tags[1]", {
            item["field"] for item in details["invalid_types"]
        })

    def test_error_code_registry_includes_public_runtime_and_completion_codes(self):
        self.assertTrue(
            {
                "runtime_not_ready",
                "browser_session_not_ready",
                "pending_mutation",
                "action_observation_required",
                "repeated_action",
            }
            <= ERROR_CODES
        )

    def test_mutation_intent_has_explicit_lifecycle(self):
        intent = MutationIntent(
            action_id="run-1:action-1",
            tool_name="agent_browser_click",
            arguments={"selector": "@e1"},
            status="prepared",
        )
        self.assertEqual(intent.status, "prepared")
        self.assertIsInstance(
            ToolBehavior(name="agent_browser_click").model_dump(),
            dict,
        )

    def test_provider_contract_accepts_name_type_and_object_arguments(self):
        from app.llm_provider import OpenAIResponsesAdapter

        decision = OpenAIResponsesAdapter._to_agent_decision(
            {
                "status": "continue",
                "evaluation_previous_goal": "需要继续。",
                "memory": "当前页面有目标。",
                "next_goal": "点击目标。",
                "actions": [
                    {
                        "type": "agent_browser_click",
                        "arguments": {"selector": "@e1"},
                    }
                ],
            }
        )

        self.assertEqual(decision.actions[0].name, "agent_browser_click")
        self.assertEqual(
            decision.actions[0].arguments,
            {"selector": "@e1"},
        )

    def test_provider_contract_preserves_observation_binding_metadata(self):
        from app.llm_provider import OpenAIResponsesAdapter

        decision = OpenAIResponsesAdapter._to_agent_decision(
            {
                "status": "continue",
                "evaluation_previous_goal": "需要继续。",
                "memory": "当前页面有目标。",
                "next_goal": "点击目标。",
                "actions": [
                    {
                        "name": "agent_browser_click",
                        "arguments": '{"selector":"@e1"}',
                        "observation_id": "observation-1",
                        "observation_revision": 7,
                    }
                ],
            }
        )

        self.assertEqual(decision.actions[0].observation_id, "observation-1")
        self.assertEqual(decision.actions[0].observation_revision, 7)

    def test_error_details_expose_stable_code(self):
        from app.mcp_client import ToolValidationError

        details = exception_details(
            ToolValidationError(
                "agent_browser_click",
                {"selector": "bad"},
                {"missing": ["selector"]},
            )
        )
        self.assertEqual(details["code"], "invalid_tool_arguments")

    def test_token_estimator_reports_named_input_slots(self):
        from app.llm import TokenEstimator

        estimator = TokenEstimator(chars_per_token=4)
        slots = estimator.slot_metrics(
            system="system prompt",
            history="history",
            observation="page observation",
            tool_result="tool result",
        )

        self.assertEqual(
            set(slots),
            {"system", "history", "observation", "tool_result"},
        )
        self.assertGreater(slots["observation"]["estimated_tokens"], 0)
        self.assertIn("budget_tokens", slots["tool_result"])
