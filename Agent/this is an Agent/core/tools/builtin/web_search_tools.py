"""网络搜索工具：调用百度千帆「智能搜索生成」API。

API 文档：https://cloud.baidu.com/doc/qianfan-api/s/Hmbu8m06u

工具只负责一次搜索请求，并把结果整理成 LLM 容易消化的文本：
    * 模型生成的搜索摘要；
    * 参考来源的标题与 URL。

API Key 来自 ``config.ini`` 的 ``[search]`` 小节，不在代码中硬编码。
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Mapping

import requests

from core.config.settings import SearchConfig
from core.tools.base import Tool, ToolResult

#: 百度千帆智能搜索生成接口地址。
SEARCH_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/chat/completions"

#: 返回给模型的摘要最大字符数，避免单次工具结果过长。
MAX_SUMMARY_CHARS = 6_000

#: 最多保留的参考来源条数。
MAX_REFERENCES = 10

#: 错误响应正文最多保留的字符数。
MAX_ERROR_BODY_CHARS = 500


class SearchWebTool(Tool):
    """搜索互联网并返回摘要与参考来源。"""

    name: ClassVar[str] = "searchWeb"
    description: ClassVar[str] = (
        "搜索互联网获取最新信息。输入搜索关键词或完整问题（query），"
        "返回智能搜索摘要以及参考来源的标题和 URL。"
        "当用户询问实时新闻、最新数据、当前事件，或需要核实时效性信息时使用。"
    )

    def __init__(self, config: SearchConfig) -> None:
        self._config = config

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或完整问题，例如「2026 年诺贝尔物理学奖」",
                }
            },
            "required": ["query"],
        }

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        query = str(arguments.get("query", "") or "").strip()
        if not query:
            return ToolResult.failure("searchWeb 需要非空的 query 参数")

        api_key = self._normalize_api_key(self._config.baidu_qianfan_api_key)
        if not api_key:
            return ToolResult.failure(
                "未配置百度千帆 API Key，无法使用网络搜索。"
                " 请在 config.ini 的 [search] 小节设置 baidu_qianfan_api_key，"
                "或设置环境变量 AGENT_SEARCH_BAIDU_QIANFAN_API_KEY。",
                query=query,
            )

        payload = self._build_payload(query)

        try:
            status, data, raw_text = await asyncio.to_thread(
                self._request, payload, api_key
            )
        except requests.RequestException as exc:
            return ToolResult.failure(
                f"网络请求失败（searchWeb）: {type(exc).__name__}: {exc}",
                query=query,
            )
        except Exception as exc:  # noqa: BLE001 - 工具异常必须回传给模型
            return ToolResult.failure(
                f"searchWeb 执行失败: {type(exc).__name__}: {exc}",
                query=query,
            )

        if status >= 400:
            return ToolResult.failure(
                self._format_error(status, data, raw_text), query=query
            )

        if data is None:
            return ToolResult.failure(
                "搜索服务返回了无法解析的响应"
                f"（HTTP {status}）: {raw_text[:MAX_ERROR_BODY_CHARS]}",
                query=query,
            )

        api_error = self._extract_api_error(data)
        if api_error is not None:
            code, message = api_error
            return ToolResult.failure(
                f"搜索服务返回错误（HTTP {status}，code={code}）: {message}",
                query=query,
            )

        summary = self._extract_summary(data)
        references = self._extract_references(data.get("references"))

        if not summary and not references:
            return ToolResult.failure(
                "搜索完成，但服务未返回摘要或参考来源。",
                query=query,
                request_id=data.get("request_id") or data.get("requestId"),
            )

        content = self._format_result(summary, references)
        return ToolResult.success(
            content,
            query=query,
            references=references,
            request_id=data.get("request_id") or data.get("requestId"),
            usage=data.get("usage") or {},
        )

    # ------------------------------------------------------------------
    # 请求
    # ------------------------------------------------------------------
    def _build_payload(self, query: str) -> dict[str, Any]:
        return {
            "messages": [{"role": "user", "content": query}],
            "model": self._config.model,
            "stream": False,
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": MAX_REFERENCES}],
            "search_mode": "required",
            "enable_followup_queries": False,
            "enable_corner_markers": True,
        }

    def _request(
        self, payload: dict[str, Any], api_key: str
    ) -> tuple[int, dict[str, Any] | None, str]:
        """在线程中执行同步 HTTP 请求，避免阻塞事件循环。"""
        response = requests.post(
            SEARCH_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=self._config.request_timeout,
        )
        try:
            raw_text = response.text or ""
            try:
                data = response.json()
            except ValueError:
                data = None
            return response.status_code, data if isinstance(data, dict) else None, raw_text
        finally:
            response.close()

    @staticmethod
    def _normalize_api_key(raw: str) -> str:
        key = (raw or "").strip()
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
        if len(key) >= 2 and key[0] == key[-1] and key[0] in {"'", '"'}:
            key = key[1:-1].strip()
        return key

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_api_error(data: dict[str, Any]) -> tuple[Any, str] | None:
        """识别 HTTP 200 但业务码表示失败的情况。"""
        code = data.get("code")
        if code in (None, 0, "0"):
            return None

        message = data.get("message") or data.get("msg") or "未知错误"
        return code, str(message)

    @staticmethod
    def _format_error(
        status: int, data: dict[str, Any] | None, raw_text: str
    ) -> str:
        if data:
            code = data.get("code") or data.get("error_code")
            message = (
                data.get("message")
                or data.get("msg")
                or data.get("error_msg")
                or data.get("error")
            )
            if code is not None or message:
                return (
                    f"搜索服务返回错误（HTTP {status}"
                    + (f"，code={code}" if code is not None else "")
                    + f"）: {message or '未知错误'}"
                )
        body = (raw_text or "").strip()
        if body:
            return (
                f"搜索服务返回错误（HTTP {status}）: "
                f"{body[:MAX_ERROR_BODY_CHARS]}"
            )
        return f"搜索服务返回错误（HTTP {status}），响应体为空。"

    @staticmethod
    def _extract_summary(data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message") or {}
        if not isinstance(message, dict):
            return ""
        return str(message.get("content") or "").strip()

    @staticmethod
    def _extract_references(raw_references: Any) -> list[dict[str, str]]:
        if isinstance(raw_references, dict):
            raw_references = [raw_references]
        if not isinstance(raw_references, list):
            return []

        references: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for item in raw_references:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            references.append(
                {
                    "title": str(item.get("title") or "").strip() or "(无标题)",
                    "url": url,
                    "date": str(item.get("date") or "").strip(),
                    "type": str(item.get("type") or "").strip(),
                }
            )
            if len(references) >= MAX_REFERENCES:
                break
        return references

    # ------------------------------------------------------------------
    # 结果整理
    # ------------------------------------------------------------------
    @staticmethod
    def _format_result(summary: str, references: list[dict[str, str]]) -> str:
        if summary:
            if len(summary) > MAX_SUMMARY_CHARS:
                summary = (
                    summary[:MAX_SUMMARY_CHARS].rstrip()
                    + "\n…（摘要过长，已截断）"
                )
            summary_text = summary
        else:
            summary_text = "（本次搜索未返回摘要）"

        if references:
            lines = []
            for index, item in enumerate(references, start=1):
                title = item["title"]
                date = item.get("date", "")
                suffix = f"（{date}）" if date else ""
                lines.append(f"{index}. {title}{suffix}\n   {item['url']}")
            references_text = "\n".join(lines)
        else:
            references_text = "（本次搜索未返回参考来源）"

        return (
            "搜索摘要：\n"
            f"{summary_text}\n\n"
            "参考来源：\n"
            f"{references_text}"
        )


__all__ = [
    "MAX_REFERENCES",
    "MAX_SUMMARY_CHARS",
    "SEARCH_ENDPOINT",
    "SearchWebTool",
]
