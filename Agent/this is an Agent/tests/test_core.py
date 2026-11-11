"""core 层单元测试：工具与 Agent 编排（全部离线，不访问网络）。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from core.agent.events import (
    ErrorEvent,
    MessageDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    TurnStarted,
)
from core.agent.runner import Agent
from core.agent.session import ConversationSession
from core.llm.types import StreamDelta, ToolCallDelta
from core.tools.builtin.datetime_tools import GetDateTool, IsLeapYearTool
from core.tools.builtin.filesystem_tools import build_file_system_tools
from core.tools.base import ToolResult
from core.tools.paths import PathResolver
from core.tools.registry import ToolRegistry


def run(coro):
    """在独立事件循环中运行协程。"""
    return asyncio.run(coro)


class FileSystemToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        resolver = PathResolver(self.root, enforce_root=True)
        self.registry = ToolRegistry()
        for tool in build_file_system_tools(resolver, max_read_chars=100):
            self.registry.register(tool)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tool_specs_are_valid(self) -> None:
        specs = self.registry.specs()
        names = {spec.name for spec in specs}
        self.assertIn("listFiles", names)
        self.assertIn("readFile", names)
        for spec in specs:
            self.assertEqual(spec.parameters["type"], "object")
            self.assertTrue(spec.description)

    def test_create_edit_and_read_file(self) -> None:
        result = run(self.registry.execute("createFile", {"directory": ".", "filename": "a.txt"}))
        self.assertTrue(result.ok, result.content)

        result = run(
            self.registry.execute("editFile", {"path": "a.txt", "content": "hello"})
        )
        self.assertTrue(result.ok, result.content)

        result = run(self.registry.execute("readFile", {"path": "a.txt"}))
        self.assertTrue(result.ok, result.content)
        self.assertEqual(result.content, "hello")

    def test_append_mode(self) -> None:
        run(self.registry.execute("createFile", {"directory": ".", "filename": "b.txt"}))
        run(self.registry.execute("editFile", {"path": "b.txt", "content": "a"}))
        run(self.registry.execute("editFile", {"path": "b.txt", "content": "b", "append": True}))
        result = run(self.registry.execute("readFile", {"path": "b.txt"}))
        self.assertEqual(result.content, "ab")

    def test_read_is_truncated(self) -> None:
        (self.root / "big.txt").write_text("x" * 500, encoding="utf-8")
        result = run(self.registry.execute("readFile", {"path": "big.txt"}))
        self.assertIn("已截断", result.content)

    def test_sandbox_blocks_escape(self) -> None:
        result = run(self.registry.execute("readFile", {"path": "../secret.txt"}))
        self.assertFalse(result.ok)
        self.assertIn("超出工作区范围", result.content)

    def test_mkdir_and_list(self) -> None:
        result = run(self.registry.execute("mkdir", {"path": "sub"}))
        self.assertTrue(result.ok, result.content)
        result = run(self.registry.execute("listFiles", {"path": "."}))
        self.assertIn("sub", result.content)

    def test_remove_file(self) -> None:
        (self.root / "c.txt").write_text("x", encoding="utf-8")
        result = run(self.registry.execute("removeFile", {"path": "c.txt"}))
        self.assertTrue(result.ok, result.content)
        self.assertFalse((self.root / "c.txt").exists())

    def test_unknown_tool_returns_failure(self) -> None:
        result = run(self.registry.execute("nope", {}))
        self.assertFalse(result.ok)
        self.assertIn("unknown tool", result.content)


class EventContractTests(unittest.TestCase):
    def test_events_are_json_serializable(self) -> None:
        event = ToolCallFinished(
            call_id="call_1",
            name="readFile",
            arguments='{"path": "a.txt"}',
            result=ToolResult.success("ok", path=Path("a.txt")),
        )

        payload = event.to_dict()

        self.assertEqual(payload["type"], "tool_call_finished")
        self.assertEqual(payload["result"]["content"], "ok")
        self.assertEqual(payload["result"]["metadata"]["path"], "a.txt")
        json.dumps(payload)  # 不应抛出 TypeError

    def test_turn_finished_is_serializable(self) -> None:
        payload = TurnFinished(reply="hi", rounds=2).to_dict()
        self.assertEqual(payload, {"type": "turn_finished", "reply": "hi", "rounds": 2, "error": None})


class DateTimeToolTests(unittest.TestCase):
    def test_is_leap_year(self) -> None:
        tool = IsLeapYearTool()
        self.assertIn("是闰年", run(tool.execute({"year": 2024})).content)
        self.assertIn("不是闰年", run(tool.execute({"year": 1900})).content)
        self.assertIn("是闰年", run(tool.execute({"year": 2000})).content)

    def test_get_date(self) -> None:
        result = run(GetDateTool().execute({}))
        self.assertTrue(result.ok)
        self.assertRegex(result.content, r"\d{4}-\d{2}-\d{2}")


class FakeChatClient:
    """离线 LLM 客户端：按脚本返回预设的流式增量。"""

    def __init__(self, script: list[list[StreamDelta]]) -> None:
        self._script = list(script)
        self.calls: list[list[StreamDelta]] = []

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, messages, tools=()):
        del messages, tools
        script = self._script.pop(0) if self._script else []
        self.calls.append(script)
        for delta in script:
            await asyncio.sleep(0)
            yield delta


class AgentRunnerTests(unittest.TestCase):
    def _build(self, script):
        client = FakeChatClient(script)
        registry = ToolRegistry()
        registry.register(GetDateTool())
        agent = Agent(
            client=client,
            registry=registry,
            session=ConversationSession(system_prompt="你是助手"),
            max_tool_rounds=3,
        )
        return agent, client

    def test_plain_reply(self) -> None:
        agent, _ = self._build([[StreamDelta(content="你好")]])
        events = []

        async def run_agent():
            async for event in agent.run("hi"):
                events.append(event)

        run(run_agent())

        self.assertIsInstance(events[0], TurnStarted)
        self.assertIsInstance(events[1], MessageDelta)
        self.assertIsInstance(events[-1], TurnFinished)
        self.assertEqual(events[-1].reply, "你好")
        self.assertIsNone(events[-1].error)

    def test_tool_call_loop(self) -> None:
        script = [
            [
                StreamDelta(content="我查一下时间"),
                StreamDelta(
                    tool_calls=[
                        ToolCallDelta(index=0, id="call_1", name="getDate", arguments="{}")
                    ],
                    finish_reason="tool_calls",
                ),
            ],
            [StreamDelta(content="现在是上午十点")],
        ]
        agent, client = self._build(script)

        events = []

        async def run_agent():
            async for event in agent.run("现在几点"):
                events.append(event)

        run(run_agent())

        kinds = [type(event).__name__ for event in events]
        self.assertIn("ToolCallStarted", kinds)
        self.assertIn("ToolCallFinished", kinds)
        self.assertEqual(len(client.calls), 2)

        finished = [e for e in events if isinstance(e, ToolCallFinished)][0]
        self.assertTrue(finished.result.ok)
        self.assertTrue(finished.result.content)

        turn_finished = [e for e in events if isinstance(e, TurnFinished)][0]
        self.assertIn("我查一下时间", turn_finished.reply)
        self.assertIn("现在是上午十点", turn_finished.reply)

        # 会话历史：user -> assistant(tool_calls) -> tool -> assistant
        roles = [message.role for message in agent.session.messages]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])

    def test_error_is_reported_as_event(self) -> None:
        class BrokenClient:
            @property
            def model(self) -> str:
                return "broken"

            async def stream(self, messages, tools=()):
                del messages, tools
                raise RuntimeError("boom")
                yield  # pragma: no cover

        agent = Agent(client=BrokenClient(), registry=ToolRegistry())
        events = []

        async def run_agent():
            async for event in agent.run("hi"):
                events.append(event)

        run(run_agent())

        self.assertTrue(any(isinstance(event, ErrorEvent) for event in events))
        turn_finished = [e for e in events if isinstance(e, TurnFinished)][0]
        self.assertIn("boom", turn_finished.error or "")

    def test_tool_call_started_event(self) -> None:
        script = [
            [
                StreamDelta(
                    tool_calls=[
                        ToolCallDelta(index=0, id="c1", name="getDate", arguments="{}")
                    ]
                )
            ],
            [StreamDelta(content="done")],
        ]
        agent, _ = self._build(script)
        events = []

        async def run_agent():
            async for event in agent.run("hi"):
                events.append(event)

        run(run_agent())
        started = [e for e in events if isinstance(e, ToolCallStarted)][0]
        self.assertEqual(started.name, "getDate")
        self.assertEqual(started.call_id, "c1")


if __name__ == "__main__":
    unittest.main()
