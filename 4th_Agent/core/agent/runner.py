"""Agent 编排器：core 层的大脑。

职责：
    1. 维护会话历史；
    2. 调用 LLM 并把流式增量转换为事件；
    3. 解析模型的工具调用请求，执行工具并把结果回填给模型；
    4. 在限定轮次内完成 "思考 → 调用工具 → 继续思考" 的循环。

Agent 不 import 任何 UI 框架，只产出事件。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Sequence

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
from core.agent.session import ConversationSession
from core.errors import AgentError
from core.llm.base import ChatClient
from core.llm.types import Message, ToolCall, ToolCallAccumulator
from core.tools.registry import ToolRegistry


@dataclass
class _RoundState:
    """单轮 LLM 请求的中间状态。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


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

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    async def run(self, prompt: str) -> AsyncIterator[AgentEvent]:
        """执行一轮问答，产出事件流。

        Args:
            prompt: 用户输入。

        Yields:
            AgentEvent: 回合开始 / 文本增量 / 工具调用 / 回合结束 / 错误。
        """
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
    # 便捷方法
    # ------------------------------------------------------------------
    async def ask(self, prompt: str) -> str:
        """执行一轮问答并返回最终文本（不消费中间事件）。"""
        reply = ""
        async for event in self.run(prompt):
            if isinstance(event, TurnFinished):
                reply = event.reply
        return reply

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


__all__ = ["Agent"]
