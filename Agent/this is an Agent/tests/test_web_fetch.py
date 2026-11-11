"""fetchWeb 工具测试（离线：mock curl，使用真实 trafilatura 解析样例 HTML）。"""

from __future__ import annotations

import asyncio
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from core.config.settings import ToolsConfig
from core.factory import build_tool_registry
from core.tools.builtin.web_fetch_tools import (
    CURL_PROCESS_TIMEOUT,
    FetchWebTool,
)

_SAMPLE_PARAGRAPHS = "".join(
    f'<p>这是第{index}段正文，介绍网页正文提取工具的使用方法，并包含一个'
    f'<a href="https://example.com/{index}">参考链接</a>。'
    "这段文字足够长，用于帮助正文提取算法识别文章主体，而不是导航栏或页脚。</p>"
    for index in range(1, 8)
)

SAMPLE_HTML = f"""<!doctype html>
<html><head>
<title>网页正文提取测试</title>
<meta name="author" content="测试作者">
<meta name="date" content="2026-10-26">
</head><body>
<nav><a href="/">首页</a><a href="/ads">广告</a></nav>
<header><h1>站点标题</h1></header>
<main><article>
<h1>网页正文提取测试</h1>
<p>这是文章导语，介绍如何使用 trafilatura 提取网页正文并转换为 Markdown。</p>
<h2>一、基本用法</h2>
{_SAMPLE_PARAGRAPHS}
<h2>二、列表与表格</h2>
<ul><li>保留标题</li><li>保留段落</li><li>保留链接</li></ul>
<ol><li>第一步</li><li>第二步</li></ol>
<table><caption>示例数据</caption><thead><tr><th>名称</th><th>数值</th></tr></thead>
<tbody><tr><td>A</td><td>1</td></tr><tr><td>B</td><td>2</td></tr></tbody></table>
<blockquote><p>引用内容。</p></blockquote>
</article></main>
<aside>侧边栏推荐</aside>
<footer>版权所有 页脚</footer>
</body></html>"""


def run(coro):
    return asyncio.run(coro)


