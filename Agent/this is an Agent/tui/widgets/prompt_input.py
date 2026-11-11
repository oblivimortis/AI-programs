"""输入框：多行输入，Enter 发送，Shift+Enter 换行。

同时负责斜杠命令补全面板的按键转发：
面板打开时，上/下键移动候选，Tab 补全，Esc 关闭。
"""

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

    class CompletionNavigate(Message):
        """补全面板里移动高亮项。"""

        def __init__(self, delta: int) -> None:
            super().__init__()
            self.delta = delta

    class CompletionAccept(Message):
        """接受当前补全项。"""

    class CompletionCancel(Message):
        """关闭补全面板。"""

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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        #: 补全面板是否打开（由屏幕层设置）。
        self.completion_active = False

    def on_mount(self) -> None:
        self.placeholder = (
            "输入你的问题，Enter 发送 · 输入 / 打开命令 · Shift+Enter 换行 · Ctrl+C 退出"
        )

    def on_key(self, event) -> None:  # noqa: ANN001 - Textual 事件
        """面板打开时优先处理补全相关按键。"""
        if not self.completion_active:
            return
        if event.key in ("up", "down"):
            self.post_message(
                self.CompletionNavigate(-1 if event.key == "up" else 1)
            )
            event.stop()
        elif event.key == "tab":
            self.post_message(self.CompletionAccept())
            event.stop()
        elif event.key == "escape":
            self.post_message(self.CompletionCancel())
            event.stop()

    def action_submit(self) -> None:
        # 补全面板打开时，Enter 先交给屏幕层处理（补全或执行命令）。
        if self.completion_active:
            self.post_message(self.CompletionAccept())
            return
        prompt = self.text.strip()
        if not prompt:
            return
        self.text = ""
        self.post_message(self.PromptSubmitted(prompt))

    def action_newline(self) -> None:
        self.insert("\n")

    def set_text(self, text: str) -> None:
        """替换全部内容并把光标放到末尾。"""
        self.text = text
        self.move_cursor(self.document.end)


__all__ = ["PromptInput"]
