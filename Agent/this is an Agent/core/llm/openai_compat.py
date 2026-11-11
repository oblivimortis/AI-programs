"""OpenAI 兼容协议的流式客户端适配器。

兼容 DeepSeek / OpenAI / 通义千问 / vLLM 等 ``/v1/chat/completions`` 接口。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Sequence

import requests

from core.config.settings import LLMConfig
from core.errors import LLMError
from core.llm.sse import aiter_sse_payloads
from core.llm.types import Message, StreamDelta, ToolCallDelta, ToolSpec


class OpenAICompatChatClient:
    """基于 ``requests`` 的 OpenAI 兼容流式客户端。"""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        #: 最近一次请求返回的 usage（服务端不提供时为 None）。
        self._last_usage: dict[str, Any] | None = None

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def last_usage(self) -> dict[str, Any] | None:
        """最近一次流式请求的 usage，形如 ``{"total_tokens": 123}``。"""
        return self._last_usage

    @property
    def config(self) -> LLMConfig:
        return self._config

    # ------------------------------------------------------------------
    # 请求构造
    # ------------------------------------------------------------------
    def _build_payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [message.to_payload() for message in messages],
            "stream": True,
            # 请求服务端在最后一块里附带 usage，用于统计上下文占用。
            "stream_options": {"include_usage": True},
            "temperature": self._config.temperature,
        }
        if self._config.max_tokens:
            payload["max_tokens"] = self._config.max_tokens
        if tools and self._config.enable_tools:
            payload["tools"] = [spec.to_payload() for spec in tools]
            payload["tool_choice"] = "auto"
        return payload

    # ------------------------------------------------------------------
    # 同步（线程内）调用
    # ------------------------------------------------------------------
    def _post(self, payload: dict[str, Any]) -> Any:
        try:
            return requests.post(
                self._config.base_url,
                headers=self._config.auth_headers(),
                json=payload,
                stream=True,
                timeout=self._config.request_timeout,
            )
        except requests.RequestException as exc:
            raise LLMError(f"请求大模型服务失败: {exc}") from exc

    def _create_response(self, payload: dict[str, Any]) -> Any:
        response = self._post(payload)
        # 少数兼容服务不认 stream_options：去掉它再试一次，保证仍然可用。
        if response.status_code == 400 and "stream_options" in payload:
            response.close()
            fallback = dict(payload)
            fallback.pop("stream_options", None)
            response = self._post(fallback)

        if response.status_code >= 400:
            body = ""
            try:
                body = response.text[:500]
            except Exception:  # pragma: no cover - 极端情况
                body = "<无法读取响应体>"
            response.close()
            raise LLMError(
                f"大模型服务返回错误状态 {response.status_code}: {body}"
            )
        return response

    # ------------------------------------------------------------------
    # 异步流式接口
    # ------------------------------------------------------------------
    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
    ) -> AsyncIterator[StreamDelta]:
        payload = self._build_payload(messages, tools)
        self._last_usage = None
        response = await asyncio.to_thread(self._create_response, payload)
        try:
            async for delta in self._iter_deltas(response):
                yield delta
        except LLMError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转换为领域异常
            raise LLMError(f"解析大模型流式响应失败: {exc}") from exc
        finally:
            await asyncio.to_thread(response.close)

    async def _iter_deltas(self, response: Any) -> AsyncIterator[StreamDelta]:
        async for payload in aiter_sse_payloads(response):
            usage = payload.get("usage")
            if isinstance(usage, dict):
                self._last_usage = usage
            choices = payload.get("choices") or []
            if not choices:
                continue
            choice = choices[0] or {}
            delta = choice.get("delta") or {}

            content = delta.get("content") or ""
            tool_deltas: list[ToolCallDelta] = []
            for raw_call in delta.get("tool_calls") or []:
                function = raw_call.get("function") or {}
                tool_deltas.append(
                    ToolCallDelta(
                        index=int(raw_call.get("index", 0) or 0),
                        id=raw_call.get("id") or "",
                        name=function.get("name") or "",
                        arguments=function.get("arguments") or "",
                    )
                )

            finish_reason = choice.get("finish_reason")
            if content or tool_deltas or finish_reason:
                yield StreamDelta(
                    content=content,
                    tool_calls=tool_deltas,
                    finish_reason=finish_reason,
                )


__all__ = ["OpenAICompatChatClient"]
