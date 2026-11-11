"""上下文压缩与归档。

只做四件事，保持简单可读：

    1. 估算会话占用的 Token（中文按字符、英文按 4 字符约 1 Token）；
    2. 压缩前把原始消息完整写入 ``.agents/context/<编号>.jsonl``；
    3. 记录压缩状态，供 Agent 判断是否需要暂停普通提问；
    4. 为 ``recallContext`` 工具提供本地全文关键词检索。

不依赖向量数据库或 Embedding：归档就是 JSONL，检索就是子串匹配。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from core.llm.types import Message

#: 默认上下文窗口大小（128k）。
DEFAULT_CONTEXT_WINDOW = 128_000

#: 上下文占用达到该比例时警告，并暂停普通提问。
COMPACT_THRESHOLD = 0.70

#: 归档目录（相对于项目根目录）。
ARCHIVE_DIR = Path(".agents") / "context"

#: 归档文件扩展名。
ARCHIVE_SUFFIX = ".jsonl"

#: 单次检索最多返回多少条命中。
MAX_SEARCH_HITS = 6

#: 检索片段前后各保留多少字符。
SNIPPET_RADIUS = 180

#: 上下文压缩专用的 Prompt。
COMPACTION_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5,000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 `recallContext` 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

**最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。**"""


def estimate_tokens(text: str) -> int:
    """粗略估算一段文本的 Token 数。

    中文等非 ASCII 字符按 1 字符约 1 Token 计算，ASCII 字符按 4 字符约
    1 Token 计算。这个估算对中文对话足够接近，而且规则简单、可解释。
    """
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    other_chars = len(text) - ascii_chars
    return other_chars + (ascii_chars + 3) // 4


def message_tokens(message: Message) -> int:
    """估算一条消息占用的 Token（含工具调用参数）。"""
    total = 6 + estimate_tokens(message.content)
    for call in message.tool_calls:
        total += estimate_tokens(call.name) + estimate_tokens(call.arguments) + 8
    return total


def messages_tokens(messages: Iterable[Message]) -> int:
    """估算一组消息占用的 Token。"""
    return sum(message_tokens(message) for message in messages)


@dataclass(frozen=True)
class ContextStatus:
    """当前上下文占用情况。"""

    used_tokens: int
    window_tokens: int
    ratio: float
    #: 窗口大小是否为默认值（无法获知模型真实窗口时为 True）。
    window_default: bool
    #: 是否已达到警告阈值。
    warning: bool
    #: 是否应暂停普通提问，直到压缩完成。
    blocked: bool

    def summary(self) -> str:
        """给 TUI 状态栏使用的一行文本。"""
        used = f"{self.used_tokens / 1000:.1f}k"
        window = f"{self.window_tokens / 1000:.0f}k"
        percent = f"{self.ratio * 100:.0f}%"
        text = f"上下文 {used} / {window}（{percent}）"
        if self.window_default:
            text += " 默认"
        if self.blocked:
            text += " ⚠ 请执行 /compact"
        elif self.warning:
            text += " ⚠ 建议执行 /compact"
        return text


