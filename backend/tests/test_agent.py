import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.agent import Agent, AgentAction, AgentDecision
from app.llm import AgentLLM
from app.mcp_client import BrowserService
from app.utils import format_mcp_tools


def mcp_tool(name: str):
    return SimpleNamespace(
        name=name,
        description=f"{name} description",
        inputSchema={"type": "object", "properties": {}},
    )


class BrowserServiceTests(unittest.IsolatedAsyncioTestCase):
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
                return_value=SimpleNamespace(
                    structuredContent={
                        "response": {"success": True, "data": {"title": "Example"}}
                    },
                    isError=False,
                    content=[],
                )
            )
        )
        browser = BrowserService(client)

        await browser.call_tool(
            session_id="browser-session-1",
            name="agent_browser_get_title",
            arguments={},
        )

        client.call_tool.assert_awaited_once_with(
            "agent_browser_get_title",
            arguments={"session": "browser-session-1"},
        )


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


class FakeBrowser:
    def __init__(self):
        self.tools = [
            mcp_tool("agent_browser_snapshot"),
            mcp_tool("agent_browser_fill"),
            mcp_tool("agent_browser_click"),
            mcp_tool("agent_browser_custom_tool"),
        ]
        self.calls = []
        self.snapshot_count = 0

    async def call_tool(self, session_id, name, arguments):
        self.calls.append((session_id, name, arguments))
        if name == "agent_browser_snapshot":
            self.snapshot_count += 1
            return {"snapshot": f"CURRENT-SNAPSHOT-{self.snapshot_count}"}
        return {"success": True, "action": name}


class FakeResponses:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.decisions.pop(0))


class FakeOpenAIClient:
    def __init__(self, decisions):
        self.responses = FakeResponses(decisions)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def test_decision_requires_actions_or_final_answer_but_not_both(self):
        with self.assertRaises(ValidationError):
            AgentDecision()

        with self.assertRaises(ValidationError):
            AgentDecision(
                actions=[AgentAction(name="agent_browser_click")],
                final_answer="done",
            )

    async def test_current_observation_is_replaced_and_page_change_ends_batch(self):
        browser = FakeBrowser()
        openai_client = FakeOpenAIClient(
            [
                AgentDecision(
                    actions=[
                        AgentAction(
                            name="agent_browser_fill",
                            arguments={"selector": "@e1", "text": "alice"},
                        ),
                        AgentAction(
                            name="agent_browser_click",
                            arguments={"selector": "@e2"},
                        ),
                        AgentAction(
                            name="agent_browser_fill",
                            arguments={"selector": "@e3", "text": "stale"},
                        ),
                    ]
                ),
                AgentDecision(final_answer="finished"),
            ]
        )
        llm = AgentLLM(openai_client, model="test-model")
        agent = Agent(
            task="fill and submit",
            session_id="browser-session-1",
            browser=browser,
            llm=llm,
            max_steps=3,
        )
        agent.trace.append({"type": "debug", "content": "TRACE-ONLY"})

        result = await agent.run()

        executed_names = [name for _, name, _ in browser.calls]
        self.assertEqual(
            executed_names,
            [
                "agent_browser_snapshot",
                "agent_browser_fill",
                "agent_browser_click",
                "agent_browser_snapshot",
            ],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.answer, "finished")
        first_call = openai_client.responses.calls[0]
        second_call = openai_client.responses.calls[1]
        self.assertEqual(first_call["model"], "test-model")
        self.assertIs(first_call["text_format"], AgentDecision)
        self.assertIn("agent_browser_custom_tool", str(first_call["input"]))
        self.assertIn("CURRENT-SNAPSHOT-2", str(second_call["input"]))
        self.assertNotIn("CURRENT-SNAPSHOT-1", str(second_call["input"]))
        self.assertIn("@e1", str(second_call["input"]))
        self.assertNotIn("TRACE-ONLY", str(second_call["input"]))
        self.assertEqual(agent.task_context, [])
        self.assertEqual(
            [
                event["name"]
                for event in agent.trace
                if event["type"] == "tool_call"
            ],
            [
                "agent_browser_snapshot",
                "agent_browser_fill",
                "agent_browser_click",
                "agent_browser_snapshot",
            ],
        )
        self.assertTrue(
            any(event["type"] == "llm_result" for event in agent.trace)
        )

    async def test_messages_continue_across_tasks_and_task_context_is_cleared(self):
        browser = FakeBrowser()
        openai_client = FakeOpenAIClient(
            [
                AgentDecision(
                    final_answer="first task finished\n\nTask supplement: exported report.csv"
                ),
                AgentDecision(final_answer="follow-up finished"),
            ]
        )
        llm = AgentLLM(openai_client, model="test-model")
        agent = Agent(
            task="export the report",
            session_id="browser-session-1",
            browser=browser,
            llm=llm,
        )
        agent.task_context.append(
            {"type": "tool_result", "content": "temporary tool result"}
        )

        first_result = await agent.run()

        self.assertTrue(first_result.success)
        self.assertEqual(
            agent.messages,
            [
                {"role": "user", "content": "export the report"},
                {
                    "role": "assistant",
                    "content": "first task finished\n\nTask supplement: exported report.csv",
                },
            ],
        )
        self.assertEqual(agent.task_context, [])

        agent.add_user_message("check the exported file")
        await agent.run()

        follow_up_context = str(openai_client.responses.calls[1]["input"])
        self.assertIn("first task finished", follow_up_context)
        self.assertIn("check the exported file", follow_up_context)
        self.assertNotIn("temporary tool result", follow_up_context)
        self.assertIn("CURRENT-SNAPSHOT-2", follow_up_context)
        self.assertNotIn("CURRENT-SNAPSHOT-1", follow_up_context)
        self.assertTrue(
            any(
                event["type"] == "message"
                and event["role"] == "user"
                and event["content"] == "check the exported file"
                for event in agent.trace
            )
        )


if __name__ == "__main__":
    unittest.main()