def _completed(
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["curl"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class FetchWebToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = FetchWebTool()
        self.url = "https://example.com/article"

    def _patch_curl(self, completed: subprocess.CompletedProcess):
        return (
            mock.patch(
                "core.tools.builtin.web_fetch_tools.shutil.which",
                return_value="curl.exe",
            ),
            mock.patch(
                "core.tools.builtin.web_fetch_tools.subprocess.run",
                return_value=completed,
            ),
        )

    def test_success_extracts_markdown_and_removes_boilerplate(self) -> None:
        which_patch, run_patch = self._patch_curl(_completed(stdout=SAMPLE_HTML.encode("utf-8")))

        with which_patch, run_patch as run_mock:
            result = run(self.tool.execute({"url": self.url}))

        self.assertTrue(result.ok, result.content)

        # 正文结构：标题、段落、列表、表格、链接。
        self.assertIn("# 网页正文提取测试", result.content)
        self.assertIn("## 一、基本用法", result.content)
        self.assertIn("[参考链接](https://example.com/1)", result.content)
        self.assertIn("- 保留标题", result.content)
        self.assertIn("| 名称 | 数值 |", result.content)
        self.assertIn("引用内容。", result.content)

        # 原始 URL，并且标题不重复。
        self.assertIn(f"原始 URL: {self.url}", result.content)
        self.assertEqual(result.content.count("# 网页正文提取测试"), 1)

        # 导航、广告、侧边栏、页脚不应进入正文。
        self.assertNotIn("广告", result.content)
        self.assertNotIn("侧边栏推荐", result.content)
        self.assertNotIn("版权所有 页脚", result.content)

        self.assertEqual(result.metadata["url"], self.url)
        self.assertEqual(result.metadata["title"], "网页正文提取测试")
        self.assertGreater(result.metadata["markdown_chars"], 0)
        self.assertFalse(result.metadata["html_truncated"])
        self.assertFalse(result.metadata["markdown_truncated"])

        # subprocess 必须使用参数列表，且不能启用 shell。
        args, kwargs = run_mock.call_args
        command = args[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], "curl.exe")
        self.assertIn("--url", command)
        self.assertEqual(command[command.index("--url") + 1], self.url)
        self.assertNotIn("shell", kwargs)
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["timeout"], CURL_PROCESS_TIMEOUT)

    def test_invalid_urls_are_rejected_without_running_curl(self) -> None:
        invalid_urls = [
            "",
            "example.com",
            "file:///etc/passwd",
            "ftp://example.com/file",
            "https://",
            "https://example.com/a b",
            "https://[::1",
        ]

        for url in invalid_urls:
            with self.subTest(url=url):
                with mock.patch(
                    "core.tools.builtin.web_fetch_tools.subprocess.run"
                ) as run_mock:
                    result = run(self.tool.execute({"url": url}))
                self.assertFalse(result.ok)
                self.assertIn("URL", result.content)
                run_mock.assert_not_called()

    def test_missing_curl_is_reported(self) -> None:
        with mock.patch(
            "core.tools.builtin.web_fetch_tools.shutil.which",
            return_value=None,
        ), mock.patch(
            "core.tools.builtin.web_fetch_tools.subprocess.run"
        ) as run_mock:
            result = run(self.tool.execute({"url": self.url}))

        self.assertFalse(result.ok)
        self.assertIn("未找到 curl", result.content)
        run_mock.assert_not_called()

    def test_curl_timeout_is_reported(self) -> None:
        with mock.patch(
            "core.tools.builtin.web_fetch_tools.shutil.which",
            return_value="curl.exe",
        ), mock.patch(
            "core.tools.builtin.web_fetch_tools.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="curl", timeout=CURL_PROCESS_TIMEOUT),
        ):
            result = run(self.tool.execute({"url": self.url}))

        self.assertFalse(result.ok)
        self.assertIn("超时", result.content)
        self.assertIn(str(CURL_PROCESS_TIMEOUT), result.content)

    def test_curl_http_error_is_reported(self) -> None:
        completed = _completed(
            returncode=22,
            stderr=b"The requested URL returned error: 404",
        )

        which_patch, run_patch = self._patch_curl(completed)
        with which_patch, run_patch:
            result = run(self.tool.execute({"url": self.url}))

        self.assertFalse(result.ok)
        self.assertIn("退出码 22", result.content)
        self.assertIn("HTTP 错误", result.content)
        self.assertIn("404", result.content)
        self.assertEqual(result.metadata["exit_code"], 22)

    def test_curl_timeout_exit_code_is_reported(self) -> None:
        completed = _completed(returncode=28, stderr=b"Operation timed out")

        which_patch, run_patch = self._patch_curl(completed)
        with which_patch, run_patch:
            result = run(self.tool.execute({"url": self.url}))

        self.assertFalse(result.ok)
        self.assertIn("退出码 28", result.content)
        self.assertIn("请求超时", result.content)

    def test_empty_html_is_reported(self) -> None:
        which_patch, run_patch = self._patch_curl(_completed(stdout=b"  \n"))
        with which_patch, run_patch:
            result = run(self.tool.execute({"url": self.url}))

        self.assertFalse(result.ok)
        self.assertIn("空内容", result.content)

    def test_html_parse_failure_is_reported(self) -> None:
        which_patch, run_patch = self._patch_curl(
            _completed(stdout=SAMPLE_HTML.encode("utf-8"))
        )
        with which_patch, run_patch, mock.patch(
            "core.tools.builtin.web_fetch_tools.trafilatura.extract",
            side_effect=ValueError("bad html"),
        ):
            result = run(self.tool.execute({"url": self.url}))

        self.assertFalse(result.ok)
        self.assertIn("HTML 解析失败", result.content)
        self.assertIn("bad html", result.content)

    def test_no_extractable_content_is_reported(self) -> None:
        which_patch, run_patch = self._patch_curl(
            _completed(stdout=SAMPLE_HTML.encode("utf-8"))
        )
        with which_patch, run_patch, mock.patch(
            "core.tools.builtin.web_fetch_tools.trafilatura.extract",
            return_value=None,
        ):
            result = run(self.tool.execute({"url": self.url}))

        self.assertFalse(result.ok)
        self.assertIn("未能从网页提取正文", result.content)

    def test_long_markdown_is_truncated(self) -> None:
        which_patch, run_patch = self._patch_curl(
            _completed(stdout=SAMPLE_HTML.encode("utf-8"))
        )
        with which_patch, run_patch, mock.patch(
            "core.tools.builtin.web_fetch_tools.MAX_MARKDOWN_CHARS", 120
        ):
            result = run(self.tool.execute({"url": self.url}))

        self.assertTrue(result.ok, result.content)
        self.assertTrue(result.metadata["markdown_truncated"])
        self.assertIn("已截断", result.content)
        self.assertIn(f"原始 URL: {self.url}", result.content)

    def test_registry_exposes_fetch_web_spec(self) -> None:
        registry = build_tool_registry(ToolsConfig(root=Path.cwd()))
        self.assertIn("fetchWeb", registry.names())

        spec = next(item for item in registry.specs() if item.name == "fetchWeb")
        self.assertEqual(spec.parameters["required"], ["url"])
        self.assertIn("url", spec.parameters["properties"])


if __name__ == "__main__":
    unittest.main()