class ContextManager:
    """管理上下文归档、压缩状态与历史检索。"""

    def __init__(
        self,
        project_root: str | Path,
        *,
        window_tokens: int = DEFAULT_CONTEXT_WINDOW,
        threshold: float = COMPACT_THRESHOLD,
        window_default: bool = True,
    ) -> None:
        self._root = Path(project_root).resolve()
        self._archive_dir = self._root / ARCHIVE_DIR
        self.window_tokens = int(window_tokens) or DEFAULT_CONTEXT_WINDOW
        self.threshold = float(threshold)
        self.window_default = window_default
        #: 最近一次 API 返回的 total_tokens（拿不到时为 None，改用估算）。
        self.last_usage_tokens: int | None = None
        #: 最近一次压缩生成的归档编号。
        self.last_archive_id: str | None = None

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def archive_dir(self) -> Path:
        return self._archive_dir

    @property
    def project_root(self) -> Path:
        return self._root

    def record_usage(self, tokens: int | None) -> None:
        """记录 API 返回的 Token 用量（None 表示这次没拿到）。"""
        self.last_usage_tokens = tokens

    def status(
        self,
        system_prompt: str,
        messages: Sequence[Message],
        *,
        used_tokens: int | None = None,
    ) -> ContextStatus:
        """计算当前上下文占用。优先使用 API 用量，否则估算。"""
        if used_tokens is None:
            used_tokens = self.last_usage_tokens
        if used_tokens is None:
            used_tokens = estimate_tokens(system_prompt or "") + messages_tokens(messages)
        ratio = used_tokens / self.window_tokens if self.window_tokens else 0.0
        warning = ratio >= self.threshold
        return ContextStatus(
            used_tokens=int(used_tokens),
            window_tokens=self.window_tokens,
            ratio=ratio,
            window_default=self.window_default,
            warning=warning,
            blocked=warning,
        )

    # ------------------------------------------------------------------
    # 归档
    # ------------------------------------------------------------------
    def new_archive_id(self) -> str:
        """生成归档编号，例如 ``20261022-153000``。"""
        return datetime.now().strftime("%Y%m%d-%H%M%S")

    def archive_path(self, archive_id: str) -> Path:
        return self._archive_dir / f"{archive_id}{ARCHIVE_SUFFIX}"

    def save_archive(
        self,
        messages: Sequence[Message],
        archive_id: str | None = None,
    ) -> tuple[str, Path]:
        """把原始消息按顺序写入归档文件，返回 (编号, 路径)。"""
        archive_id = archive_id or self.new_archive_id()
        path = self.archive_path(archive_id)
        self._archive_dir.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        for index, message in enumerate(messages):
            record: dict[str, Any] = {
                "index": index,
                "role": message.role,
                "content": message.content,
            }
            if message.tool_calls:
                record["tool_calls"] = [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in message.tool_calls
                ]
            if message.tool_call_id:
                record["tool_call_id"] = message.tool_call_id
            if message.name:
                record["name"] = message.name
            lines.append(json.dumps(record, ensure_ascii=False))

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.last_archive_id = archive_id
        return archive_id, path

    def list_archives(self) -> list[str]:
        """列出全部归档编号（按文件名排序，新的在后）。"""
        if not self._archive_dir.is_dir():
            return []
        return sorted(
            path.name[: -len(ARCHIVE_SUFFIX)]
            for path in self._archive_dir.glob(f"*{ARCHIVE_SUFFIX}")
        )

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    @staticmethod
    def _searchable(record: dict[str, Any]) -> str:
        """把一条归档记录拼成可检索的文本。"""
        parts = [str(record.get("content") or "")]
        for call in record.get("tool_calls") or []:
            if isinstance(call, dict):
                parts.append(str(call.get("name") or ""))
                parts.append(str(call.get("arguments") or ""))
        if record.get("name"):
            parts.append(str(record["name"]))
        return "\n".join(parts)

    @staticmethod
    def _snippet(text: str, position: int, length: int) -> str:
        """截取命中位置前后的文本，避免结果过长。"""
        start = max(0, position - SNIPPET_RADIUS)
        end = min(len(text), position + length + SNIPPET_RADIUS)
        snippet = text[start:end].strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet = snippet + "…"
        return snippet

    def search(
        self,
        text: str,
        archive_id: str | None = None,
        *,
        limit: int = MAX_SEARCH_HITS,
    ) -> list[dict[str, Any]]:
        """在归档中做全文关键词检索。

        Args:
            text: 关键词或文本片段。
            archive_id: 只检索指定归档；为空时检索全部归档。
            limit: 最多返回几条命中。

        Returns:
            命中列表，每项包含 archive / line / role / snippet。
        """
        query = (text or "").strip()
        if not query:
            return []

        archives = [archive_id] if archive_id else self.list_archives()
        needle = query.casefold()
        hits: list[dict[str, Any]] = []

        for current in archives:
            path = self.archive_path(current)
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, start=1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                haystack = self._searchable(record)
                position = haystack.casefold().find(needle)
                if position < 0:
                    continue
                hits.append(
                    {
                        "archive": current,
                        "line": line_number,
                        "role": record.get("role", ""),
                        "snippet": self._snippet(haystack, position, len(query)),
                    }
                )
                if len(hits) >= limit:
                    return hits
        return hits


__all__ = [
    "ARCHIVE_DIR",
    "ARCHIVE_SUFFIX",
    "COMPACTION_PROMPT",
    "COMPACT_THRESHOLD",
    "ContextManager",
    "ContextStatus",
    "DEFAULT_CONTEXT_WINDOW",
    "MAX_SEARCH_HITS",
    "SNIPPET_RADIUS",
    "estimate_tokens",
    "message_tokens",
    "messages_tokens",
]
