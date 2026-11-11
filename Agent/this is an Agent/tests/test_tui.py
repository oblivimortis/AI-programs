"""TUI 层的离线路面测试（使用 Textual 的 headless pilot）。

说明：这里刻意使用 ``unittest.TestCase`` + ``asyncio.run`` 而不是
``IsolatedAsyncioTestCase``，后者会开启 asyncio 调试模式，使 Textual 的
渲染循环明显变慢。
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from core.agent.runner import Agent
from core.agent.session import ConversationSession
from core.config.settings import AppConfig, LLMConfig, ToolsConfig
from core.llm.types import StreamDelta, ToolCallDelta
from core.tools.builtin.datetime_tools import GetDateTool
from core.tools.registry import ToolRegistry
from textual.events import MouseScrollDown, MouseScrollUp

from tui.app import AgentApp
from tui.commands import SLASH_COMMANDS
from tui.widgets.chat_log import ChatLog
from tui.widgets.command_panel import CommandPanel
from tui.widgets.message_view import AssistantMessageView
from tui.widgets.prompt_input import PromptInput
from tui.widgets.tool_call_view import ToolCallView


class FakeChatClient:
    """离线 LLM 客户端。"""

    def __init__(self, script: list[list[StreamDelta]]) -> None:
        self._script = list(script)

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, messages, tools=()):
        del messages, tools
        script = self._script.pop(0) if self._script else []
        for delta in script:
            await asyncio.sleep(0)
            yield delta


def build_config() -> AppConfig:
    return AppConfig(
        llm=LLMConfig(base_url="http://localhost", api_key="test", model="fake-model"),
        tools=ToolsConfig(root=Path.cwd()),
    )


def build_app(script: list[list[StreamDelta]]) -> AgentApp:
    registry = ToolRegistry()
    registry.register(GetDateTool())
    agent = Agent(
        client=FakeChatClient(script),
        registry=registry,
        session=ConversationSession(system_prompt="你是助手"),
    )
    return AgentApp(agent, build_config())


class TuiSmokeTests(unittest.TestCase):
    def test_app_starts_and_shows_chat(self) -> None:
        async def scenario() -> None:
            app = build_app([[StreamDelta(content="你好")]])
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertIsNotNone(app.screen.query_one(ChatLog))
                self.assertIsNotNone(app.screen.query_one(PromptInput))

        asyncio.run(scenario())

    def test_chat_log_scrolls_with_mouse_wheel(self) -> None:
        async def scenario() -> None:
            app = build_app([])
            async with app.run_test(size=(100, 30)) as pilot:
                log = app.screen.query_one(ChatLog)
                for index in range(80):
                    await log.add_notice(f"line {index}")
                await pilot.pause()

                self.assertGreater(log.max_scroll_y, 0)
                self.assertTrue(log.show_vertical_scrollbar)
                bottom = log.scroll_offset.y

                log.post_message(MouseScrollUp(log, 1, 1, 0, -1, 0, False, False, False))
                await pilot.pause()
                self.assertLess(log.scroll_offset.y, bottom)

                log.post_message(MouseScrollDown(log, 1, 1, 0, 1, 0, False, False, False))
                await pilot.pause()
                self.assertEqual(log.scroll_offset.y, bottom)

        asyncio.run(scenario())

    def test_prompt_produces_assistant_message(self) -> None:
        async def scenario() -> None:
            app = build_app([[StreamDelta(content="这是一条回复")]])
            async with app.run_test() as pilot:
                await pilot.pause()
                prompt = app.screen.query_one(PromptInput)
                prompt.focus()
                await pilot.press("h", "i", "enter")
                for _ in range(20):
                    await pilot.pause(0.05)

                bubbles = app.screen.query(AssistantMessageView)
                self.assertTrue(bubbles, "应该出现助手消息气泡")
                self.assertIn("这是一条回复", bubbles[0].text)

        asyncio.run(scenario())

    def test_tool_call_is_rendered(self) -> None:
        script = [
            [
                StreamDelta(content="我查一下时间"),
                StreamDelta(
                    tool_calls=[
                        ToolCallDelta(index=0, id="c1", name="getDate", arguments="{}")
                    ]
                ),
            ],
            [StreamDelta(content="已经查到时间")],
        ]

        async def scenario() -> None:
            app = build_app(script)
            async with app.run_test() as pilot:
                await pilot.pause()
                prompt = app.screen.query_one(PromptInput)
                prompt.focus()
                await pilot.press("h", "i", "enter")
                for _ in range(30):
                    await pilot.pause(0.05)

                cards = app.screen.query(ToolCallView)
                self.assertTrue(cards, "应该出现工具调用卡片")
                self.assertIn("getDate", cards[0].title)

        asyncio.run(scenario())

    def test_shift_enter_inserts_newline(self) -> None:
        async def scenario() -> None:
            app = build_app([])
            async with app.run_test() as pilot:
                await pilot.pause()
                prompt = app.screen.query_one(PromptInput)
                prompt.focus()
                await pilot.press("a", "shift+enter", "b")
                await pilot.pause()
                self.assertEqual(prompt.text, "a\nb")

                await pilot.press("ctrl+j")
                await pilot.pause()
                self.assertEqual(prompt.text, "a\nb\n")

        asyncio.run(scenario())

    def test_long_assistant_reply_is_scrollable(self) -> None:
        """长 Markdown 回复必须撑开滚动区域（回合容器高度为 auto）。"""

        async def scenario() -> None:
            app = build_app([])
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                log = app.screen.query_one(ChatLog)
                await log.begin_turn()
                long_text = "\n\n".join(
                    f"### 第 {index} 段\n\n" + "这是一段很长的正文内容。" * 20
                    for index in range(8)
                )
                await log.append_assistant_text(long_text)
                for _ in range(10):
                    await pilot.pause(0.05)

                self.assertGreater(log.max_scroll_y, 0, "长回复应该可以滚动")
                log.scroll_end(animate=False)
                await pilot.pause()
                bottom = log.scroll_offset.y
                self.assertGreater(bottom, 0)
                await pilot.press("f5")
                await pilot.pause()
                self.assertLess(log.scroll_offset.y, bottom)

        asyncio.run(scenario())


class SlashCommandTests(unittest.TestCase):
    def _build(self, root: Path) -> AgentApp:
        registry = ToolRegistry()
        agent = Agent(
            client=FakeChatClient([]),
            registry=registry,
            session=ConversationSession(system_prompt="你是助手"),
        )
        config = AppConfig(
            llm=LLMConfig(base_url="http://localhost", api_key="test", model="fake-model"),
            tools=ToolsConfig(root=root),
        )
        return AgentApp(agent, config)

    def test_panel_opens_filters_and_closes(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                app = self._build(Path(tmp))
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause()
                    prompt = app.screen.query_one(PromptInput)
                    prompt.focus()
                    await pilot.press("/")
                    await pilot.pause()
                    panel = app.screen.query_one(CommandPanel)
                    self.assertTrue(panel.is_open)

                    await pilot.press("s")
                    await pilot.pause()
                    # /save 与 /status
                    self.assertEqual(panel.option_count, 2)

                    await pilot.press("escape")
                    await pilot.pause()
                    self.assertFalse(panel.is_open)

        asyncio.run(scenario())

    def test_save_and_new_session_commands(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                app = self._build(root)
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause()
                    app.agent.session.add_user("测试消息")
                    prompt = app.screen.query_one(PromptInput)
                    prompt.focus()

                    prompt.set_text("/save")
                    await pilot.press("enter")
                    await pilot.pause()
                    saved = list((root / ".agents" / "sessions").glob("*.json"))
                    self.assertEqual(len(saved), 1)

                    prompt.set_text("/new")
                    await pilot.press("enter")
                    await pilot.pause()
                    self.assertEqual(len(app.agent.session.messages), 0)

        asyncio.run(scenario())

    def test_ctrl_o_opens_full_command_menu(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                app = self._build(Path(tmp))
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause()
                    prompt = app.screen.query_one(PromptInput)
                    prompt.focus()
                    await pilot.press("ctrl+o")
                    await pilot.pause()
                    panel = app.screen.query_one(CommandPanel)
                    self.assertTrue(panel.is_open)
                    self.assertEqual(panel.option_count, len(SLASH_COMMANDS))

                    # 菜单里 Enter 直接执行选中的命令（第一项是 /new）。
                    await pilot.press("enter")
                    await pilot.pause()
                    self.assertFalse(panel.is_open)
                    self.assertEqual(len(app.agent.session.messages), 0)

        asyncio.run(scenario())

    def test_keyboard_page_scroll(self) -> None:
        async def scenario() -> None:
            app = build_app([])
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                log = app.screen.query_one(ChatLog)
                for index in range(100):
                    await log.add_notice(f"line {index}")
                await pilot.pause()
                self.assertGreater(log.max_scroll_y, 0)

                for key in ("f5", "shift+pageup", "alt+up"):
                    log._follow = True
                    log.scroll_end(animate=False)
                    await pilot.pause()
                    bottom = log.scroll_offset.y
                    await pilot.press(key)
                    await pilot.pause()
                    self.assertLess(log.scroll_offset.y, bottom, f"{key} 应该向上翻页")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
