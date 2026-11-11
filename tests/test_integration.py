"""端到端集成测试：用本地模拟的 OpenAI 兼容服务验证完整链路。

覆盖范围：HTTP 请求 -> SSE 解析 -> 工具调用累积 -> 工具执行 -> 结果回填。
不访问外网。
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core.agent.events import ToolCallFinished, TurnFinished
from core.agent.runner import Agent
from core.agent.session import ConversationSession
from core.config.settings import AppConfig, LLMConfig, ToolsConfig
from core.factory import build_tool_registry
from core.llm.openai_compat import OpenAICompatChatClient
from core.tools.base import Tool


def _chunk(delta: dict, finish_reason: str | None = None) -> dict:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}


class _Handler(BaseHTTPRequestHandler):
    """最小 OpenAI 兼容 SSE 服务。"""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}

        state = self.server.state  # type: ignore[attr-defined]
        index = state["count"]
        state["count"] += 1
        state["requests"].append(payload)

        chunks = state["script"][index] if index < len(state["script"]) else [
            _chunk({"content": "没有更多内容"})
        ]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        # 以关闭连接的方式结束响应（不使用 Content-Length，便于流式读取）
        self.close_connection = True

    def log_message(self, *args) -> None:  # noqa: ANN002 - 静默日志
        return


class _EchoTool(Tool):
    """回显参数的工具，便于断言参数解析是否正确。"""

    name = "echo"
    description = "回显传入的参数"

    @classmethod
    def parameters(cls) -> dict:
        return {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要回显的文本"}},
            "required": ["text"],
        }

    async def execute(self, arguments) -> object:
        from core.tools.base import ToolResult

        return ToolResult.success(f"echo: {arguments.get('text', '')}")


class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.state = {  # type: ignore[attr-defined]
            "count": 0,
            "script": [
                [
                    _chunk({"content": "我先查一下"}),
                    _chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "echo",
                                        "arguments": '{"text": "hello"}',
                                    },
                                }
                            ]
                        }
                    ),
                    _chunk({}, finish_reason="tool_calls"),
                ],
                [_chunk({"content": "工具返回了 hello"})],
            ],
            "requests": [],
        }
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}/v1/chat/completions"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _build_agent(self) -> Agent:
        config = AppConfig(
            llm=LLMConfig(base_url=self.base_url, api_key="test-key", model="mock-model"),
            tools=ToolsConfig(root=Path.cwd()),
        )
        registry = build_tool_registry(config.tools)
        registry.register(_EchoTool())
        return Agent(
            client=OpenAICompatChatClient(config.llm),
            registry=registry,
            session=ConversationSession(system_prompt="你是助手"),
            max_tool_rounds=3,
        )

    def test_full_tool_call_roundtrip(self) -> None:
        import asyncio

        agent = self._build_agent()

        async def scenario():
            events = []
            async for event in agent.run("请回显 hello"):
                events.append(event)
            return events

        events = asyncio.run(scenario())

        finished = [e for e in events if isinstance(e, ToolCallFinished)]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0].name, "echo")
        self.assertTrue(finished[0].result.ok)
        self.assertIn("hello", finished[0].result.content)

        turn = [e for e in events if isinstance(e, TurnFinished)][0]
        self.assertIn("工具返回了 hello", turn.reply)
        self.assertIsNone(turn.error)

        # 第二次请求应当带上 tool 角色的结果
        requests = self.server.state["requests"]  # type: ignore[attr-defined]
        self.assertEqual(len(requests), 2)
        second_messages = requests[1]["messages"]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertIn("echo: hello", second_messages[-1]["content"])
        self.assertTrue(requests[0]["tools"], "请求中应包含工具 schema")


if __name__ == "__main__":
    unittest.main()
