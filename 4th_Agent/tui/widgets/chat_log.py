"""对话日志组件：负责消息气泡的排布与自动滚动。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from core.tools.base import ToolResult
from tui.widgets.message_view import AssistantMessageView, NoticeView, UserMessageView
from tui.widgets.tool_call_view import ToolCallView


class ChatLog(VerticalScroll):
    """纵向滚动的对话记录。

    组件只负责"摆放"，不负责业务逻辑：
    屏幕层把 core 的事件翻译成这里的方法调用即可。
    """

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
        padding: 1 2;
        /* 始终预留滚动条位置，避免出现/消失时正文左右跳动 */
        overflow-y: scroll;
        scrollbar-size-vertical: 3;
        scrollbar-gutter: stable;
        scrollbar-visibility: visible;
        scrollbar-background: $panel;
        scrollbar-background-hover: $panel;
        scrollbar-background-active: $panel;
        scrollbar-color: $accent;
        scrollbar-color-hover: $text;
        scrollbar-color-active: $text;
    }
    ChatLog > TurnBlock {
        height: auto;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._follow = True
        self._turn: Vertical | None = None
        self._bubble: AssistantMessageView | None = None

    # ------------------------------------------------------------------
    # 滚动
    # ------------------------------------------------------------------
    def _capture_follow(self) -> bool:
        """判断本轮内容变化后是否需要继续跟随底部。"""
        at_bottom = self.max_scroll_y <= 0 or self.scroll_offset.y >= self.max_scroll_y - 2
        if at_bottom:
            self._follow = True
        return self._follow and at_bottom

    def _schedule_scroll(self, follow: bool) -> None:
        if follow:
            self.call_after_refresh(self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        self.scroll_end(animate=False)

    def page_up(self) -> None:
        """键盘翻页：向上滚动并停止自动跟随。"""
        self._follow = False
        self.scroll_page_up(animate=False)

    def page_down(self) -> None:
        """键盘翻页：向下滚动，回到底部时恢复自动跟随。"""
        self.scroll_page_down(animate=False)
        if self.scroll_offset.y >= self.max_scroll_y - 2:
            self._follow = True

    def on_mouse_scroll_up(self, event) -> None:  # noqa: ANN001 - Textual 事件
        # 自定义 on_* 会覆盖 ScrollView 的默认滚轮处理，所以这里显式滚动。
        self._follow = False
        self.scroll_relative(y=-self.app.scroll_sensitivity_y, animate=False, immediate=True)
        event.stop()

    def on_mouse_scroll_down(self, event) -> None:  # noqa: ANN001 - Textual 事件
        self.scroll_relative(y=self.app.scroll_sensitivity_y, animate=False, immediate=True)
        if self.scroll_offset.y >= self.max_scroll_y - 2:
            self._follow = True
        event.stop()

    # ------------------------------------------------------------------
    # 内容
    # ------------------------------------------------------------------
    async def add_user_message(self, text: str) -> None:
        """追加一条用户消息。"""
        follow = self._capture_follow()
        await self.mount(UserMessageView(text))
        self._schedule_scroll(follow)

    async def add_notice(self, text: str, kind: str = "info") -> None:
        """追加一条系统提示。"""
        follow = self._capture_follow()
        await self.mount(NoticeView(text, kind))
        self._schedule_scroll(follow)

    async def begin_turn(self) -> None:
        """开始新一轮问答（创建回合容器）。"""
        follow = self._capture_follow()
        self._turn = Vertical(classes="turn-block")
        await self.mount(self._turn)
        self._bubble = None
        self._schedule_scroll(follow)

    async def append_assistant_text(self, text: str) -> None:
        """追加助手流式文本（必要时新建气泡）。"""
        if not text:
            return
        if self._turn is None:
            await self.begin_turn()
        if self._bubble is None:
            follow = self._capture_follow()
            self._bubble = AssistantMessageView()
            assert self._turn is not None
            await self._turn.mount(self._bubble)
            self._schedule_scroll(follow)
        self._bubble.append_text(text)
        self._schedule_scroll(self._capture_follow())

    async def add_tool_call(self, name: str, arguments: str) -> ToolCallView:
        """追加一个工具调用卡片。"""
        if self._turn is None:
            await self.begin_turn()
        follow = self._capture_follow()
        view = ToolCallView(name, arguments)
        assert self._turn is not None
        await self._turn.mount(view)
        # 工具卡片插在当前气泡之后，后续文本会另起气泡
        self._bubble = None
        self._schedule_scroll(follow)
        return view

    def set_tool_result(self, view: ToolCallView, result: ToolResult) -> None:
        """回填工具执行结果。"""
        view.set_result(result)
        self._schedule_scroll(self._capture_follow())

    async def clear(self) -> None:
        """清空整个对话记录。"""
        self._turn = None
        self._bubble = None
        self._follow = True
        await self.remove_children()
        await self.mount(Static(""))

    def compose(self) -> ComposeResult:
        return iter(())


__all__ = ["ChatLog"]
