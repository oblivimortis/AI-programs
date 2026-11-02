"""TUI 应用外壳。

职责：
    * 组装界面（App -> Screen -> Widgets）；
    * 提供全局快捷键；
    * 把注入的 Agent 实例交给屏幕层。

App 不关心 Agent 的内部实现（哪个模型、哪些工具），只负责把它交给屏幕。
"""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding

from core.agent.runner import Agent
from core.config.settings import AppConfig
from tui.screens.chat_screen import ChatScreen


class AgentApp(App[None]):
    """Agent 的终端界面。"""

    CSS_PATH = "theme.tcss"
    TITLE = "Agent"
    SUB_TITLE = "core + tui"

    BINDINGS = [
        Binding("ctrl+c", "quit", "退出", priority=True),
    ]

    def __init__(self, agent: Agent, config: AppConfig) -> None:
        super().__init__()
        self._agent = agent
        self._config = config

    @property
    def agent(self) -> Agent:
        return self._agent

    def on_mount(self) -> None:
        self.push_screen(ChatScreen(self._agent, self._config))


__all__ = ["AgentApp"]
