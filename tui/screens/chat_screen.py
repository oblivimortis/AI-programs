"""聊天主屏幕：把 core 的事件流翻译成 TUI 组件操作。

除了对话本身，这里还负责：
    * 斜杠命令（/new、/resume、/save、/compact、/quit 等）；
    * 斜杠命令补全面板；
    * 会话保存与恢复；
    * MCP 服务器的异步加载与侧栏刷新。
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList, TextArea
from textual.widgets.option_list import Option

from core.agent.events import (
    AgentEvent,
    ContextStatusEvent,
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
from core.mcp import MCPManager
from core.session_store import SessionStore
from tui.commands import SLASH_COMMANDS, filter_commands, find_command
from tui.widgets.chat_log import ChatLog
from tui.widgets.command_panel import CommandPanel
from tui.widgets.context_bar import ContextBar
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
        Binding("ctrl+o", "show_commands", "命令"),
        Binding("shift+pageup", "scroll_log(-1)", "上翻"),
        Binding("shift+pagedown", "scroll_log(1)", "下翻"),
        Binding("f5", "scroll_log(-1)", "上翻"),
        Binding("f8", "scroll_log(1)", "下翻"),
        Binding("alt+up", "scroll_log(-1)", "上翻"),
        Binding("alt+down", "scroll_log(1)", "下翻"),
        Binding("escape", "close_overlay", "关闭面板", show=False),
    ]

    DEFAULT_CSS = """
    #session-panel {
        height: auto;
        max-height: 10;
        border: round $accent;
        background: $panel;
        display: none;
    }
    """

    def __init__(self, agent: Agent, config: AppConfig, **kwargs) -> None:
        super().__init__(**kwargs)
        self._agent = agent
        self._config = config
        self._tool_views: dict[str, ToolCallView] = {}
        self._busy = False
        self._session_store = SessionStore(config.tools.root)
        self._mcp = MCPManager(config.mcp)
        self._current_session_id: str | None = None
        self._picker_open = False
        #: 是否为完整命令菜单（Ctrl+P），此时 Enter 直接执行选中命令。
        self._menu_mode = False

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
                mcp=self._mcp.states(),
            )
        with Vertical(id="composer"):
            yield CommandPanel(id="command-panel")
            yield OptionList(id="session-panel")
            yield ContextBar(id="context")
            yield StatusBar(id="status")
            yield PromptInput(id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        self._chat_log = self.query_one(ChatLog)
        self._prompt = self.query_one(PromptInput)
        self._status = self.query_one(StatusBar)
        self._panel = self.query_one(CommandPanel)
        self._status.set_status("就绪", "ready")
        self._update_context_bar()
        self.query_one(PromptInput).focus()
        await self._chat_log.add_notice(
            f"已连接模型 {self._agent.model}，可用工具 {len(self._agent.tool_names)} 个。"
            " 输入问题后按 Enter 发送 · 输入 / 或按 Ctrl+O 查看命令 · /compact 压缩上下文。"
        )
        self._init_mcp()

    async def on_unmount(self) -> None:
        await self._mcp.close_all()

    # ------------------------------------------------------------------
    # MCP：后台连接，连上后刷新侧栏
    # ------------------------------------------------------------------
    @work(exclusive=False)
    async def _init_mcp(self) -> None:
        if not self._mcp.enabled:
            self._refresh_mcp()
            return
        await self._mcp.start_all(on_update=self._refresh_mcp)

    def _refresh_mcp(self) -> None:
        """把当前 MCP 状态同步到侧栏，并注册新出现的工具。"""
        self._mcp.register_tools(self._agent.registry)
        sidebar = self.query_one(Sidebar)
        sidebar.refresh_info(
            model=self._agent.model,
            workspace=str(self._config.tools.root),
            tools=self._agent.tool_names,
            skills=self._agent.skill_names,
            mcp=self._mcp.states(),
        )

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    @on(PromptInput.PromptSubmitted)
    async def _handle_prompt(self, event: PromptInput.PromptSubmitted) -> None:
        event.stop()
        prompt = event.prompt.strip()
        if not prompt:
            return

        # 输入的是命令前缀：能确定就直接补全/执行，不确定就只补全。
        if prompt.startswith("/") and " " not in prompt and self._panel.is_open:
            candidates = filter_commands(prompt)
            names = {command.name for command in candidates}
            if candidates and prompt.lower() not in names:
                if len(candidates) == 1:
                    prompt = candidates[0].name
                else:
                    self._prompt.set_text(candidates[0].name)
                    return
        self._close_command_panel()

        if prompt.startswith("/"):
            await self._run_command(prompt)
            return

        if self._busy:
            await self._chat_log.add_notice("正在生成中，请稍候…", "info")
            return
        await self._chat_log.add_user_message(prompt)
        self._set_busy(True)
        self._run_agent(prompt)

    # ------------------------------------------------------------------
    # 斜杠命令
    # ------------------------------------------------------------------
    async def _run_command(self, text: str) -> None:
        name = text.split()[0].lower()
        command = find_command(name)
        if command is None:
            await self._chat_log.add_notice(
                f"未知命令：{name}。输入 / 查看可用命令。", "error"
            )
            return

        if command.name == "/new":
            await self._new_session()
        elif command.name == "/resume":
            await self._open_session_picker()
        elif command.name == "/save":
            await self._save_session()
        elif command.name == "/compact":
            await self._run_compact()
        elif command.name == "/status":
            await self._show_status()
        elif command.name == "/clear":
            await self.action_clear_chat()
        elif command.name == "/help":
            await self._show_help()
        elif command.name == "/quit":
            self.app.exit()

    async def _new_session(self) -> None:
        """保存当前会话并开启新的会话。"""
        if self._agent.session.messages:
            meta = self._session_store.save(
                self._agent.session, self._current_session_id
            )
            await self._chat_log.add_notice(
                f"已保存当前会话：{meta.id}（{meta.title}）", "info"
            )
        self._agent.reset()
        self._current_session_id = None
        self._tool_views.clear()
        await self._chat_log.clear()
        await self._chat_log.add_notice("已开启新会话。", "info")
        agents_md = self._config.tools.root / "AGENTS.md"
        if agents_md.is_file():
            self._agent.inject_agents_md(agents_md)
        self._update_context_bar()

    async def _save_session(self) -> None:
        if not self._agent.session.messages:
            await self._chat_log.add_notice("当前会话为空，无需保存。", "info")
            return
        meta = self._session_store.save(self._agent.session, self._current_session_id)
        self._current_session_id = meta.id
        await self._chat_log.add_notice(
            f"已保存会话 {meta.id}（{meta.message_count} 条消息）。", "info"
        )

    async def _open_session_picker(self) -> None:
        """弹出历史会话列表，用上下键选择，Enter 恢复，Esc 取消。"""
        metas = self._session_store.list()
        if not metas:
            await self._chat_log.add_notice("没有已保存的会话。", "info")
            return
        panel = self.query_one("#session-panel", OptionList)
        panel.clear_options()
        for meta in metas:
            panel.add_option(
                Option(
                    f"{meta.created_at}  {meta.title}（{meta.message_count} 条）",
                    id=meta.id,
                )
            )
        panel.display = True
        self._picker_open = True
        self._panel.hide()
        self._prompt.completion_active = False
        panel.focus()

    @on(OptionList.OptionSelected, "#session-panel")
    async def _session_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        await self._resume_session(str(event.option.id))

    async def _resume_session(self, session_id: str) -> None:
        panel = self.query_one("#session-panel", OptionList)
        panel.display = False
        self._picker_open = False
        try:
            messages, injected = self._session_store.load(session_id)
        except Exception as exc:  # noqa: BLE001 - 读取失败要提示用户
            await self._chat_log.add_notice(f"恢复会话失败：{exc}", "error")
            self._prompt.focus()
            return

        self._agent.load_session(messages, injected)
        self._current_session_id = session_id
        self._tool_views.clear()
        await self._chat_log.clear()
        await self._chat_log.add_notice(
            f"已恢复会话 {session_id}（{len(messages)} 条消息）。", "info"
        )
        await self._render_history(messages)
        self._update_context_bar()
        self._prompt.focus()

    async def _render_history(self, messages) -> None:
        """把恢复的会话简单地画回界面（只显示文本消息）。"""
        for message in messages[-20:]:
            if message.role == "user":
                await self._chat_log.add_user_message(message.content)
            elif message.role == "assistant" and message.content:
                await self._chat_log.begin_turn()
                await self._chat_log.append_assistant_text(message.content)

    async def _show_status(self) -> None:
        status = self._agent.context_status()
        lines = [f"当前会话：{self._current_session_id or '未保存'}"]
        lines.append(f"消息数：{len(self._agent.session.messages)}")
        if status is not None:
            lines.append(status.summary())
        await self._chat_log.add_notice("\n".join(lines), "info")

    async def _show_help(self) -> None:
        lines = ["可用命令："]
        for command in SLASH_COMMANDS:
            lines.append(f"{command.name}  {command.description}")
        await self._chat_log.add_notice("\n".join(lines), "info")

    # ------------------------------------------------------------------
    # 补全面板
    # ------------------------------------------------------------------
    @on(TextArea.Changed)
    async def _on_prompt_changed(self, event: TextArea.Changed) -> None:
        prompt = getattr(self, "_prompt", None)
        if prompt is None or event.text_area is not prompt or self._picker_open:
            return
        candidates = filter_commands(prompt.text)
        if candidates:
            self._panel.show(candidates)
            prompt.completion_active = True
        else:
            self._panel.hide()
            prompt.completion_active = False

    @on(PromptInput.CompletionNavigate)
    async def _complete_navigate(self, event: PromptInput.CompletionNavigate) -> None:
        if self._panel.is_open:
            self._panel.move(event.delta)

    @on(PromptInput.CompletionAccept)
    async def _complete_accept(self, event: PromptInput.CompletionAccept) -> None:
        if not self._panel.is_open:
            return
        name = self._panel.selected_command()
        if not name:
            self._close_command_panel()
            return

        # 命令菜单里选中即执行；补全模式下先把命令填进输入框。
        if self._menu_mode:
            self._close_command_panel()
            self._prompt.set_text("")
            await self._run_command(name)
            return

        if self._prompt.text.strip().lower() == name:
            self._close_command_panel()
            self._prompt.set_text("")
            await self._run_command(name)
            return

        self._prompt.set_text(name)
        self._close_command_panel()
        self._prompt.focus()

    @on(PromptInput.CompletionCancel)
    async def _complete_cancel(self, event: PromptInput.CompletionCancel) -> None:
        self._close_command_panel()
        self._prompt.focus()

    def _close_command_panel(self) -> None:
        self._panel.hide()
        self._menu_mode = False
        if getattr(self, "_prompt", None) is not None:
            self._prompt.completion_active = False

    def action_show_commands(self) -> None:
        """Ctrl+P：打开完整命令菜单，一次看到所有可用命令。"""
        if self._picker_open:
            self.query_one("#session-panel", OptionList).display = False
            self._picker_open = False
        self._panel.show(list(SLASH_COMMANDS))
        self._menu_mode = True
        self._prompt.completion_active = True
        self._prompt.focus()

    def action_close_overlay(self) -> None:
        """Esc：关闭补全面板或会话列表。"""
        if self._picker_open:
            self.query_one("#session-panel", OptionList).display = False
            self._picker_open = False
            self._prompt.focus()
            return
        self._close_command_panel()

    # ------------------------------------------------------------------
    # Agent 事件
    # ------------------------------------------------------------------
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
            self._update_context_bar()

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
        elif isinstance(event, ContextStatusEvent):
            self.query_one(ContextBar).show_status(
                event.used_tokens,
                event.window_tokens,
                event.ratio,
                event.window_default,
                warning=event.warning,
                blocked=event.blocked,
            )
        elif isinstance(event, ErrorEvent):
            self._status.set_status(event.message, "error")
            await self._chat_log.add_notice(event.message, "error")
        elif isinstance(event, TurnFinished):
            if event.error:
                self._status.set_status(event.error, "error")
            else:
                self._status.set_status("就绪", "ready")

    # ------------------------------------------------------------------
    # 上下文压缩
    # ------------------------------------------------------------------
    @work(exclusive=True, group="agent")
    async def _run_compact(self) -> None:
        """执行 /compact：先归档原始上下文，再生成摘要并替换历史。"""
        if self._busy:
            await self._chat_log.add_notice("正在生成中，请稍候…", "info")
            return
        self._set_busy(True)
        self._status.set_status("正在压缩上下文…", "busy")
        await self._chat_log.add_notice(
            "开始压缩上下文：原始记录先写入 .agents/context/，再生成摘要。", "info"
        )
        try:
            result = await self._agent.compact()
            if result.ok:
                await self._chat_log.add_notice(
                    f"压缩完成：归档 {result.archive_id}，摘要约 {result.summary_tokens} tokens。"
                    " 需要历史细节时可以让模型调用 recallContext。",
                    "info",
                )
            else:
                await self._chat_log.add_notice(f"压缩失败：{result.message}", "error")
        finally:
            self._set_busy(False)
            self._update_context_bar()

    def _update_context_bar(self) -> None:
        """按当前会话状态刷新上下文占用显示。"""
        status = self._agent.context_status()
        bar = self.query_one(ContextBar)
        if status is None:
            bar.update("上下文统计不可用")
            return
        bar.show_status(
            status.used_tokens,
            status.window_tokens,
            status.ratio,
            status.window_default,
            warning=status.warning,
            blocked=status.blocked,
        )

    def _refresh_sidebar(self) -> None:
        """热重载后同步侧栏里的工具与技能列表。"""
        sidebar = self.query_one(Sidebar)
        sidebar.refresh_info(
            model=self._agent.model,
            workspace=str(self._config.tools.root),
            tools=self._agent.tool_names,
            skills=self._agent.skill_names,
            mcp=self._mcp.states(),
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
        """新建会话：保存当前会话，再清空界面与后端历史。"""
        await self._new_session()

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
