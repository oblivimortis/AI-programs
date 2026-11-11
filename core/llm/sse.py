"""SSE（Server-Sent Events）流解析工具。

``requests`` 的流式接口是同步阻塞的，直接在异步事件循环里使用会卡住 UI。
本模块把阻塞式响应桥接为异步迭代器：读取动作放在后台线程中执行，
通过 ``asyncio.Queue`` 把结果安全地投递回事件循环。
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, AsyncIterator, Iterator

_DONE = "[DONE]"


def iter_sse_payloads(lines: Iterator[bytes]) -> Iterator[dict[str, Any]]:
    """把原始 SSE 行迭代为 JSON 负载。

    仅处理 ``data:`` 字段；``[DONE]`` 表示流结束。
    """
    for raw_line in lines:
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data:
            continue
        if data == _DONE:
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


async def aiter_sse_payloads(response: Any) -> AsyncIterator[dict[str, Any]]:
    """异步迭代 SSE 负载。

    Args:
        response: 具备 ``iter_lines()`` 方法的响应对象（requests / httpx 均可）。

    Raises:
        Exception: 后台线程中发生的异常会被重新抛到事件循环。
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()

    def pump() -> None:
        try:
            for payload in iter_sse_payloads(response.iter_lines()):
                loop.call_soon_threadsafe(queue.put_nowait, payload)
        except BaseException as exc:  # noqa: BLE001 - 需要把异常传回事件循环
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        else:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    thread = threading.Thread(target=pump, name="sse-reader", daemon=True)
    thread.start()

    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        # 消费方提前退出时，确保后台线程不会长期阻塞在网络读取上。
        closer = getattr(response, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # pragma: no cover - 关闭失败不影响主流程
                pass


__all__ = ["aiter_sse_payloads", "iter_sse_payloads"]
