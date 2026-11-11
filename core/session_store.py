"""会话持久化：把当前会话保存为文件，并支持列出与恢复。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.agent.session import ConversationSession
from core.llm.types import Message, ToolCall

#: 会话文件目录（相对于项目根目录）。
SESSION_DIR = Path(".agents") / "sessions"


@dataclass(frozen=True)
class SessionMeta:
    """会话列表里的一项。"""

    id: str
    title: str
    created_at: str
    message_count: int


class SessionStore:
    """会话文件的读写。

    文件格式为 JSON，包含标题、时间和完整的消息快照；
    恢复时直接按消息快照重建会话。
    """

    def __init__(self, project_root: str | Path) -> None:
        self._dir = Path(project_root) / SESSION_DIR

    @property
    def directory(self) -> Path:
        return self._dir

    def new_id(self) -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S")

    def save(
        self,
        session: ConversationSession,
        session_id: str | None = None,
        title: str | None = None,
    ) -> SessionMeta:
        """保存会话，返回元信息。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        session_id = session_id or self.new_id()
        path = self._dir / f"{session_id}.json"
        messages = [message.to_payload() for message in session.messages]
        data = {
            "id": session_id,
            "title": title or self._guess_title(messages),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message_count": len(messages),
            "agents_md_injected": bool(getattr(session, "agents_md_injected", False)),
            "messages": messages,
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return SessionMeta(
            id=session_id,
            title=data["title"],
            created_at=data["created_at"],
            message_count=len(messages),
        )

    def list(self) -> list[SessionMeta]:
        """列出全部会话，按时间从新到旧。"""
        if not self._dir.is_dir():
            return []
        metas: list[SessionMeta] = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            metas.append(
                SessionMeta(
                    id=str(data.get("id") or path.stem),
                    title=str(data.get("title") or "未命名会话"),
                    created_at=str(data.get("created_at") or ""),
                    message_count=int(data.get("message_count") or 0),
                )
            )
        return sorted(metas, key=lambda meta: meta.created_at, reverse=True)

    def load(self, session_id: str) -> tuple[list[Message], bool]:
        """读取会话消息，返回 (消息列表, 是否已注入 AGENTS.md)。"""
        path = self._dir / f"{session_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        messages = [
            self._to_message(item) for item in data.get("messages") or []
        ]
        return messages, bool(data.get("agents_md_injected"))

    @staticmethod
    def _to_message(payload: dict) -> Message:
        tool_calls = [
            ToolCall(
                id=str(call.get("id") or ""),
                name=str(call.get("name") or ""),
                arguments=str(call.get("arguments") or ""),
            )
            for call in payload.get("tool_calls") or []
        ]
        return Message(
            role=payload.get("role", "user"),
            content=str(payload.get("content") or ""),
            tool_calls=tool_calls,
            tool_call_id=payload.get("tool_call_id"),
            name=payload.get("name"),
        )

    @staticmethod
    def _guess_title(messages: list[dict]) -> str:
        """用第一条用户消息开头做标题。"""
        for message in messages:
            if message.get("role") == "user" and message.get("content"):
                text = " ".join(str(message["content"]).split())
                return text[:30] or "未命名会话"
        return "空会话"


__all__ = ["SESSION_DIR", "SessionMeta", "SessionStore"]
