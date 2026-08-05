import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.llm import AgentLLM
from app.models import AgentResult


from tests.support import (
    FakeBrowser,
    FakeOpenAIClient,
    completed_decision,
)

class AgentApiTests(unittest.IsolatedAsyncioTestCase):
    def test_stream_event_buffer_is_bounded_and_merges_duplicate_progress(self):
        from app.api.agent import BoundedTraceQueue

        queue = BoundedTraceQueue(maxsize=2)
        event = {
            "type": "trace",
            "event": {
                "kind": "thinking",
                "status": "running",
                "title": "正在分析页面并规划下一步",
            },
        }
        queue.publish(event)
        queue.publish(event.copy())
        queue.publish({"type": "trace", "event": {"kind": "usage"}})
        queue.publish({"type": "trace", "event": {"kind": "error"}})

        self.assertLessEqual(queue.qsize(), 2)
        self.assertEqual(queue.merged_count, 1)

    async def test_health_endpoints_separate_process_liveness_and_runtime_readiness(self):
        import main

        live = await main.health_live()
        self.assertEqual(live, {"status": "ok"})

        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    runtime_supervisor=SimpleNamespace(
                        snapshot=Mock(
                            return_value={
                                "status": "degraded",
                                "ready": False,
                                "last_error": "MCP unavailable",
                            }
                        )
                    )
                )
            )
        )
        with self.assertRaises(HTTPException) as context:
            await main.health_ready(request)

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(
            context.exception.detail["code"],
            "runtime_not_ready",
        )

    async def test_lifespan_closes_browsers_before_mcp_and_waits_for_llm_config(
        self,
    ):
        import main

        class AsyncContext:
            def __init__(self, value, name):
                self.value = value
                self.name = name

            async def __aenter__(self):
                events.append(f"{self.name}_enter")
                return self.value

            async def __aexit__(self, exc_type, exc, traceback):
                events.append(f"{self.name}_exit")
                return False

        events = []
        mcp_session = SimpleNamespace(initialize=AsyncMock())
        browser = SimpleNamespace(
            cleanup_orphaned_sessions=AsyncMock(
                side_effect=lambda: events.append("orphan_cleanup")
            ),
            cache_tools=AsyncMock(
                side_effect=lambda: events.append("cache_tools")
            ),
            close_all_sessions=AsyncMock(
                side_effect=lambda: events.append("browser_close") or {}
            ),
        )
        test_app = SimpleNamespace(state=SimpleNamespace())

        with (
            patch.object(main, "load_dotenv"),
            patch.object(main.os, "getenv", return_value=None),
            patch.object(main, "get_server_parameters", return_value="params"),
            patch.object(
                main,
                "stdio_client",
                return_value=AsyncContext(("read", "write"), "stdio"),
            ),
            patch.object(
                main,
                "ClientSession",
                return_value=AsyncContext(mcp_session, "mcp"),
            ),
            patch.object(main, "BrowserService", return_value=browser),
            patch.object(main, "AsyncOpenAI") as client_factory,
        ):
            async with main.lifespan(test_app):
                events.append("yield")
                self.assertIsNone(test_app.state.agent_llm)
                self.assertIsNone(test_app.state.openai_client)
                await test_app.state.runtime_start_task
                self.assertIs(test_app.state.browser_service, browser)

        client_factory.assert_not_called()
        mcp_session.initialize.assert_awaited_once()
        browser.cleanup_orphaned_sessions.assert_awaited_once()
        browser.cache_tools.assert_awaited_once()
        browser.close_all_sessions.assert_awaited_once()
        self.assertLess(events.index("browser_close"), events.index("mcp_exit"))

    async def test_llm_config_replaces_runtime_client_without_returning_api_key(self):
        import main

        old_client = SimpleNamespace(close=AsyncMock())
        old_llm = SimpleNamespace(client=old_client, model="old-model")
        existing_agent = SimpleNamespace(llm=old_llm)
        state = SimpleNamespace(
            agents={"conversation-1": existing_agent},
            agent_llm=old_llm,
            openai_client=old_client,
        )
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        new_client = SimpleNamespace(close=AsyncMock())

        with patch.object(
            main,
            "AsyncOpenAI",
            return_value=new_client,
        ) as client_factory:
            result = await main.configure_llm(
                main.LLMConfigRequest(
                    api_url="https://gateway.example.com/v1/",
                    api_key="secret-key",
                    model="gpt-5-mini",
                ),
                request,
            )

        client_factory.assert_called_once_with(
            api_key="secret-key",
            base_url="https://gateway.example.com/v1",
        )
        self.assertTrue(result.configured)
        self.assertEqual(result.api_url, "https://gateway.example.com/v1")
        self.assertEqual(result.model, "gpt-5-mini")
        self.assertNotIn("api_key", result.model_dump())
        self.assertIs(state.agent_llm.client, new_client)
        self.assertIs(existing_agent.llm, state.agent_llm)
        old_client.close.assert_awaited_once()

    async def test_identical_llm_config_reuses_runtime_client(self):
        import main

        payload = main.LLMConfigRequest(
            api_url="https://gateway.example.com/v1",
            api_key="secret-key",
            model="gpt-5-mini",
        )
        current_client = SimpleNamespace(close=AsyncMock())
        current_llm = AgentLLM(current_client, model="gpt-5-mini")
        state = SimpleNamespace(
            agents={},
            agent_llm=current_llm,
            openai_client=current_client,
            llm_config_fingerprint=main.llm_config_fingerprint(payload),
        )
        request = SimpleNamespace(app=SimpleNamespace(state=state))

        with patch.object(main, "AsyncOpenAI") as client_factory:
            result = await main.configure_llm(payload, request)

        self.assertTrue(result.configured)
        self.assertIs(state.agent_llm, current_llm)
        client_factory.assert_not_called()
        current_client.close.assert_not_awaited()

    async def test_llm_model_discovery_returns_sorted_unique_model_ids(self):
        import main

        discovered_client = SimpleNamespace(
            models=SimpleNamespace(
                list=AsyncMock(
                    return_value=SimpleNamespace(
                        data=[
                            SimpleNamespace(id="deepseek-pro"),
                            SimpleNamespace(id="deepseek-flash"),
                            SimpleNamespace(id="deepseek-pro"),
                        ]
                    )
                )
            ),
            close=AsyncMock(),
        )

        with patch.object(
            main,
            "AsyncOpenAI",
            return_value=discovered_client,
        ) as client_factory:
            result = await main.discover_llm_models(
                main.LLMModelDiscoveryRequest(
                    api_url="https://gateway.example.com/v1/",
                    api_key="secret-key",
                )
            )

        client_factory.assert_called_once_with(
            api_key="secret-key",
            base_url="https://gateway.example.com/v1",
        )
        discovered_client.models.list.assert_awaited_once()
        discovered_client.close.assert_awaited_once()
        self.assertEqual(
            result.models,
            ["deepseek-flash", "deepseek-pro"],
        )
        self.assertNotIn("api_key", result.model_dump())

    async def test_multiple_endpoints_share_clients_and_resolve_models(self):
        import main

        clients = [
            SimpleNamespace(close=AsyncMock()),
            SimpleNamespace(close=AsyncMock()),
        ]
        state = SimpleNamespace(agents={})
        request = SimpleNamespace(app=SimpleNamespace(state=state))

        with patch.object(
            main,
            "AsyncOpenAI",
            side_effect=clients,
        ) as client_factory:
            result = await main.configure_llm_endpoints(
                main.LLMEndpointsConfigRequest(
                    endpoints=[
                        {
                            "id": "deepseek",
                            "name": "DeepSeek",
                            "api_url": "https://deepseek.example.com/v1",
                            "api_key": "deepseek-key",
                            "models": ["deepseek-flash", "deepseek-pro"],
                        },
                        {
                            "id": "local",
                            "name": "本地模型",
                            "api_url": "http://127.0.0.1:4000/v1",
                            "api_key": "local-key",
                            "models": ["qwen-fast"],
                        },
                    ]
                ),
                request,
            )

        self.assertEqual(client_factory.call_count, 2)
        self.assertEqual(len(result.endpoints), 2)
        self.assertNotIn("api_key", str(result.model_dump()))
        flash = state.llm_registry.resolve("deepseek", "deepseek-flash")
        pro = state.llm_registry.resolve("deepseek", "deepseek-pro")
        local = state.llm_registry.resolve("local", "qwen-fast")
        self.assertIs(flash.client, pro.client)
        self.assertIsNot(flash.client, local.client)
        self.assertEqual(flash.model, "deepseek-flash")
        self.assertEqual(pro.model, "deepseek-pro")
        self.assertEqual(flash.endpoint_id, "deepseek")
        self.assertEqual(local.endpoint_id, "local")

    async def test_conversation_can_switch_model_without_mutating_other_agents(self):
        import main

        flash = AgentLLM(FakeOpenAIClient([]), model="deepseek-flash")
        pro = AgentLLM(FakeOpenAIClient([]), model="deepseek-pro")
        registry = SimpleNamespace(
            resolve=Mock(side_effect=lambda endpoint_id, model: {
                ("deepseek", "deepseek-flash"): flash,
                ("deepseek", "deepseek-pro"): pro,
            }[(endpoint_id, model)])
        )
        first_agent = SimpleNamespace(
            llm=flash,
            add_user_message=Mock(),
            run=AsyncMock(return_value=AgentResult(success=True, answer="ok")),
        )
        other_agent = SimpleNamespace(llm=flash)
        browser = FakeBrowser()
        browser.ready_sessions.add("browser-session-1")
        state = SimpleNamespace(
            agents={"conversation-1": first_agent, "conversation-2": other_agent},
            llm_registry=registry,
            agent_llm=flash,
        )
        request = SimpleNamespace(app=SimpleNamespace(state=state))

        result = await main.run_agent(
            main.AgentRunRequest(
                message="继续任务",
                conversation_id="conversation-1",
                browser_session_id="browser-session-1",
                llm_endpoint_id="deepseek",
                llm_model="deepseek-pro",
            ),
            request,
            browser,
        )

        self.assertTrue(result.success)
        self.assertIs(first_agent.llm, pro)
        self.assertIs(other_agent.llm, flash)
        registry.resolve.assert_called_once_with("deepseek", "deepseek-pro")

    async def test_concurrent_turns_in_same_conversation_are_serialized(self):
        import main

        entered = 0
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def run(_browser_session_id):
            nonlocal entered
            entered += 1
            if entered == 1:
                first_entered.set()
                await release_first.wait()
            return AgentResult(success=True, answer="ok")

        agent = SimpleNamespace(
            llm=None,
            tracer=SimpleNamespace(event_sink=None),
            add_user_message=Mock(),
            run=run,
        )
        llm = AgentLLM(FakeOpenAIClient([]), model="test-model")
        browser = FakeBrowser()
        browser.ready_sessions.add("browser-session-1")
        state = SimpleNamespace(
            agents={"conversation-1": agent},
            agent_llm=llm,
            agent_locks={},
        )
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        payload = main.AgentRunRequest(
            message="continue",
            conversation_id="conversation-1",
            browser_session_id="browser-session-1",
        )

        first = asyncio.create_task(main.run_agent(payload, request, browser))
        await first_entered.wait()
        second = asyncio.create_task(main.run_agent(payload, request, browser))
        await asyncio.sleep(0)

        self.assertEqual(entered, 1)
        release_first.set()
        await asyncio.gather(first, second)
        self.assertEqual(entered, 2)

    def test_llm_config_rejects_invalid_or_empty_values(self):
        import main

        invalid_payloads = [
            {
                "api_url": "ftp://gateway.example.com/v1",
                "api_key": "secret-key",
                "model": "gpt-5-mini",
            },
            {
                "api_url": "https://gateway.example.com/v1",
                "api_key": "   ",
                "model": "gpt-5-mini",
            },
            {
                "api_url": "https://gateway.example.com/v1",
                "api_key": "secret-key",
                "model": "   ",
            },
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(
                ValidationError
            ):
                main.LLMConfigRequest(**payload)

    async def test_page_suggestions_use_one_lightweight_llm_call(self):
        import main

        openai_client = FakeOpenAIClient(
            [],
            summaries=[
                '["总结当前页面", "提取关键步骤", "整理成操作清单"]'
            ],
        )
        state = SimpleNamespace(
            agent_llm=AgentLLM(openai_client, model="test-model"),
        )
        request = SimpleNamespace(app=SimpleNamespace(state=state))

        result = await main.generate_page_suggestions(
            main.PageSuggestionsRequest(
                url="https://example.com/guide",
                title="使用指南",
                content="这是一篇介绍浏览器自动化工作流的使用指南。",
            ),
            request,
        )

        self.assertEqual(
            result.suggestions,
            ["总结当前页面", "提取关键步骤", "整理成操作清单"],
        )
        self.assertEqual(len(openai_client.responses.create_calls), 1)
        call = openai_client.responses.create_calls[0]
        self.assertEqual(call["model"], "test-model")
        self.assertIn(
            "BEGIN_UNTRUSTED_PAGE_CONTEXT",
            str(call["input"]),
        )

    async def test_agent_run_requires_an_initialized_llm(self):
        import main

        browser = FakeBrowser()
        browser.refresh_session_ready = AsyncMock(return_value=True)
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(agents={}, agent_llm=None)
            )
        )

        with self.assertRaises(HTTPException) as context:
            await main.run_agent(
                main.AgentRunRequest(
                    message="打开 example.com",
                    conversation_id="conversation-1",
                    browser_session_id="test",
                ),
                request,
                browser,
            )

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(
            context.exception.detail["code"],
            "llm_not_configured",
        )
        browser.refresh_session_ready.assert_not_awaited()

    async def test_session_must_be_started_before_running_agent(self):
        import main

        browser = FakeBrowser()
        browser.refresh_session_ready = AsyncMock(return_value=False)
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    agents={},
                    agent_llm=AgentLLM(
                        FakeOpenAIClient([]),
                        model="test-model",
                    ),
                )
            )
        )

        with self.assertRaises(HTTPException) as context:
            await main.run_agent(
                main.AgentRunRequest(
                    message="打开 x.com",
                    conversation_id="conversation-1",
                    browser_session_id="test",
                ),
                request,
                browser,
            )

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(
            context.exception.detail["code"],
            "browser_session_not_ready",
        )
        self.assertEqual(
            context.exception.detail["browser_session_id"],
            "test",
        )
        self.assertEqual(context.exception.detail["status"], "missing")
        browser.refresh_session_ready.assert_awaited_once_with("test")

    async def test_closed_browser_is_rejected_before_agent_loop(self):
        import main

        browser = FakeBrowser()
        await browser.start_session("test1")
        browser.refresh_session_ready = AsyncMock(return_value=False)
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    agents={},
                    agent_llm=AgentLLM(
                        FakeOpenAIClient([]),
                        model="test-model",
                    ),
                )
            )
        )

        with self.assertRaises(HTTPException) as context:
            await main.run_agent(
                main.AgentRunRequest(
                    message="打开 leetcode",
                    conversation_id="conversation-2",
                    browser_session_id="test1",
                ),
                request,
                browser,
            )

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(
            context.exception.detail["browser_session_id"],
            "test1",
        )
        self.assertEqual(browser.calls, [])

    async def test_session_start_endpoint_marks_session_ready(self):
        import main

        browser = FakeBrowser()

        result = await main.start_browser_session(
            main.BrowserSessionStartRequest(
                browser_session_id="test",
            ),
            browser,
        )

        self.assertTrue(result.ready)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.ownership, "backend")
        self.assertEqual(result.browser_session_id, "test")
        self.assertEqual(result.mode, "isolated")
        self.assertEqual(result.url, "about:blank")
        self.assertTrue(browser.is_session_ready("test"))

    def test_existing_session_requires_explicit_cdp_address(self):
        import main

        with self.assertRaises(ValidationError):
            main.BrowserSessionStartRequest(
                browser_session_id="work-chrome",
                mode="existing",
            )

    def test_current_session_url_is_optional_and_auto_mode_is_rejected(self):
        import main

        request = main.BrowserSessionStartRequest(
            browser_session_id="current-chrome",
            mode="current",
        )
        self.assertIsNone(request.expected_url)
        request_with_url = main.BrowserSessionStartRequest(
            browser_session_id="current-chrome-url",
            mode="current",
            expected_url=" https://example.com/current/ ",
        )
        self.assertEqual(
            request_with_url.expected_url,
            "https://example.com/current/",
        )
        with self.assertRaises(ValidationError):
            main.BrowserSessionStartRequest(
                browser_session_id="legacy-auto",
                mode="auto",
                expected_url="https://example.com/current",
            )

    async def test_sessions_can_be_listed_queried_and_closed(self):
        import main

        browser = FakeBrowser()
        await main.start_browser_session(
            main.BrowserSessionStartRequest(
                browser_session_id="first",
            ),
            browser,
        )
        await main.start_browser_session(
            main.BrowserSessionStartRequest(
                browser_session_id="work-chrome",
                mode="existing",
                cdp_url="http://127.0.0.1:9222",
            ),
            browser,
        )

        sessions = await main.list_browser_sessions(browser)
        existing = await main.get_browser_session(
            "work-chrome",
            browser,
        )
        closed = await main.close_browser_session("first", browser)

        self.assertEqual(
            {session.browser_session_id for session in sessions},
            {"first", "work-chrome"},
        )
        self.assertEqual(existing.mode, "existing")
        self.assertEqual(existing.ownership, "external")
        self.assertFalse(closed.ready)
        self.assertEqual(closed.status, "closed")
        self.assertFalse(browser.is_session_ready("first"))
        self.assertTrue(browser.is_session_ready("work-chrome"))

    async def test_session_start_error_keeps_exception_type(self):
        import main

        browser = FakeBrowser()
        browser.start_session = AsyncMock(side_effect=TimeoutError())

        with self.assertRaises(HTTPException) as context:
            await main.start_browser_session(
                main.BrowserSessionStartRequest(
                    browser_session_id="test",
                ),
                browser,
            )

        self.assertEqual(
            context.exception.detail["code"],
            "browser_session_start_failed",
        )
        self.assertIn("TimeoutError", context.exception.detail["message"])

    async def test_agent_stream_emits_public_trace_and_final_result(self):
        import main

        browser = FakeBrowser()
        browser.ready_sessions.add("browser-session-1")
        state = SimpleNamespace(
            agents={},
            agent_llm=AgentLLM(
                FakeOpenAIClient([completed_decision("stream finished")]),
                model="test-model",
            ),
            active_runs={},
            agent_locks={},
        )
        request = SimpleNamespace(app=SimpleNamespace(state=state))

        with TemporaryDirectory() as temp_dir, patch.object(
            main,
            "CONVERSATION_TRACE_DIR",
            Path(temp_dir),
        ):
            response = await main.stream_agent_run(
                main.AgentRunRequest(
                    message="inspect page",
                    conversation_id="conversation-stream",
                    browser_session_id="browser-session-1",
                    run_id="run-stream-1",
                ),
                request,
                browser,
            )
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(
                    chunk.decode() if isinstance(chunk, bytes) else chunk
                )
            self.assertTrue(
                (Path(temp_dir) / "conversation-stream.md").exists()
            )
        events = [json.loads(line) for line in "".join(chunks).splitlines()]

        self.assertEqual(events[0], {"type": "run_started", "run_id": "run-stream-1"})
        self.assertTrue(any(event["type"] == "trace" for event in events))
        result_event = next(event for event in events if event["type"] == "result")
        self.assertEqual(result_event["result"]["answer"], "stream finished")
        self.assertEqual(events[-1], {"type": "done", "run_id": "run-stream-1"})
        self.assertEqual(state.active_runs, {})

    async def test_running_agent_can_be_cancelled_by_run_id(self):
        import main

        cleanup_finished = asyncio.Event()

        async def running_agent():
            try:
                await asyncio.sleep(60)
            finally:
                # 模拟 Agent 在 finally 中异步移除页面可视化覆盖层。
                await asyncio.sleep(0)
                cleanup_finished.set()

        task = asyncio.create_task(running_agent())
        await asyncio.sleep(0)
        state = SimpleNamespace(active_runs={"run-cancel-1": task})
        request = SimpleNamespace(app=SimpleNamespace(state=state))

        result = await main.cancel_agent_run("run-cancel-1", request)

        self.assertEqual(result, {"cancelled": True, "run_id": "run-cancel-1"})
        self.assertTrue(task.cancelled())
        self.assertTrue(cleanup_finished.is_set())

    async def test_full_trace_is_readable_and_appended_per_conversation(self):
        import main

        browser = FakeBrowser()
        browser.ready_sessions.add("browser-session-1")
        llm = AgentLLM(
            FakeOpenAIClient(
                [
                    completed_decision("first finished"),
                    completed_decision("follow-up finished"),
                ]
            ),
            model="test-model",
        )
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(agents={}, agent_llm=llm)
            )
        )

        with (
            TemporaryDirectory() as temp_dir,
            patch.object(
                main,
                "CONVERSATION_TRACE_DIR",
                Path(temp_dir),
                create=True,
            ),
        ):
            await main.run_agent(
                main.AgentRunRequest(
                    message="open example.com",
                    conversation_id="conversation-1",
                    browser_session_id="browser-session-1",
                ),
                request,
                browser,
            )
            await main.run_agent(
                main.AgentRunRequest(
                    message="tell me the title",
                    conversation_id="conversation-1",
                    browser_session_id="browser-session-1",
                ),
                request,
                browser,
            )

            trace_file = Path(temp_dir) / "conversation-1.md"
            self.assertTrue(trace_file.exists())
            trace_text = trace_file.read_text(encoding="utf-8")

        self.assertIn("## 用户消息", trace_text)
        self.assertIn("open example.com", trace_text)
        self.assertIn("tell me the title", trace_text)
        self.assertIn("## LLM 输入", trace_text)
        self.assertIn("## 工具调用：agent_browser_snapshot", trace_text)
        self.assertIn('  "timestamp":', trace_text)
        self.assertNotIn('"tools":', trace_text)

    async def test_follow_up_reuses_agent_but_can_switch_browser_session(self):
        import main

        browser = FakeBrowser()
        browser.ready_sessions.update(
            {"browser-session-1", "browser-session-2"}
        )
        llm = AgentLLM(
            FakeOpenAIClient(
                [
                    completed_decision("first finished"),
                    completed_decision("follow-up finished"),
                ]
            ),
            model="test-model",
        )
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(agents={}, agent_llm=llm)
            )
        )
        first_request = main.AgentRunRequest(
            message="open example.com",
            conversation_id="conversation-1",
            browser_session_id="browser-session-1",
        )

        with (
            TemporaryDirectory() as temp_dir,
            patch.object(
                main,
                "CONVERSATION_TRACE_DIR",
                Path(temp_dir),
                create=True,
            ),
        ):
            first_result = await main.run_agent(
                first_request,
                request,
                browser,
            )
            first_agent = request.app.state.agents["conversation-1"]
            second_result = await main.run_agent(
                main.AgentRunRequest(
                    message="tell me the title",
                    conversation_id="conversation-1",
                    browser_session_id="browser-session-2",
                ),
                request,
                browser,
            )

        self.assertTrue(first_result.success)
        self.assertTrue(second_result.success)
        self.assertIs(
            request.app.state.agents["conversation-1"],
            first_agent,
        )
        self.assertEqual(
            [
                browser_session_id
                for browser_session_id, name, _ in browser.calls
                if name == "agent_browser_snapshot"
            ],
            ["browser-session-1", "browser-session-2"],
        )
        self.assertEqual(
            [message["role"] for message in first_agent.messages],
            ["user", "assistant", "user", "assistant"],
        )
        self.assertIn(
            '<最终回答 status="completed">first finished</最终回答>',
            first_agent.messages[1]["content"],
        )
        self.assertIn(
            '<最终回答 status="completed">follow-up finished</最终回答>',
            first_agent.messages[3]["content"],
        )


if __name__ == "__main__":
    unittest.main()
