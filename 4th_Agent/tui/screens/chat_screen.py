"""聊天主屏幕：把 core 的事件流翻译成 TUI 组件操作。

这是 TUI 层的"应用服务"：
    * 只依赖 ``core`` 的公开 API（Agent / 事件）；
    * 不直接访问网络、文件系统；
    * 所有界面细节都通过 ``tui/widgets`` 中的组件完成。
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header

from core.agent.events import (
    AgentEvent,
    ErrorEvent,
    MessageDelta,
    SystemPromptUpdated,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    TurnStarted,
)
from core.agent.runner import Agent
from core.config.settings import AppConfig
from tui.widgets.chat_log import ChatLog
from tui.widgets.prompt_input import PromptInput
from tui.widgets.sidebar import Sidebar
from tui.widgets.status_bar import StatusBar
from tui.widgets.tool_call_view import ToolCallView


class ChatScreen(Screen[None]):
    """Agent 对话界面。"""

    BINDINGS = [
        Binding("ctrl+l", "clear_chat", "清空"),
        Binding("ctrl+n", "new_session", "新会话"),
        Binding("ctrl+b", "toggle_sidebar", "侧栏"),
        Binding("shift+pageup", "scroll_log(-1)", "上翻"),
        Binding("shift+pagedown", "scroll_log(1)", "下翻"),
        Binding("f5", "scroll_log(-1)", "上翻"),
        Binding("f8", "scroll_log(1)", "下翻"),
    ]

    def __init__(self, agent: Agent, config: AppConfig, **kwargs) -> None:
        super().__init__(**kwargs)
        self._agent = agent
        self._config = config
        self._tool_views: dict[str, ToolCallView] = {}
        self._busy = False

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield ChatLog(id="chat-log")
            yield Sidebar(
                id="sidebar",
                model=self._agent.model,
                workspace=str(self._config.tools.root),
                tools=self._agent.tool_names,
                skills=self._agent.skill_names,
            )
        with Vertical(id="composer"):
            yield StatusBar(id="status")
            yield PromptInput(id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        self._chat_log = self.query_one(ChatLog)
        self._prompt = self.query_one(PromptInput)
        self._status = self.query_one(StatusBar)
        self._status.set_status("就绪", "ready")
        self.query_one(PromptInput).focus()
        await self._chat_log.add_notice(
            f"已连接模型 {self._agent.model}，可用工具 {len(self._agent.tool_names)} 个。"
            " 输入问题后按 Enter 发送。"
        )

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    @on(PromptInput.PromptSubmitted)
    async def _handle_prompt(self, event: PromptInput.PromptSubmitted) -> None:
        event.stop()
        if self._busy:
            await self._chat_log.add_notice("正在生成中，请稍候…", "info")
            return
        await self._chat_log.add_user_message(event.prompt)
        self._set_busy(True)
        self._run_agent(event.prompt)

    @work(exclusive=True, group="agent")
    async def _run_agent(self, prompt: str) -> None:
        """在后台消费 core 的事件流。"""
        try:
            async for event in self._agent.run(prompt):
                await self._dispatch(event)
        except Exception as exc:  # noqa: BLE001 - 任何异常都要在界面上可见
            self._status.set_status(f"{type(exc).__name__}: {exc}", "error")
            await self._chat_log.add_notice(f"执行失败: {exc}", "error")
        finally:
            self._set_busy(False)

    async def _dispatch(self, event: AgentEvent) -> None:
        """把 core 事件映射为界面操作。"""
        if isinstance(event, SystemPromptUpdated):
            await self._chat_log.add_notice("检测到技能文件变化，已热重载系统提示词。", "info")
            self._refresh_sidebar()
        elif isinstance(event, TurnStarted):
            await self._chat_log.begin_turn()
        elif isinstance(event, MessageDelta):
            await self._chat_log.append_assistant_text(event.text)
        elif isinstance(event, ToolCallStarted):
            view = await self._chat_log.add_tool_call(event.name, event.arguments)
            self._tool_views[event.call_id] = view
        elif isinstance(event, ToolCallFinished):
            view = self._tool_views.pop(event.call_id, None)
            if view is not None:
                self._chat_log.set_tool_result(view, event.result)
        elif isinstance(event, ErrorEvent):
            self._status.set_status(event.message, "error")
            await self._chat_log.add_notice(event.message, "error")
        elif isinstance(event, TurnFinished):
            if event.error:
                self._status.set_status(event.error, "error")
            else:
                self._status.set_status("就绪", "ready")

    def _refresh_sidebar(self) -> None:
        """热重载后同步侧栏里的工具与技能列表。"""
        sidebar = self.query_one(Sidebar)
        sidebar.refresh_info(
            model=self._agent.model,
            workspace=str(self._config.tools.root),
            tools=self._agent.tool_names,
            skills=self._agent.skill_names,
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._status.set_status("生成中…" if busy else "就绪", "busy" if busy else "ready")
        self._prompt.disabled = busy

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    async def action_clear_chat(self) -> None:
        """清空界面上的对话记录（保留后端会话历史）。"""
        self._tool_views.clear()
        await self._chat_log.clear()
        await self._chat_log.add_notice("界面已清空（后端会话历史仍然保留）。", "info")

    async def action_new_session(self) -> None:
        """新建会话：清空界面与后端历史。"""
        self._agent.reset()
        self._tool_views.clear()
        await self._chat_log.clear()
        await self._chat_log.add_notice("已开启新会话。", "info")

    def action_toggle_sidebar(self) -> None:
        """显示 / 隐藏侧边栏。"""
        sidebar = self.query_one(Sidebar)
        sidebar.display = not sidebar.display

    def action_scroll_log(self, direction: int = 1) -> None:
        """键盘翻页聊天记录：鼠标滚轮不可用时也能回看历史。"""
        log = self.query_one(ChatLog)
        if direction < 0:
            log.page_up()
        else:
            log.page_down()


__all__ = ["ChatScreen"]
