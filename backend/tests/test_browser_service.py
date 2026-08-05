import asyncio
import json
import os
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch
from urllib.request import urlopen

from mcp.types import CallToolResult

from app.browser_process import (
    get_chrome_cdp_candidates,
    get_server_parameters,
    run_agent_browser_cli,
)
from app.mcp_client import (
    BrowserService,
    BrowserSessionDisconnected,
    ManagedBrowserSession,
    ToolValidationError,
    unwrap,
)
from app.runtime_supervisor import BrowserRuntimeSupervisor


from tests.support import mcp_result, mcp_tool, mcp_tool_v2, ready_session_info

class BrowserServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_unwrap_reads_mcp_v2_structured_content(self):
        result = CallToolResult(
            content=[],
            structuredContent={
                "response": {
                    "success": True,
                    "data": {"url": "about:blank"},
                }
            },
            isError=False,
        )

        self.assertEqual(
            unwrap(result),
            {"success": True, "data": {"url": "about:blank"}},
        )

    def test_unwrap_reads_mcp_v2_error_flag(self):
        result = CallToolResult(
            content=[],
            structuredContent={},
            isError=True,
        )

        with self.assertRaises(RuntimeError):
            unwrap(result)

    @unittest.skipUnless(
        os.getenv("BROWSER_AGENT_REAL_SMOKE") == "1",
        "set BROWSER_AGENT_REAL_SMOKE=1 to run the bounded network smoke test",
    )
    async def test_real_website_smoke_is_bounded_and_non_blocking(self):
        def fetch() -> tuple[int, str]:
            with urlopen("https://example.com/", timeout=5) as response:
                return response.status, response.geturl()

        status, url = await asyncio.wait_for(
            asyncio.to_thread(fetch),
            timeout=6,
        )

        self.assertEqual(status, 200)
        self.assertTrue(url.startswith("https://example.com"))

    @staticmethod
    def _register_current_session(
        browser: BrowserService,
        *,
        url: str | None = None,
    ) -> ManagedBrowserSession:
        managed = ManagedBrowserSession(
            browser_session_id="current",
            runtime_session_id="runtime-1",
            mode="current",
            ownership="external",
            status="ready",
            url=url,
            runtime_cdp_url="ws://127.0.0.1:9222/devtools/browser/test",
            page_count=1,
        )
        browser.sessions[managed.browser_session_id] = managed
        return managed

    def test_browser_startup_does_not_force_auto_connect(self):
        with patch.dict(
            "os.environ",
            {"AGENT_BROWSER_AUTO_CONNECT": "false"},
            clear=True,
        ):
            params = get_server_parameters()

        self.assertNotIn("AGENT_BROWSER_AUTO_CONNECT", params.env)

    def test_chrome_cdp_candidates_read_devtools_active_port(self):
        with TemporaryDirectory() as temp_dir:
            user_data_dir = Path(temp_dir)
            (user_data_dir / "DevToolsActivePort").write_text(
                "9222\n/devtools/browser/test-token\n",
                encoding="utf-8",
            )

            candidates = get_chrome_cdp_candidates([user_data_dir])

        self.assertEqual(
            candidates[0],
            "ws://127.0.0.1:9222/devtools/browser/test-token",
        )
        self.assertNotIn("http://127.0.0.1:9222", candidates)

    def test_chrome_cdp_candidates_skip_closed_fallback_ports(self):
        open_connection = MagicMock()
        with patch(
            "app.browser_process.socket.create_connection",
            side_effect=[OSError("closed"), open_connection],
        ) as connect:
            candidates = get_chrome_cdp_candidates([])

        self.assertEqual(candidates, ["http://127.0.0.1:9229"])
        self.assertEqual(
            connect.call_args_list,
            [
                call(("127.0.0.1", 9222), timeout=0.2),
                call(("127.0.0.1", 9229), timeout=0.2),
            ],
        )
        open_connection.close.assert_called_once_with()

    async def test_cli_session_start_runs_in_worker_thread(self):
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch(
                "app.browser_process.subprocess.run",
                return_value=completed,
                create=True,
            ) as run,
            patch(
                "app.browser_process.asyncio.create_subprocess_exec",
                side_effect=AssertionError(
                    "async subprocess is unsupported by the server loop"
                ),
            ),
        ):
            await run_agent_browser_cli(
                "--session",
                "test",
                "open",
                "about:blank",
            )

        run.assert_called_once()
        self.assertNotIn("capture_output", run.call_args.kwargs)

    async def test_cli_json_failure_is_not_treated_as_success(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=(
                '{"success":false,"data":null,'
                '"error":"Auto-launch failed"}'
            ),
            stderr="",
        )

        with (
            patch(
                "app.browser_process.subprocess.run",
                return_value=completed,
            ),
            self.assertRaisesRegex(RuntimeError, "Auto-launch failed"),
        ):
            await run_agent_browser_cli(
                "--session",
                "test",
                "get",
                "url",
                "--json",
            )

    async def test_tools_are_cached_for_agent_use(self):
        client = SimpleNamespace(
            list_tools=AsyncMock(
                return_value=SimpleNamespace(
                    tools=[mcp_tool("agent_browser_snapshot")],
                    nextCursor=None,
                )
            )
        )
        browser = BrowserService(client)

        tools = await browser.cache_tools()

        self.assertEqual(browser.tools, tools)
        self.assertEqual(client.list_tools.await_count, 1)

    async def test_session_is_injected_into_every_tool_call(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[
                    ready_session_info(),
                    mcp_result(
                        {"url": "about:blank", "title": "Example"}
                    ),
                ]
            )
        )
        browser = BrowserService(client)

        with patch(
            "app.mcp_client.run_agent_browser_cli",
            new=AsyncMock(
                return_value={"success": True, "data": {"url": "about:blank"}}
            ),
            create=True,
        ):
            managed = await browser.start_session("browser-session-1")
        client.call_tool.reset_mock()

        await browser.call_tool(
            browser_session_id="browser-session-1",
            name="agent_browser_get_title",
            arguments={},
        )

        client.call_tool.assert_awaited_once_with(
            "agent_browser_get_title",
            arguments={"session": managed.runtime_session_id},
        )

    async def test_timeout_ms_is_synced_to_agent_browser(self):
        """客户端超时与 agent-browser 的 timeoutMs 对齐，避免通道被占。"""
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[
                    ready_session_info(),
                    mcp_result(
                        {"url": "about:blank", "title": "Example"}
                    ),
                ]
            )
        )
        browser = BrowserService(client)
        managed = self._register_current_session(browser)
        browser.tools = [
            mcp_tool_v2(
                "agent_browser_open",
                properties={"url": {"type": "string"}},
                required=["url"],
            )
        ]
        client.call_tool.reset_mock()

        with patch("app.mcp_client.BROWSER_TOOL_TIMEOUT_SECONDS", 12):
            await browser.call_tool(
                browser_session_id=managed.browser_session_id,
                name="agent_browser_open",
                arguments={"url": "https://example.com"},
            )

        client.call_tool.assert_awaited_once_with(
            "agent_browser_open",
            arguments={
                "session": managed.runtime_session_id,
                "url": "https://example.com",
                "extraArgs": ["--cdp", "ws://127.0.0.1:9222/devtools/browser/test"],
                "timeoutMs": 12_000,
            },
        )

    async def test_tool_call_has_timeout_protection(self):
        async def wait_forever(*args, **kwargs):
            await asyncio.Event().wait()

        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[
                    ready_session_info(),
                ]
            )
        )
        events = []
        browser = BrowserService(client, lifecycle_sink=events.append)
        with patch(
            "app.mcp_client.run_agent_browser_cli",
            new=AsyncMock(
                return_value={"success": True, "data": {"url": "about:blank"}}
            ),
            create=True,
        ):
            await browser.start_session("browser-session-1")
        client.call_tool.side_effect = wait_forever

        with patch("app.mcp_client.BROWSER_TOOL_TIMEOUT_SECONDS", 0.01):
            with self.assertRaisesRegex(TimeoutError, "timed out") as context:
                await browser.call_tool(
                    browser_session_id="browser-session-1",
                    name="agent_browser_snapshot",
                    arguments={},
                )

        self.assertTrue(browser.is_session_ready("browser-session-1"))
        self.assertEqual(context.exception.code, "tool_timeout")
        self.assertEqual(
            context.exception.tool_name,
            "agent_browser_snapshot",
        )
        self.assertTrue(context.exception.retryable)
        self.assertEqual(context.exception.phase, "mcp_response")
        self.assertGreaterEqual(context.exception.duration_ms, 1)
        self.assertEqual(
            events[-1],
            {
                "type": "browser_transport",
                "event": "tool_failed",
                "tool_name": "agent_browser_snapshot",
                "runtime_session_id": ANY,
                "status": "timed_out",
                "phase": "mcp_response",
                "duration_ms": ANY,
                "error": {
                    "type": "BrowserToolTimeout",
                    "message": "agent_browser_snapshot timed out after 0.01 seconds",
                },
            },
        )

    async def test_read_only_disconnect_is_retried_once_after_recovery(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[
                    ConnectionError("connection closed"),
                    mcp_result({"title": "Example"}),
                ]
            )
        )
        browser = BrowserService(client)
        managed = self._register_current_session(browser)
        browser._recover_runtime = AsyncMock()

        result = await browser.call_tool(
            browser_session_id=managed.browser_session_id,
            name="agent_browser_get_title",
            arguments={},
        )

        self.assertEqual(result["data"]["title"], "Example")
        self.assertEqual(client.call_tool.await_count, 2)
        browser._recover_runtime.assert_awaited_once()

    async def test_navigation_disconnect_recovers_without_replaying_action(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=ConnectionError("connection closed")
            )
        )
        browser = BrowserService(client)
        managed = self._register_current_session(browser)
        browser._recover_runtime = AsyncMock()

        with self.assertRaises(BrowserSessionDisconnected) as context:
            await browser.call_tool(
                browser_session_id=managed.browser_session_id,
                name="agent_browser_open",
                arguments={"url": "https://example.com"},
            )

        self.assertEqual(client.call_tool.await_count, 1)
        browser._recover_runtime.assert_awaited_once()
        self.assertTrue(context.exception.recovered)
        self.assertEqual(context.exception.code, "session_disconnected")

    async def test_potential_write_disconnect_is_uncertain_without_replay(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=ConnectionError("connection closed")
            )
        )
        browser = BrowserService(client)
        managed = self._register_current_session(browser)
        browser._recover_runtime = AsyncMock()

        with self.assertRaises(BrowserSessionDisconnected) as context:
            await browser.call_tool(
                browser_session_id=managed.browser_session_id,
                name="agent_browser_click",
                arguments={"selector": "@e1"},
            )

        self.assertEqual(client.call_tool.await_count, 1)
        browser._recover_runtime.assert_awaited_once()
        self.assertTrue(context.exception.uncertain)
        self.assertEqual(context.exception.code, "action_uncertain")

    async def test_invalid_tool_arguments_are_rejected_before_mcp_call(self):
        client = SimpleNamespace(call_tool=AsyncMock())
        browser = BrowserService(client)
        managed = self._register_current_session(browser)
        browser.tools = [
            mcp_tool_v2(
                "agent_browser_click",
                properties={"selector": {"type": "string"}},
                required=["selector"],
            )
        ]

        with self.assertRaises(ToolValidationError) as context:
            await browser.call_tool(
                managed.browser_session_id,
                "agent_browser_click",
                {},
            )

        self.assertEqual(context.exception.code, "invalid_tool_arguments")
        self.assertIn("selector", context.exception.details["missing"])
        client.call_tool.assert_not_awaited()

    async def test_cached_read_only_annotation_allows_one_disconnect_retry(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[
                    ConnectionError("connection closed"),
                    mcp_result({"title": "Example"}),
                ]
            )
        )
        browser = BrowserService(client)
        managed = self._register_current_session(browser)
        browser.tools = [
            mcp_tool_v2(
                "custom_read_tool",
                properties={},
                read_only_hint=True,
            )
        ]
        browser._recover_runtime = AsyncMock()

        result = await browser.call_tool(
            managed.browser_session_id,
            "custom_read_tool",
            {},
        )

        self.assertEqual(result["data"]["title"], "Example")
        self.assertEqual(client.call_tool.await_count, 2)
        browser._recover_runtime.assert_awaited_once()


    async def test_runtime_supervisor_tracks_ready_and_stopped_states(self):
        events = []
        browser = SimpleNamespace(close_all_sessions=AsyncMock())

        @asynccontextmanager
        async def factory():
            events.append("enter")
            try:
                yield browser
            finally:
                events.append("exit")

        supervisor = BrowserRuntimeSupervisor(factory)

        await supervisor.start()

        self.assertEqual(supervisor.status, "ready")
        self.assertIs(supervisor.service, browser)
        self.assertEqual(supervisor.snapshot()["status"], "ready")

        await supervisor.stop()

        self.assertEqual(supervisor.status, "stopped")
        self.assertEqual(events, ["enter", "exit"])
        browser.close_all_sessions.assert_awaited_once()

    async def test_runtime_supervisor_exposes_degraded_state_after_start_failure(self):
        async def factory():
            raise RuntimeError("MCP unavailable")

        supervisor = BrowserRuntimeSupervisor(factory)

        await supervisor.start()

        self.assertEqual(supervisor.status, "degraded")
        self.assertIn("MCP unavailable", supervisor.last_error or "")
        self.assertIsNone(supervisor.service)

    async def test_runtime_rebuild_requests_are_coalesced(self):
        enters = 0
        browsers = []

        @asynccontextmanager
        async def factory():
            nonlocal enters
            enters += 1
            browser = SimpleNamespace(close_all_sessions=AsyncMock())
            browsers.append(browser)
            yield browser

        supervisor = BrowserRuntimeSupervisor(factory)
        await supervisor.start()
        await asyncio.gather(supervisor.rebuild(), supervisor.rebuild())

        self.assertEqual(enters, 2)
        self.assertEqual(supervisor.status, "ready")
        self.assertEqual(supervisor.generation, 2)
        self.assertEqual(browsers[0].close_all_sessions.await_count, 1)

    async def test_runtime_rebuild_close_failure_enters_degraded_state(self):
        browser = SimpleNamespace(
            close_all_sessions=AsyncMock(
                side_effect=RuntimeError("runtime close failed")
            )
        )

        @asynccontextmanager
        async def factory():
            yield browser

        supervisor = BrowserRuntimeSupervisor(factory)
        await supervisor.start()

        await supervisor.rebuild()

        self.assertEqual(supervisor.status, "degraded")
        self.assertIsNone(supervisor.service)
        self.assertIn("runtime close failed", supervisor.last_error or "")

    async def test_mcp_timeout_response_uses_same_structured_error(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[
                    ready_session_info(),
                    mcp_result(
                        success=False,
                        error="agent-browser command timed out after 30000ms",
                    ),
                ]
            )
        )
        events = []
        browser = BrowserService(client, lifecycle_sink=events.append)
        with patch(
            "app.mcp_client.run_agent_browser_cli",
            new=AsyncMock(
                return_value={"success": True, "data": {"url": "about:blank"}}
            ),
        ):
            await browser.start_session("browser-session-1")

        with self.assertRaises(TimeoutError) as context:
            await browser.call_tool(
                browser_session_id="browser-session-1",
                name="agent_browser_snapshot",
                arguments={},
            )

        self.assertEqual(context.exception.code, "tool_timeout")
        self.assertEqual(
            context.exception.tool_name,
            "agent_browser_snapshot",
        )
        self.assertEqual(context.exception.phase, "agent_browser")
        self.assertEqual(events[-1]["type"], "browser_transport")
        self.assertEqual(events[-1]["phase"], "agent_browser")

    async def test_slow_mcp_call_records_transport_duration(self):
        async def delayed_result(*args, **kwargs):
            await asyncio.sleep(0.01)
            return mcp_result({"snapshot": "ready"})

        client = SimpleNamespace(
            call_tool=AsyncMock(side_effect=[ready_session_info()])
        )
        events = []
        browser = BrowserService(client, lifecycle_sink=events.append)
        with patch(
            "app.mcp_client.run_agent_browser_cli",
            new=AsyncMock(
                return_value={"success": True, "data": {"url": "about:blank"}}
            ),
        ):
            managed = await browser.start_session("browser-session-1")
        client.call_tool.side_effect = delayed_result

        with patch("app.mcp_client.SLOW_BROWSER_TOOL_SECONDS", 0.005):
            await browser.call_tool(
                browser_session_id="browser-session-1",
                name="agent_browser_snapshot",
                arguments={},
            )

        self.assertEqual(
            events[-1],
            {
                "type": "browser_transport",
                "event": "tool_slow",
                "tool_name": "agent_browser_snapshot",
                "runtime_session_id": managed.runtime_session_id,
                "status": "succeeded",
                "phase": "mcp_response",
                "duration_ms": ANY,
            },
        )

    async def test_windows_isolated_start_uses_cli_once_then_mcp_health(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[ready_session_info()]
            )
        )
        browser = BrowserService(client)

        with patch(
            "app.mcp_client.run_agent_browser_cli",
            new=AsyncMock(
                return_value={"success": True, "data": {"url": "about:blank"}}
            ),
            create=True,
        ) as run_cli:
            session = await browser.start_session("test", mode="isolated")
            same_session = await browser.start_session(
                "test",
                mode="isolated",
            )

        self.assertEqual(session.url, "about:blank")
        self.assertIs(same_session, session)
        self.assertEqual(session.mode, "isolated")
        self.assertEqual(session.status, "ready")
        self.assertEqual(session.ownership, "backend")
        self.assertNotEqual(session.runtime_session_id, "test")
        self.assertTrue(browser.is_session_ready("test"))
        self.assertEqual(
            run_cli.await_args_list,
            [
                call(
                    "--session",
                    session.runtime_session_id,
                    "open",
                    "about:blank",
                    "--headed",
                    "--json",
                ),
                call(
                    "--session",
                    session.runtime_session_id,
                    "get",
                    "cdp-url",
                    "--json",
                ),
            ],
        )
        self.assertEqual(
            client.call_tool.await_args_list,
            [
                call(
                    "agent_browser_session_info",
                    arguments={"session": session.runtime_session_id},
                ),
            ],
        )

    def test_isolated_startup_closes_only_internal_chrome_new_tab_target(self):
        browser = BrowserService(SimpleNamespace())
        targets = MagicMock()
        targets.read.return_value = json.dumps(
            [
                {"id": "internal", "type": "page", "url": "chrome://newtab/"},
                {"id": "managed", "type": "page", "url": "about:blank"},
            ]
        ).encode()
        targets.__enter__.return_value = targets
        closed = MagicMock()
        closed.__enter__.return_value = closed
        after = MagicMock()
        after.read.return_value = b'[]'
        after.__enter__.return_value = after

        with patch(
            "app.mcp_client.request.urlopen",
            side_effect=[targets, closed, after],
        ) as urlopen:
            browser._close_internal_new_tab_targets(
                "ws://127.0.0.1:9222/devtools/browser/test"
            )

        self.assertEqual(
            [call.args[0] for call in urlopen.call_args_list],
            [
                "http://127.0.0.1:9222/json/list",
                "http://127.0.0.1:9222/json/close/internal",
                "http://127.0.0.1:9222/json/list",
            ],
        )

    async def test_existing_browser_is_connected_by_explicit_cdp_address(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[ready_session_info()]
            )
        )
        browser = BrowserService(client)

        with patch(
            "app.mcp_client.run_agent_browser_cli",
            new=AsyncMock(
                return_value={"success": True, "data": {"url": "https://x.com/"}}
            ),
        ) as run_cli:
            session = await browser.start_session(
                "work-chrome",
                mode="existing",
                cdp_url="http://127.0.0.1:9222",
            )

        self.assertEqual(session.url, "https://x.com/")
        self.assertEqual(session.mode, "existing")
        self.assertEqual(session.ownership, "external")
        run_cli.assert_awaited_once_with(
            "--session",
            session.runtime_session_id,
            "connect",
            "http://127.0.0.1:9222",
            "--json",
        )
        self.assertEqual(
            client.call_tool.await_args_list,
            [
                call(
                    "agent_browser_session_info",
                    arguments={
                        "session": session.runtime_session_id,
                        "extraArgs": [
                            "--cdp",
                            "http://127.0.0.1:9222",
                        ],
                    },
                ),
            ],
        )

    async def test_current_browser_connects_to_selected_tab_without_creating_tab(self):
        client = SimpleNamespace(call_tool=AsyncMock(side_effect=[ready_session_info()]))
        browser = BrowserService(client)
        run_cli = AsyncMock(
            side_effect=[
                {
                    "success": True,
                    "data": {
                        "tabs": [
                            {
                                "tabId": "t1",
                                "url": "https://example.com/current",
                                "active": True,
                            }
                        ]
                    },
                },
                {"success": True, "data": {"url": "https://example.com/current"}},
            ]
        )

        with (
            patch("app.mcp_client.run_agent_browser_cli", new=run_cli),
            patch(
                "app.mcp_client.get_chrome_cdp_candidates",
                return_value=["ws://127.0.0.1:9222/devtools/browser/test"],
            ),
        ):
            managed = await browser.start_session(
                "explicit-current",
                mode="current",
                expected_url="https://example.com/current",
            )

        self.assertEqual(managed.mode, "current")
        self.assertEqual(managed.ownership, "external")
        self.assertEqual(managed.url, "https://example.com/current")
        self.assertEqual(
            run_cli.await_args_list,
            [
                call(
                    "--session",
                    managed.runtime_session_id,
                    "--cdp",
                    "ws://127.0.0.1:9222/devtools/browser/test",
                    "tab",
                    "list",
                    "--json",
                ),
                call(
                    "--session",
                    managed.runtime_session_id,
                    "--cdp",
                    "ws://127.0.0.1:9222/devtools/browser/test",
                    "tab",
                    "t1",
                    "--json",
                ),
            ],
        )
        self.assertEqual(
            client.call_tool.await_args_list,
            [
                call(
                    "agent_browser_session_info",
                    arguments={
                        "session": managed.runtime_session_id,
                        "extraArgs": [
                            "--cdp",
                            "ws://127.0.0.1:9222/devtools/browser/test",
                        ],
                    },
                ),
            ],
        )

    async def test_current_browser_connects_when_initial_url_hint_is_stale(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[
                    mcp_result(
                        {
                            "active": True,
                            "runtime": {
                                "browserLaunched": True,
                                "pageCount": 2,
                            },
                        }
                    )
                ]
            )
        )
        browser = BrowserService(client)
        cdp_url = "ws://127.0.0.1:9222/devtools/browser/test"
        run_cli = AsyncMock(
            side_effect=[
                {
                    "success": True,
                    "data": {
                        "tabs": [
                            {"tabId": "t1", "url": "https://example.com/new"},
                            {"tabId": "t2", "url": "https://x.com/"},
                        ]
                    },
                },
                {"success": True, "data": {"url": "https://example.com/new"}},
            ]
        )

        with (
            patch("app.mcp_client.run_agent_browser_cli", new=run_cli),
            patch(
                "app.mcp_client.get_chrome_cdp_candidates",
                return_value=[cdp_url],
            ),
        ):
            managed = await browser.start_session(
                "current-browser",
                mode="current",
                expected_url="https://example.com/old",
            )

        self.assertTrue(managed.ready)
        self.assertEqual(managed.url, "https://example.com/new")
        self.assertEqual(managed.page_count, 2)
        self.assertEqual(
            run_cli.await_args_list[-1],
            call(
                "--session",
                managed.runtime_session_id,
                "--cdp",
                cdp_url,
                "tab",
                "t1",
                "--json",
            ),
        )

    async def test_current_browser_reconnect_prefers_last_known_page(self):
        browser = BrowserService(SimpleNamespace())
        managed = ManagedBrowserSession(
            browser_session_id="current",
            runtime_session_id="runtime-1",
            mode="current",
            ownership="external",
            status="recovering",
            url="https://x.com/elonmusk",
            expected_url="https://x.com/home",
        )
        cdp_url = "ws://127.0.0.1:9222/devtools/browser/test"
        run_cli = AsyncMock(
            side_effect=[
                {
                    "success": True,
                    "data": {
                        "tabs": [
                            {"tabId": "home", "url": managed.expected_url},
                            {"tabId": "profile", "url": managed.url},
                        ]
                    },
                },
                {"success": True},
            ]
        )

        with (
            patch("app.mcp_client.run_agent_browser_cli", new=run_cli),
            patch(
                "app.mcp_client.get_chrome_cdp_candidates",
                return_value=[cdp_url],
            ),
        ):
            await browser._connect_current_browser(managed)

        self.assertEqual(run_cli.await_args_list[-1].args[-2], "profile")

    async def test_current_browser_tool_keeps_external_cdp_connection(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[
                    ready_session_info(),
                    mcp_result({"url": "https://example.com/current"}),
                ]
            )
        )
        browser = BrowserService(client)
        cdp_url = "ws://127.0.0.1:9222/devtools/browser/test"
        run_cli = AsyncMock(
            side_effect=[
                {
                    "success": True,
                    "data": {
                        "tabs": [
                            {
                                "tabId": "t1",
                                "url": "https://example.com/current",
                            }
                        ]
                    },
                },
                {"success": True, "data": {"url": "https://example.com/current"}},
            ]
        )

        with (
            patch("app.mcp_client.run_agent_browser_cli", new=run_cli),
            patch(
                "app.mcp_client.get_chrome_cdp_candidates",
                return_value=[cdp_url],
            ),
        ):
            managed = await browser.start_session(
                "explicit-current",
                mode="current",
                expected_url="https://example.com/current",
            )
            await browser.call_tool(
                "explicit-current",
                "agent_browser_get_url",
                {"extraArgs": ["--headed"]},
            )

        self.assertEqual(
            client.call_tool.await_args_list[-1],
            call(
                "agent_browser_get_url",
                arguments={
                    "session": managed.runtime_session_id,
                    "extraArgs": ["--cdp", cdp_url],
                },
            ),
        )

    async def test_runtime_disconnect_recovers_without_replaying_write_action(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[
                    mcp_result(
                        success=False,
                        error="Failed to connect: connection refused",
                    ),
                    ready_session_info(),
                    mcp_result({"url": "https://x.com/elonmusk"}),
                ]
            )
        )
        events = []
        browser = BrowserService(client, lifecycle_sink=events.append)
        managed = self._register_current_session(
            browser,
            url="https://x.com/elonmusk",
        )

        with patch.object(
            browser,
            "_start_runtime",
            new=AsyncMock(return_value={"success": True}),
        ) as restart:
            with self.assertRaises(BrowserSessionDisconnected) as context:
                await browser.call_tool(
                    "current",
                    "agent_browser_click",
                    {"selector": "@e1"},
                )

        restart.assert_awaited_once_with(managed)
        self.assertTrue(context.exception.uncertain)
        self.assertEqual(client.call_tool.await_count, 2)
        self.assertEqual(managed.status, "ready")
        self.assertEqual(events[0]["type"], "browser_transport")
        self.assertEqual(events[0]["event"], "tool_failed")
        self.assertEqual(
            [
                event["event"]
                for event in events
                if event["type"] == "browser_session"
            ],
            ["runtime_recovery_started", "runtime_recovery_succeeded"],
        )
        self.assertEqual(
            client.call_tool.await_args_list[-1].kwargs["arguments"]["extraArgs"],
            ["--cdp", managed.runtime_cdp_url],
        )

    async def test_page_error_does_not_restart_runtime(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                return_value=mcp_result(
                    success=False,
                    error="Element not found: @e1",
                )
            )
        )
        browser = BrowserService(client)
        managed = self._register_current_session(browser)

        with patch.object(browser, "_start_runtime", new=AsyncMock()) as restart:
            with self.assertRaisesRegex(RuntimeError, "Element not found"):
                await browser.call_tool(
                    "current",
                    "agent_browser_click",
                    {"selector": "@e1"},
                )

        restart.assert_not_awaited()
        self.assertEqual(managed.status, "ready")
        self.assertEqual(client.call_tool.await_count, 1)

    async def test_failed_runtime_recovery_marks_session_disconnected(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                return_value=mcp_result(
                    success=False,
                    error="Failed to connect: connection refused",
                )
            )
        )
        events = []
        browser = BrowserService(client, lifecycle_sink=events.append)
        managed = self._register_current_session(browser)

        with patch.object(
            browser,
            "_start_runtime",
            new=AsyncMock(side_effect=RuntimeError("Chrome is unavailable")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Chrome is unavailable"):
                await browser.call_tool(
                    "current",
                    "agent_browser_click",
                    {"selector": "@e1"},
                )

        self.assertEqual(managed.status, "disconnected")
        self.assertEqual(client.call_tool.await_count, 1)
        self.assertEqual(events[-1]["event"], "runtime_recovery_failed")

    async def test_current_browser_failure_does_not_fall_back_to_isolated(self):
        client = SimpleNamespace(call_tool=AsyncMock(side_effect=[ready_session_info()]))
        browser = BrowserService(client)
        run_cli = AsyncMock()

        with (
            patch("app.mcp_client.run_agent_browser_cli", new=run_cli),
            patch(
                "app.mcp_client.get_chrome_cdp_candidates",
                return_value=[],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "No running Chrome found"):
                await browser.start_session(
                    "explicit-current",
                    mode="current",
                    expected_url="https://example.com/current",
                )

        self.assertEqual(
            run_cli.await_args_list,
            [
                call(
                    "--session",
                    ANY,
                    "close",
                    "--json",
                )
            ],
        )

    async def test_existing_cdp_target_cannot_be_claimed_twice(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[ready_session_info()]
            )
        )
        browser = BrowserService(client)

        with patch(
            "app.mcp_client.run_agent_browser_cli",
            new=AsyncMock(
                return_value={"success": True, "data": {"url": "https://x.com/"}}
            ),
        ):
            await browser.start_session(
                "first",
                mode="existing",
                cdp_url="http://127.0.0.1:9222",
            )
            with self.assertRaisesRegex(ValueError, "already controlled"):
                await browser.start_session(
                    "second",
                    mode="existing",
                    cdp_url="http://127.0.0.1:9222",
                )

    async def test_failed_start_is_cleaned_and_releases_cdp_claim(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(side_effect=[ready_session_info()])
        )
        browser = BrowserService(client)
        launch = AsyncMock(
            side_effect=[
                RuntimeError("connect failed"),
                {"success": True, "data": {"closed": True}},
                {"success": True, "data": {"url": "https://x.com/"}},
            ]
        )

        with patch("app.mcp_client.run_agent_browser_cli", new=launch):
            with self.assertRaisesRegex(RuntimeError, "connect failed"):
                await browser.start_session(
                    "failed",
                    mode="existing",
                    cdp_url="http://127.0.0.1:9222",
                )
            failed = browser.get_session("failed")
            recovered = await browser.start_session(
                "recovered",
                mode="existing",
                cdp_url="http://127.0.0.1:9222",
            )

        self.assertEqual(failed.status, "error")
        self.assertEqual(recovered.status, "ready")
        self.assertEqual(
            launch.await_args_list[1],
            call(
                "--session",
                failed.runtime_session_id,
                "close",
                "--json",
            ),
        )

    async def test_session_can_be_closed_independently(self):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[
                    ready_session_info(),
                    ready_session_info(),
                ]
            )
        )
        browser = BrowserService(client)

        with patch(
            "app.mcp_client.run_agent_browser_cli",
            new=AsyncMock(
                return_value={"success": True, "data": {"url": "about:blank"}}
            ),
        ) as run_cli:
            first = await browser.start_session("first", mode="isolated")
            await browser.start_session("second", mode="isolated")
            closed = await browser.close_session("first")

        self.assertFalse(browser.is_session_ready("first"))
        self.assertTrue(browser.is_session_ready("second"))
        self.assertEqual(closed.status, "closed")
        self.assertEqual(
            run_cli.await_args_list[-1],
            call(
                "--session",
                first.runtime_session_id,
                "close",
                "--json",
            ),
        )

    async def test_session_removes_visual_overlay_before_runtime_close(self):
        events: list[str] = []

        async def call_tool(name, arguments):
            if name == "agent_browser_session_info":
                return ready_session_info()
            if name == "agent_browser_eval":
                events.append("overlay_removed")
                self.assertIn("browser-agent-visual-layer", arguments["script"])
                return mcp_result({"value": True})
            raise AssertionError(f"Unexpected tool: {name}")

        async def run_cli(*arguments):
            if "close" in arguments:
                events.append("runtime_closed")
            return {"success": True, "data": {"url": "about:blank"}}

        browser = BrowserService(SimpleNamespace(call_tool=AsyncMock(side_effect=call_tool)))
        browser.tools = [mcp_tool("agent_browser_eval")]

        with patch("app.mcp_client.run_agent_browser_cli", new=run_cli):
            await browser.start_session("visual-session", mode="isolated")
            await browser.close_session("visual-session")

        self.assertEqual(events, ["overlay_removed", "runtime_closed"])

    async def test_session_start_and_close_emit_lifecycle_events(self):
        events = []
        client = SimpleNamespace(
            call_tool=AsyncMock(side_effect=[ready_session_info()])
        )
        browser = BrowserService(client, lifecycle_sink=events.append)

        with patch(
            "app.mcp_client.run_agent_browser_cli",
            new=AsyncMock(
                return_value={"success": True, "data": {"url": "about:blank"}}
            ),
        ):
            managed = await browser.start_session("test")
            await browser.close_session("test")

        self.assertEqual(
            [event["event"] for event in events],
            ["start_requested", "ready", "closed"],
        )
        ready = events[1]
        self.assertEqual(ready["type"], "browser_session")
        self.assertEqual(ready["runtime_session_id"], managed.runtime_session_id)
        self.assertEqual(ready["page_count"], 1)
        self.assertEqual(events[-1]["status"], "closed")
        self.assertEqual(events[-1]["page_count"], 0)

    async def test_all_managed_sessions_are_closed_even_if_one_close_fails(
        self,
    ):
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[ready_session_info(), ready_session_info()]
            )
        )
        browser = BrowserService(client)
        launch = AsyncMock(
            side_effect=[
                {"success": True, "data": {"url": "about:blank"}},
                {"success": True, "data": {"cdpUrl": None}},
                {"success": True, "data": {"url": "about:blank"}},
                {"success": True, "data": {"cdpUrl": None}},
                RuntimeError("first close failed"),
                {"success": True, "data": {"closed": True}},
            ]
        )

        with patch("app.mcp_client.run_agent_browser_cli", new=launch):
            first = await browser.start_session("first")
            second = await browser.start_session("second")
            failures = await browser.close_all_sessions()

        self.assertEqual(list(failures), ["first"])
        self.assertIn("first close failed", str(failures["first"]))
        self.assertEqual(browser.list_sessions(), [])
        self.assertEqual(
            launch.await_args_list[-2:],
            [
                call(
                    "--session",
                    first.runtime_session_id,
                    "close",
                    "--json",
                ),
                call(
                    "--session",
                    second.runtime_session_id,
                    "close",
                    "--json",
                ),
            ],
        )

    async def test_only_project_orphan_sessions_are_cleaned(self):
        client = SimpleNamespace()
        browser = BrowserService(client)
        run_cli = AsyncMock(
            side_effect=[
                {
                    "success": True,
                    "data": {
                        "sessions": [
                            "personal-agent",
                            "browser-agent-old-1",
                            "browser-agent-old-2",
                        ]
                    },
                },
                {"success": True, "data": {"closed": True}},
                {"success": True, "data": {"closed": True}},
            ]
        )

        with patch("app.mcp_client.run_agent_browser_cli", new=run_cli):
            cleaned = await browser.cleanup_orphaned_sessions()

        self.assertEqual(
            cleaned,
            ["browser-agent-old-1", "browser-agent-old-2"],
        )
        self.assertEqual(
            run_cli.await_args_list,
            [
                call("session", "list", "--json"),
                call(
                    "--session",
                    "browser-agent-old-1",
                    "close",
                    "--json",
                ),
                call(
                    "--session",
                    "browser-agent-old-2",
                    "close",
                    "--json",
                ),
            ],
        )

    async def test_closed_browser_is_detected_before_next_task(self):
        ready_result = ready_session_info()
        session_info_result = mcp_result(
            {
                "active": True,
                "runtime": {
                    "browserLaunched": False,
                    "pageCount": 0,
                },
            }
        )
        client = SimpleNamespace(
            call_tool=AsyncMock(
                side_effect=[ready_result, session_info_result]
            )
        )
        browser = BrowserService(client)

        with patch(
            "app.mcp_client.run_agent_browser_cli",
            new=AsyncMock(
                return_value={"success": True, "data": {"url": "about:blank"}}
            ),
            create=True,
        ):
            managed = await browser.start_session("test1")

        ready = await browser.refresh_session_ready("test1")

        self.assertFalse(ready)
        self.assertFalse(browser.is_session_ready("test1"))
        self.assertEqual(managed.status, "disconnected")
        self.assertEqual(
            client.call_tool.await_args_list[-1],
            call(
                "agent_browser_session_info",
                arguments={"session": managed.runtime_session_id},
            ),
        )
