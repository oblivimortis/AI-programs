"""searchWeb 工具与搜索配置测试（全部离线，不访问真实网络）。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from core.agent.events import ToolCallFinished
from core.agent.runner import Agent
from core.agent.session import ConversationSession
from core.config.settings import SearchConfig, ToolsConfig, load_config
from core.factory import build_tool_registry
from core.llm.types import StreamDelta, ToolCallDelta
from core.tools.builtin.web_search_tools import SEARCH_ENDPOINT, SearchWebTool
from core.tools.registry import ToolRegistry


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    """模拟 requests.Response 的最小接口。"""

    def __init__(
        self,
        status_code: int = 200,
        payload: dict | None = None,
        text: str | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {}, ensure_ascii=False)
        self.closed = False

    def json(self):
        if self._payload is None:
            raise ValueError("response is not JSON")
        return self._payload

    def close(self) -> None:
        self.closed = True


class FakeChatClient:
    """按脚本产出流式增量的离线客户端。"""

    def __init__(self, script: list[list[StreamDelta]]) -> None:
        self._script = list(script)

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, messages, tools=()):
        del messages, tools
        script = self._script.pop(0) if self._script else []
        for delta in script:
            await asyncio.sleep(0)
            yield delta


def _success_payload() -> dict:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "今天北京晴，最高气温 22 摄氏度。",
                },
            }
        ],
        "is_safe": True,
        "references": [
            {
                "content": "天气摘要",
                "date": "2026-10-26",
                "title": "北京天气预报",
                "type": "web",
                "url": "https://example.com/weather",
            },
            {
                "content": "备用来源",
                "title": "北京天气网",
                "type": "web",
                "url": "https://example.com/weather-2",
            },
            {
                "content": "没有链接的条目",
                "title": "无链接",
                "type": "web",
                "url": "",
            },
        ],
        "request_id": "req-test-1",
        "usage": {"completion_tokens": 10, "prompt_tokens": 20, "total_tokens": 30},
    }


class SearchWebToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SearchConfig(
            baidu_qianfan_api_key="test-key",
            model="test-search-model",
            request_timeout=5,
        )
        self.tool = SearchWebTool(self.config)

    def test_success_formats_summary_and_references(self) -> None:
        response = FakeResponse(payload=_success_payload())

        with mock.patch(
            "core.tools.builtin.web_search_tools.requests.post",
            return_value=response,
        ) as post:
            result = run(self.tool.execute({"query": "今天北京天气"}))

        self.assertTrue(result.ok, result.content)
        self.assertIn("今天北京晴", result.content)
        self.assertIn("北京天气预报", result.content)
        self.assertIn("https://example.com/weather", result.content)
        self.assertIn("北京天气网", result.content)
        self.assertNotIn("无链接", result.content)
        self.assertIn("参考来源", result.content)
        self.assertTrue(response.closed)

        self.assertEqual(result.metadata["query"], "今天北京天气")
        self.assertEqual(result.metadata["request_id"], "req-test-1")
        self.assertEqual(len(result.metadata["references"]), 2)
        self.assertEqual(result.metadata["references"][0]["title"], "北京天气预报")
        self.assertEqual(result.metadata["references"][0]["url"], "https://example.com/weather")

        args, kwargs = post.call_args
        self.assertEqual(args[0], SEARCH_ENDPOINT)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        body = kwargs["json"]
        self.assertEqual(
            body["messages"], [{"role": "user", "content": "今天北京天气"}]
        )
        self.assertEqual(body["model"], "test-search-model")
        self.assertEqual(body["search_source"], "baidu_search_v2")
        self.assertEqual(body["search_mode"], "required")
        self.assertFalse(body["stream"])
        self.assertEqual(kwargs["timeout"], 5)

    def test_missing_config_returns_clear_error(self) -> None:
        tool = SearchWebTool(SearchConfig())

        with mock.patch(
            "core.tools.builtin.web_search_tools.requests.post"
        ) as post:
            result = run(tool.execute({"query": "测试"}))

        self.assertFalse(result.ok)
        self.assertIn("未配置百度千帆 API Key", result.content)
        self.assertIn("AGENT_SEARCH_BAIDU_QIANFAN_API_KEY", result.content)
        post.assert_not_called()

    def test_empty_query_is_rejected(self) -> None:
        result = run(self.tool.execute({"query": "   "}))
        self.assertFalse(result.ok)
        self.assertIn("非空的 query", result.content)

    def test_http_error_is_reported(self) -> None:
        response = FakeResponse(
            status_code=401,
            payload={
                "requestId": "req-error",
                "code": 216003,
                "message": "Authentication error: invalid api key",
            },
        )

        with mock.patch(
            "core.tools.builtin.web_search_tools.requests.post",
            return_value=response,
        ):
            result = run(self.tool.execute({"query": "测试"}))

        self.assertFalse(result.ok)
        self.assertIn("HTTP 401", result.content)
        self.assertIn("216003", result.content)
        self.assertIn("Authentication error", result.content)
        self.assertNotIn("test-key", result.content)

    def test_business_error_in_http_200_is_reported(self) -> None:
        response = FakeResponse(
            status_code=200,
            payload={"code": 400, "message": "客户端请求参数错误"},
        )

        with mock.patch(
            "core.tools.builtin.web_search_tools.requests.post",
            return_value=response,
        ):
            result = run(self.tool.execute({"query": "测试"}))

        self.assertFalse(result.ok)
        self.assertIn("code=400", result.content)
        self.assertIn("客户端请求参数错误", result.content)

    def test_network_error_is_reported(self) -> None:
        with mock.patch(
            "core.tools.builtin.web_search_tools.requests.post",
            side_effect=requests.ConnectionError("connection refused"),
        ):
            result = run(self.tool.execute({"query": "测试"}))

        self.assertFalse(result.ok)
        self.assertIn("网络请求失败", result.content)
        self.assertIn("connection refused", result.content)

    def test_invalid_json_is_reported(self) -> None:
        response = FakeResponse(status_code=200, payload=None, text="<html>bad gateway</html>")

        with mock.patch(
            "core.tools.builtin.web_search_tools.requests.post",
            return_value=response,
        ):
            result = run(self.tool.execute({"query": "测试"}))

        self.assertFalse(result.ok)
        self.assertIn("无法解析", result.content)
        self.assertIn("bad gateway", result.content)

    def test_empty_references_are_reported_as_placeholder(self) -> None:
        payload = _success_payload()
        payload["references"] = []

        with mock.patch(
            "core.tools.builtin.web_search_tools.requests.post",
            return_value=FakeResponse(payload=payload),
        ):
            result = run(self.tool.execute({"query": "测试"}))

        self.assertTrue(result.ok, result.content)
        self.assertIn("本次搜索未返回参考来源", result.content)

    def test_registry_exposes_search_web_spec(self) -> None:
        registry = build_tool_registry(
            ToolsConfig(root=Path.cwd()),
            SearchConfig(baidu_qianfan_api_key="test-key"),
        )

        self.assertIn("searchWeb", registry.names())
        spec = next(item for item in registry.specs() if item.name == "searchWeb")
        self.assertIn("query", spec.parameters["properties"])
        self.assertEqual(spec.parameters["required"], ["query"])

    def test_agent_receives_config_error_as_tool_result(self) -> None:
        script = [
            [
                StreamDelta(
                    tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="call_search_1",
                            name="searchWeb",
                            arguments='{"query": "测试"}',
                        )
                    ]
                )
            ],
            [StreamDelta(content="搜索功能暂不可用。")],
        ]
        agent = Agent(
            client=FakeChatClient(script),
            registry=ToolRegistry([SearchWebTool(SearchConfig())]),
            session=ConversationSession(system_prompt="你是助手"),
            max_tool_rounds=3,
        )

        async def scenario():
            return [event async for event in agent.run("搜索一下")]

        events = run(scenario())
        finished = [event for event in events if isinstance(event, ToolCallFinished)][0]
        self.assertFalse(finished.result.ok)
        self.assertIn("未配置百度千帆 API Key", finished.result.content)

        # 工具失败结果仍然会回填会话，模型可以在下一轮向用户解释。
        self.assertEqual(agent.session.messages[-2].role, "tool")
        self.assertIn("未配置", agent.session.messages[-2].content)


class SearchConfigTests(unittest.TestCase):
    _BASE_LLM = (
        "[llm]\n"
        "base_url = http://localhost/v1/chat/completions\n"
        "api_key = test-llm-key\n"
        "model = test-model\n"
    )

    def _load(self, body: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(body, encoding="utf-8")
            return load_config(path)

    def test_search_section_is_loaded(self) -> None:
        config = self._load(
            self._BASE_LLM
            + "\n[search]\n"
            + "baidu_qianfan_api_key = section-key\n"
            + "model = custom-search-model\n"
            + "request_timeout = 12\n"
        )

        self.assertEqual(config.search.baidu_qianfan_api_key, "section-key")
        self.assertEqual(config.search.model, "custom-search-model")
        self.assertEqual(config.search.request_timeout, 12)
    def test_legacy_search_key_in_agent_section_is_loaded(self) -> None:
        config = self._load(
            self._BASE_LLM
            + "\n[agent]\n"
            + "search = legacy-key\n"
        )

        self.assertEqual(config.search.baidu_qianfan_api_key, "legacy-key")

    def test_legacy_search_key_in_search_section_is_loaded(self) -> None:
        config = self._load(
            self._BASE_LLM
            + "\n[search]\n"
            + "search = legacy-key\n"
        )

        self.assertEqual(config.search.baidu_qianfan_api_key, "legacy-key")

    def test_environment_overrides_config(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"AGENT_SEARCH_BAIDU_QIANFAN_API_KEY": "env-key"},
            clear=False,
        ):
            config = self._load(
                self._BASE_LLM
                + "\n[search]\n"
                + "baidu_qianfan_api_key = section-key\n"
            )

        self.assertEqual(config.search.baidu_qianfan_api_key, "env-key")

    def test_missing_key_does_not_break_startup(self) -> None:
        config = self._load(self._BASE_LLM)
        self.assertEqual(config.search.baidu_qianfan_api_key, "")
        self.assertEqual(config.search.model, "ernie-4.5-turbo-32k")
        self.assertEqual(config.search.request_timeout, 60)


if __name__ == "__main__":
    unittest.main()
