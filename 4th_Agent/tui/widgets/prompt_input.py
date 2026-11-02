"""输入框：多行输入，Enter 发送，Shift+Enter 换行。"""

from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea


class PromptInput(TextArea):
    """底部多行输入框。

    Enter 提交；Shift+Enter 插入换行（部分终端区分不了 Shift+Enter，
    这时可以用 Ctrl+J 换行）。对外只暴露语义化事件 :class:`PromptSubmitted`。
    """

    class PromptSubmitted(Message):
        """用户提交了一轮输入。"""

        def __init__(self, prompt: str) -> None:
            super().__init__()
            self.prompt = prompt

    DEFAULT_CSS = """
    PromptInput {
        border: round $accent;
        height: 5;
        max-height: 12;
    }
    """

    BINDINGS = [
        Binding("enter", "submit", "发送", priority=True),
        Binding("shift+enter", "newline", "换行", priority=True),
        Binding("ctrl+j", "newline", "换行", priority=True),
    ]

    def on_mount(self) -> None:
        self.placeholder = (
            "输入你的问题，Enter 发送 · Shift+Enter 换行 · Ctrl+L 清空 · Ctrl+C 退出"
        )

    def action_submit(self) -> None:
        prompt = self.text.strip()
        if not prompt:
            return
        self.text = ""
        self.post_message(self.PromptSubmitted(prompt))

    def action_newline(self) -> None:
        self.insert("\n")


__all__ = ["PromptInput"]
