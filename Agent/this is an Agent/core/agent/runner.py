"""Agent 编排器：core 层的大脑。

职责：
    1. 维护会话历史；
    2. 调用 LLM 并把流式增量转换为事件；
    3. 解析模型的工具调用请求，执行工具并把结果回填给模型；
    4. 在限定轮次内完成 "思考 → 调用工具 → 继续思考" 的循环；
    5. 监控上下文占用，并支持 /compact 压缩（原始记录先归档）。

Agent 不 import 任何 UI 框架，只产出事件。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

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
from core.agent.session import ConversationSession
from core.context import (
    COMPACTION_PROMPT,
    DEFAULT_CONTEXT_WINDOW,
    ContextManager,
    ContextStatus,
    estimate_tokens,
)
from core.errors import AgentError
from core.llm.base import ChatClient
from core.llm.types import Message, ToolCall, ToolCallAccumulator
from core.tools.registry import ToolRegistry

#: 注入 AGENTS.md 时使用的消息前缀（用于识别是否已经注入过）。
AGENTS_MD_PREFIX = "【项目说明：AGENTS.md】"


def _agents_md_used(messages: Sequence[Message]) -> bool:
    return any(message.content.startswith(AGENTS_MD_PREFIX) for message in messages)


@dataclass
class _RoundState:
    """单轮 LLM 请求的中间状态。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class CompactResult:
    """一次 /compact 的结果。"""

    ok: bool
    message: str = ""
    archive_id: str = ""
    archive_path: str = ""
    #: 压缩后摘要约占用的 Token（估算）。
    summary_tokens: int = 0


