"""上下文压缩、归档与检索的离线测试。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from core.agent.events import ContextStatusEvent, ErrorEvent
from core.agent.runner import Agent
from core.agent.session import ConversationSession
from core.context import (
    COMPACT_THRESHOLD,
    ContextManager,
    estimate_tokens,
    messages_tokens,
)
from core.llm.types import Message, StreamDelta
from core.tools.builtin.context_tools import RecallContextTool
from core.tools.registry import ToolRegistry


class FakeClient:
    """离线 LLM 客户端：直接返回固定文本。"""

    def __init__(self, reply: str = "这是一段摘要", usage: dict | None = None) -> None:
        self._reply = reply
        self.last_usage = usage
        self.calls = 0

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, messages, tools=()):
        del messages, tools
        self.calls += 1
        if self._reply:
            yield StreamDelta(content=self._reply)


def collect(agent: Agent, prompt: str) -> list[object]:
    async def scenario() -> list[object]:
        return [event async for event in agent.run(prompt)]

    return asyncio.run(scenario())


class TokenEstimateTests(unittest.TestCase):
    def test_estimate_chinese_and_ascii(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("中文中文"), 4)

    def test_messages_tokens_includes_tool_calls(self) -> None:
        messages = [
            Message(role="user", content="你好"),
            Message(role="tool", content="结果", tool_call_id="c1", name="readFile"),
        ]
        self.assertGreater(messages_tokens(messages), 6)


class ContextStatusTests(unittest.TestCase):
    def test_warning_and_blocked_at_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = ContextManager(tmp, window_tokens=100)
            messages = [Message(role="user", content="字" * 75)]
            status = context.status("系统", messages)
            self.assertTrue(status.warning)
            self.assertTrue(status.blocked)
            self.assertGreaterEqual(status.ratio, COMPACT_THRESHOLD)
            self.assertIn("默认", status.summary())

    def test_below_threshold_is_not_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = ContextManager(tmp, window_tokens=1000)
            status = context.status("系统", [Message(role="user", content="短")])
            self.assertFalse(status.blocked)
            self.assertFalse(status.warning)


class ArchiveTests(unittest.TestCase):
    def test_save_archive_keeps_order_and_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = ContextManager(tmp)
            messages = [
                Message(role="user", content="第一条"),
                Message(
                    role="assistant",
                    content="调用工具",
                    tool_calls=[],
                ),
                Message(role="tool", content="工具结果", tool_call_id="c1", name="readFile"),
            ]
            archive_id, path = context.save_archive(messages, "20261022-153000")
            self.assertEqual(archive_id, "20261022-153000")
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8")
            self.assertIn("第一条", text)
            self.assertIn("工具结果", text)
            self.assertIn('"tool_call_id": "c1"', text)

    def test_search_across_archives_and_by_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = ContextManager(tmp)
            context.save_archive(
                [Message(role="user", content="部署到测试环境")], "20261022-153000"
            )
            context.save_archive(
                [Message(role="user", content="别的内容")], "20261022-160000"
            )

            hits = context.search("测试环境")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["archive"], "20261022-153000")

            self.assertEqual(context.search("测试环境", "20261022-160000"), [])
            self.assertEqual(context.search(""), [])


class CompactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build_agent(self, client: FakeClient, window: int = 1000) -> tuple[Agent, ContextManager]:
        context = ContextManager(self.root, window_tokens=window)
        agent = Agent(
            client=client,
            registry=ToolRegistry(),
            session=ConversationSession(system_prompt="你是助手"),
            system_prompt="你是助手",
            context_manager=context,
        )
        agent.session.add_user("旧问题")
        agent.session.add_assistant("旧回答")
        return agent, context

    def test_compact_archives_then_replaces_history(self) -> None:
        agent, context = self._build_agent(FakeClient())
        result = asyncio.run(agent.compact())

        self.assertTrue(result.ok, result.message)
        archive = context.archive_path(result.archive_id)
        self.assertTrue(archive.is_file())
        text = archive.read_text(encoding="utf-8")
        self.assertIn("旧问题", text)
        self.assertIn("旧回答", text)

        messages = agent.session.messages
        self.assertEqual(len(messages), 1)
        self.assertIn("上下文压缩摘要", messages[0].content)
        self.assertIn(result.archive_id, messages[0].content)
        self.assertIn("recallContext", messages[0].content)
        self.assertEqual(agent.session.system_prompt, "你是助手")

    def test_failed_compaction_keeps_original_context(self) -> None:
        agent, _ = self._build_agent(FakeClient(reply=""))
        before = agent.session.messages
        result = asyncio.run(agent.compact())
        self.assertFalse(result.ok)
        self.assertEqual(agent.session.messages, before)

    def test_run_rejects_when_context_blocked(self) -> None:
        agent, _ = self._build_agent(FakeClient(), window=100)
        agent.session.add_user("字" * 100)
        events = collect(agent, "还能问吗")
        self.assertTrue(any(isinstance(event, ErrorEvent) for event in events))
        # 被拒绝时不应把新问题写进历史。
        self.assertEqual(len(agent.session.messages), 3)
        self.assertNotIn("还能问吗", [m.content for m in agent.session.messages])
        self.assertTrue(
            any(isinstance(event, ContextStatusEvent) and event.blocked for event in events)
        )

    def test_run_emits_context_status_after_response(self) -> None:
        agent, _ = self._build_agent(FakeClient(reply="好的"))
        events = collect(agent, "你好")
        statuses = [event for event in events if isinstance(event, ContextStatusEvent)]
        self.assertTrue(statuses)
        self.assertGreater(statuses[-1].used_tokens, 0)
        self.assertEqual(statuses[-1].window_tokens, 1000)

    def test_run_rejected_while_compacting(self) -> None:
        agent, _ = self._build_agent(FakeClient())
        agent._compacting = True
        events = collect(agent, "提问")
        self.assertTrue(any(isinstance(event, ErrorEvent) for event in events))


class RecallContextToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.context = ContextManager(self._tmp.name)
        self.context.save_archive(
            [Message(role="user", content="把项目部署到测试环境")], "20261022-153000"
        )
        self.tool = RecallContextTool(self.context)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_snippet_archive_and_position(self) -> None:
        result = asyncio.run(self.tool.execute({"text": "测试环境"}))
        self.assertTrue(result.ok, result.content)
        self.assertIn("20261022-153000", result.content)
        self.assertIn("第 1 条", result.content)
        self.assertIn("user", result.content)

    def test_missing_hit_reports_available_archives(self) -> None:
        result = asyncio.run(self.tool.execute({"text": "不存在的内容"}))
        self.assertFalse(result.ok)
        self.assertIn("20261022-153000", result.content)

    def test_empty_text_is_rejected(self) -> None:
        result = asyncio.run(self.tool.execute({"text": "  "}))
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
