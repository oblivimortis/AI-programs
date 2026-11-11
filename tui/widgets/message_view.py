"""消息气泡组件：用户 / 助手 / 提示。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Markdown, Static

#: 助手回复的节流刷新间隔（秒），避免逐字重排导致的卡顿。
STREAM_REFRESH_INTERVAL = 1 / 20


class UserMessageView(Vertical):
    """用户输入气泡。"""

    DEFAULT_CSS = """
    UserMessageView {
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1;
    }
    UserMessageView > .role-label {
        color: $text-muted;
        text-style: bold;
    }
    UserMessageView > .user-body {
        color: $text;
    }
    """

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._text = text

    def compose(self) -> ComposeResult:
        yield Static("You", classes="role-label")
        yield Static(self._text, classes="user-body")


class AssistantMessageView(Vertical):
    """助手回复气泡，支持流式增量刷新。"""

    DEFAULT_CSS = """
    AssistantMessageView {
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1;
    }
    AssistantMessageView > .role-label {
        color: $accent;
        text-style: bold;
    }
    AssistantMessageView > Markdown {
        height: auto;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._buffer = ""
        self._pending = ""
        self._dirty = False

    def compose(self) -> ComposeResult:
        yield Static("Assistant", classes="role-label")
        yield Markdown("", classes="assistant-body")

    def on_mount(self) -> None:
        self._timer = self.set_interval(STREAM_REFRESH_INTERVAL, self.flush_pending)

    # ------------------------------------------------------------------
    # 流式接口
    # ------------------------------------------------------------------
    def append_text(self, text: str) -> None:
        """追加一段流式文本（不会立即触发重排）。"""
        if not text:
            return
        self._buffer += text
        self._pending += text
        self._dirty = True

    def flush_pending(self) -> None:
        """把待处理文本刷新到界面。"""
        if not self._dirty:
            return
        self._dirty = False
        self._pending = ""
        try:
            body = self.query_one(".assistant-body", Markdown)
        except Exception:  # pragma: no cover - 组件尚未挂载
            return
        body.update(self._buffer)

    @property
    def text(self) -> str:
        return self._buffer


class NoticeView(Static):
    """系统提示 / 错误提示。"""

    DEFAULT_CSS = """
    NoticeView {
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1;
        color: $text-muted;
    }
    NoticeView.error {
        color: $error;
    }
    """

    def __init__(self, text: str, kind: str = "info", **kwargs) -> None:
        super().__init__(text, **kwargs)
        self.add_class(kind)


__all__ = ["AssistantMessageView", "NoticeView", "UserMessageView"]