class Agent:
    """带工具调用能力的对话代理。"""

    def __init__(
        self,
        client: ChatClient,
        registry: ToolRegistry,
        session: ConversationSession | None = None,
        max_tool_rounds: int = 5,
        system_prompt: str | None = None,
        system_prompt_provider: Callable[[], str] | None = None,
        skill_library: Any | None = None,
        context_manager: ContextManager | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._session = session or ConversationSession(system_prompt=system_prompt)
        if system_prompt and not self._session.system_prompt:
            self._session.system_prompt = system_prompt
        #: 每轮对话开始前调用，返回最新的系统提示词（用于技能热重载）。
        self._system_prompt_provider = system_prompt_provider
        #: 可选的技能库引用，供前端展示当前可用技能（不参与对话流程）。
        self._skill_library = skill_library
        #: 上下文管理器：Token 统计、归档与压缩。
        self._context = context_manager
        #: 正在压缩时禁止执行其他工具、禁止处理新任务。
        self._compacting = False
        self._max_tool_rounds = max(1, max_tool_rounds)

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------
    @property
    def client(self) -> ChatClient:
        return self._client

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def session(self) -> ConversationSession:
        return self._session

    @property
    def model(self) -> str:
        return self._client.model

    @property
    def tool_names(self) -> list[str]:
        return self._registry.names()

    @property
    def skill_library(self) -> Any | None:
        """当前技能库（可能为 None）；供前端展示可用技能。"""
        return self._skill_library

    @property
    def skill_names(self) -> list[str]:
        """当前可用技能的名称列表。"""
        skills = getattr(self._skill_library, "skills", ())
        return [skill.name for skill in skills]

    @property
    def system_prompt(self) -> str | None:
        return self._session.system_prompt

    @property
    def context_manager(self) -> ContextManager | None:
        return self._context

    @property
    def is_compacting(self) -> bool:
        return self._compacting

    # ------------------------------------------------------------------
    # 上下文状态
    # ------------------------------------------------------------------
    def context_status(self) -> ContextStatus | None:
        """返回当前上下文占用；未启用上下文管理时为 None。"""
        if self._context is None:
            return None
        return self._context.status(
            self._session.system_prompt or "",
            self._session.messages,
        )

    def _record_usage(self) -> None:
        """从客户端读取 API 返回的 total_tokens（拿不到就沿用估算）。"""
        if self._context is None:
            return
        usage = getattr(self._client, "last_usage", None)
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        if isinstance(total, int) and total > 0:
            self._context.record_usage(total)

    def _status_event(self, status: ContextStatus) -> ContextStatusEvent:
        return ContextStatusEvent(
            used_tokens=status.used_tokens,
            window_tokens=status.window_tokens,
            ratio=status.ratio,
            window_default=status.window_default,
            warning=status.warning,
            blocked=status.blocked,
        )

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    async def run(self, prompt: str) -> AsyncIterator[AgentEvent]:
        """执行一轮问答，产出事件流。

        Args:
            prompt: 用户输入。

        Yields:
            AgentEvent: 回合开始 / 文本增量 / 工具调用 / 上下文状态 / 回合结束。
        """
        # 压缩期间不允许继续处理新任务，也不允许执行其他工具。
        if self._compacting:
            error = "正在压缩上下文，请等待 /compact 完成后再提问。"
            yield ErrorEvent(message=error)
            yield TurnFinished(reply="", rounds=0, error=error)
            return

        # 达到阈值后暂停普通提问，直到用户执行 /compact。
        status = self.context_status()
        if status is not None:
            yield self._status_event(status)
            if status.blocked:
                error = "上下文占用已达到 70%，请先执行 /compact 压缩上下文。"
                yield ErrorEvent(message=error)
                yield TurnFinished(reply="", rounds=0, error=error)
                return

        # 先检查技能等系统提示词来源是否变化，再记录本轮用户消息。
        if self.refresh_system_prompt():
            yield SystemPromptUpdated()

        self._session.add_user(prompt)
        yield TurnStarted(prompt=prompt)

        reply_parts: list[str] = []
        error: str | None = None
        rounds = 0

        try:
            for round_index in range(self._max_tool_rounds):
                rounds = round_index + 1
                # 工具（例如 createSkill）可能在上一轮修改了技能文件；
                # 每次请求模型前再检查一次，变化时立即通知前端并带上新清单。
                if self.refresh_system_prompt():
                    yield SystemPromptUpdated()
                state = _RoundState()
                async for event in self._stream_once(state):
                    yield event

                # 每次 LLM 响应后更新上下文占用（优先使用 API 的 usage）。
                self._record_usage()
                current = self.context_status()
                if current is not None:
                    yield self._status_event(current)

                reply_parts.append(state.text)
                tool_calls = state.tool_calls

                if not tool_calls:
                    self._session.add_assistant(state.text)
                    break

                self._session.add_assistant(state.text, tool_calls=tool_calls)
                for call in tool_calls:
                    yield ToolCallStarted(
                        call_id=call.id,
                        name=call.name,
                        arguments=call.arguments,
                    )
                    arguments = call.parsed_arguments()
                    result = await self._registry.execute(call.name, arguments)
                    self._session.add_tool_result(call.id, result.content, name=call.name)
                    yield ToolCallFinished(
                        call_id=call.id,
                        name=call.name,
                        arguments=call.arguments,
                        result=result,
                    )
            else:
                # 达到最大工具轮数仍未拿到最终回复，提示用户。
                error = f"已达到最大工具调用轮数 ({self._max_tool_rounds})"
        except AgentError as exc:
            error = str(exc)
            yield ErrorEvent(message=error)
        except Exception as exc:  # noqa: BLE001 - 单次请求异常不应拖垮前端
            error = f"{type(exc).__name__}: {exc}"
            yield ErrorEvent(message=error)

        yield TurnFinished(reply="".join(reply_parts), rounds=rounds, error=error)

    async def _stream_once(self, state: _RoundState) -> AsyncIterator[AgentEvent]:
        """执行一次 LLM 流式请求，边流边产出事件。"""
        messages: Sequence[Message] = self._session.to_payload_messages()
        specs = self._registry.specs()
        accumulator = ToolCallAccumulator()

        async for delta in self._client.stream(messages, specs):
            if delta.content:
                state.text += delta.content
                yield MessageDelta(text=delta.content)
            accumulator.extend(delta.tool_calls)

        state.tool_calls = accumulator.build()

    # ------------------------------------------------------------------
    # 上下文压缩
    # ------------------------------------------------------------------
    @staticmethod
    def _format_message(message: Message) -> str:
        role = {
            "user": "用户",
            "assistant": "助手",
            "tool": "工具结果",
            "system": "系统",
        }.get(message.role, message.role)
        body = message.content or ""
        if message.tool_calls:
            calls = "；".join(
                f"{call.name}({call.arguments})" for call in message.tool_calls
            )
            body += f"\n[工具调用] {calls}"
        if message.tool_call_id:
            body += f"\n[对应工具调用] {message.tool_call_id}"
        return f"### {role}\n{body}"

    def _render_transcript(self, messages: Sequence[Message]) -> str:
        """把历史拼成压缩用的文本；超长时优先保留最近的对话。"""
        window = self._context.window_tokens if self._context else DEFAULT_CONTEXT_WINDOW
        budget = int(window * 0.8)
        blocks: list[str] = []
        used = 0
        for message in reversed(messages):
            block = self._format_message(message)
            cost = estimate_tokens(block)
            if used + cost > budget and blocks:
                blocks.append("（更早的消息已省略，原始内容见归档文件）")
                break
            blocks.append(block)
            used += cost
        blocks.reverse()
        return "\n\n".join(blocks)

    async def compact(self) -> CompactResult:
        """执行上下文压缩：先归档原始记录，再让 LLM 生成摘要并替换历史。"""
        if self._context is None:
            return CompactResult(ok=False, message="未启用上下文管理")
        if self._compacting:
            return CompactResult(ok=False, message="正在压缩中，请稍候")
        messages = self._session.messages
        if not messages:
            return CompactResult(ok=False, message="当前没有可压缩的对话历史")

        self._compacting = True
        try:
            # 1. 先完整保存原始上下文；保存失败则不继续。
            archive_id, path = self._context.save_archive(messages)
            if not path.is_file():
                return CompactResult(ok=False, message="原始上下文保存失败，已取消压缩")

            # 2. 只调用 LLM 生成摘要（不携带任何工具定义）。
            request = [
                Message(role="system", content=COMPACTION_PROMPT),
                Message(role="user", content=self._render_transcript(messages)),
            ]
            parts: list[str] = []
            async for delta in self._client.stream(request, ()):
                if delta.content:
                    parts.append(delta.content)
            summary = "".join(parts).strip()
            if not summary:
                return CompactResult(
                    ok=False,
                    message="模型没有返回摘要，已保留原有上下文",
                    archive_id=archive_id,
                    archive_path=str(path),
                )

            # 3. 用摘要替换历史，并追加归档说明；系统提示词与工具定义保持不变。
            finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            note = (
                f"\n\n> 此前上下文已于 {finished_at} 完成压缩，原始上下文保存在编号为 "
                f"{archive_id} 的归档文件中。如需查找历史信息，请使用 recallContext 工具。"
            )
            content = f"【上下文压缩摘要】\n\n{summary}{note}"
            self._session.replace_messages([Message(role="user", content=content)])
            self._context.record_usage(None)
            self._context.last_archive_id = archive_id
            return CompactResult(
                ok=True,
                message="上下文压缩完成",
                archive_id=archive_id,
                archive_path=str(path),
                summary_tokens=estimate_tokens(content),
            )
        except Exception as exc:  # noqa: BLE001 - 压缩失败要保留原上下文
            return CompactResult(ok=False, message=f"压缩失败: {exc}")
        finally:
            self._compacting = False

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------
    async def ask(self, prompt: str) -> str:
        """执行一轮问答并返回最终文本（不消费中间事件）。"""
        reply = ""
        async for event in self.run(prompt):
            if isinstance(event, TurnFinished):
                reply = event.reply
        return reply

    def inject_agents_md(self, path: str | Path) -> bool:
        """会话首次加载时把 AGENTS.md 注入历史。

        只注入一次：注入后的消息会随会话一起保存，后续轮次直接复用，
        不再重复拼接，保证前缀缓存稳定。
        """
        if self._session.agents_md_injected:
            return False
        file = Path(path)
        if not file.is_file():
            return False
        try:
            text = file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return False
        if not text:
            return False
        self._session.add_user(f"{AGENTS_MD_PREFIX}\n{text}")
        self._session.agents_md_injected = True
        return True

    def load_session(
        self,
        messages: Sequence[Message],
        agents_md_injected: bool = False,
    ) -> None:
        """从保存的快照恢复会话历史。"""
        self._session.replace_messages(messages)
        self._session.agents_md_injected = agents_md_injected or _agents_md_used(messages)

    def refresh_system_prompt(self) -> bool:
        """重新获取系统提示词；发生变化时写回会话。

        Returns:
            系统提示词是否发生了变化。
        """
        if self._system_prompt_provider is None:
            return False
        prompt = self._system_prompt_provider()
        if not prompt or prompt == self._session.system_prompt:
            return False
        self._session.system_prompt = prompt
        return True

    def reset(self) -> None:
        """清空会话历史。"""
        self._session.clear()


__all__ = ["AGENTS_MD_PREFIX", "Agent", "CompactResult"]
