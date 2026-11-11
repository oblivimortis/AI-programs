"""网页正文抓取工具：curl 下载 HTML，trafilatura 提取 Markdown。"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, ClassVar, Mapping
from urllib.parse import urlsplit

import trafilatura
from trafilatura.utils import decode_file

from core.tools.base import Tool, ToolResult

#: 单次 curl 请求的最长传输时间（秒）。
CURL_MAX_TIME = 30

#: 建立连接的最长等待时间（秒）。
CURL_CONNECT_TIMEOUT = 10

#: 交给 subprocess 的硬超时，略大于 curl 自身的 --max-time。
CURL_PROCESS_TIMEOUT = CURL_MAX_TIME + 5

#: 最多交给 trafilatura 解析的 HTML 字节数，防止超大页面耗尽内存/Token。
MAX_HTML_BYTES = 8_000_000

#: curl 层允许下载的最大字节数（部分服务器不返回 Content-Length）。
MAX_CURL_FILESIZE = 10_000_000

#: 返回给模型的最大 Markdown 字符数。
MAX_MARKDOWN_CHARS = 20_000

#: 页面标题的最大字符数。
MAX_TITLE_CHARS = 300

#: curl 错误码到中文提示的映射。
CURL_ERROR_HINTS = {
    6: "无法解析主机名",
    7: "无法连接服务器",
    18: "传输提前中断",
    22: "HTTP 错误（4xx/5xx）",
    28: "请求超时",
    35: "TLS 握手失败",
    47: "重定向次数过多",
    52: "服务器返回空响应",
    56: "接收数据失败",
    60: "证书校验失败",
    63: "网页超过大小限制",
}

#: 面向浏览器的 User-Agent，避免部分站点直接拒绝 curl 默认 UA。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 AgentFetchWeb/1.0"
)


class FetchWebError(Exception):
    """带用户可读信息的抓取/解析错误。"""

    def __init__(self, message: str, **metadata: Any) -> None:
        super().__init__(message)
        self.message = message
        self.metadata = metadata


@dataclass(frozen=True)
class FetchedPage:
    """已抓取并提取正文的网页。"""

    url: str
    title: str
    date: str
    author: str
    markdown: str
    html_size: int
    html_truncated: bool
    markdown_truncated: bool


class FetchWebTool(Tool):
    """抓取指定网页并提取正文，转换为 Markdown 返回给模型。"""

    name: ClassVar[str] = "fetchWeb"
    description: ClassVar[str] = (
        "抓取指定网页并提取正文。输入完整的 http/https URL，"
        "返回网页正文的 Markdown（保留标题、段落、列表、表格和链接）"
        "以及原始 URL。当需要阅读搜索结果之外的具体网页内容时使用。"
    )

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的网页完整 URL，必须以 http:// 或 https:// 开头",
                }
            },
            "required": ["url"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            url = self._validate_url(arguments.get("url"))
        except FetchWebError as exc:
            return ToolResult.failure(exc.message)

        try:
            page = await asyncio.to_thread(self._fetch_page, url)
        except FetchWebError as exc:
            return ToolResult.failure(exc.message, url=url, **exc.metadata)
        except Exception as exc:  # noqa: BLE001 - 工具异常必须回传给模型
            return ToolResult.failure(
                f"fetchWeb 执行失败: {type(exc).__name__}: {exc}", url=url
            )

        content = self._compose_content(page)
        return ToolResult.success(
            content,
            url=page.url,
            title=page.title,
            date=page.date,
            author=page.author,
            html_bytes=page.html_size,
            markdown_chars=len(page.markdown),
            html_truncated=page.html_truncated,
            markdown_truncated=page.markdown_truncated,
        )

    # ------------------------------------------------------------------
    # URL 校验
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_url(raw: Any) -> str:
        url = str(raw or "").strip()
        if not url:
            raise FetchWebError("fetchWeb 需要非空的 URL 参数")

        if any(char.isspace() for char in url):
            raise FetchWebError("URL 无效：不能包含空白字符")

        if any(ord(char) < 32 for char in url):
            raise FetchWebError("URL 无效：不能包含控制字符")

        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise FetchWebError(f"URL 无效: {exc}") from exc

        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise FetchWebError(
                "URL 无效：必须是以 http:// 或 https:// 开头的完整网页地址"
            )
        return url

    # ------------------------------------------------------------------
    # 抓取 + 提取
    # ------------------------------------------------------------------
    def _fetch_page(self, url: str) -> FetchedPage:
        html_bytes, html_size, html_truncated = self._download_html(url)

        try:
            html_text = decode_file(html_bytes)
        except Exception as exc:  # noqa: BLE001
            raise FetchWebError(
                f"HTML 解码失败: {type(exc).__name__}: {exc}",
                html_bytes=html_size,
            ) from exc

        if not html_text.strip():
            raise FetchWebError(
                "网页 HTML 为空，无法提取正文。", html_bytes=html_size
            )

        try:
            markdown = trafilatura.extract(
                html_text,
                url=url,
                output_format="markdown",
                include_links=True,
                include_tables=True,
                include_formatting=True,
                include_images=False,
                include_comments=False,
                deduplicate=True,
                favor_recall=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise FetchWebError(
                f"HTML 解析失败: {type(exc).__name__}: {exc}",
                html_bytes=html_size,
            ) from exc

        if not markdown or not markdown.strip():
            raise FetchWebError(
                "未能从网页提取正文。页面可能由 JavaScript 动态渲染、需要登录，"
                "或返回的不是 HTML 内容。",
                html_bytes=html_size,
            )

        markdown = _normalize_markdown(markdown)
        markdown_truncated = len(markdown) > MAX_MARKDOWN_CHARS
        if markdown_truncated:
            markdown = _truncate_markdown(markdown, MAX_MARKDOWN_CHARS)

        title, date, author = self._extract_metadata(html_text, url)
        return FetchedPage(
            url=url,
            title=title,
            date=date,
            author=author,
            markdown=markdown,
            html_size=html_size,
            html_truncated=html_truncated,
            markdown_truncated=markdown_truncated,
        )

    def _download_html(self, url: str) -> tuple[bytes, int, bool]:
        curl_path = shutil.which("curl")
        if not curl_path:
            raise FetchWebError(
                "未找到 curl 可执行文件。请安装 curl，并确保它已加入 PATH。"
            )

        args = [
            curl_path,
            "--location",
            "--silent",
            "--show-error",
            "--fail",
            "--compressed",
            "--globoff",
            "--proto",
            "=http,https",
            "--proto-redir",
            "=http,https",
            "--connect-timeout",
            str(CURL_CONNECT_TIMEOUT),
            "--max-time",
            str(CURL_MAX_TIME),
            "--max-redirs",
            "5",
            "--max-filesize",
            str(MAX_CURL_FILESIZE),
            "--user-agent",
            USER_AGENT,
            "--header",
            "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "--header",
            "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
            "--url",
            url,
        ]

        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                timeout=CURL_PROCESS_TIMEOUT,
                check=False,
            )
        except FileNotFoundError as exc:
            raise FetchWebError(
                "未找到 curl 可执行文件。请安装 curl，并确保它已加入 PATH。"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise FetchWebError(
                f"网页访问超时（超过 {CURL_PROCESS_TIMEOUT} 秒）。"
            ) from exc
        except OSError as exc:
            raise FetchWebError(
                f"启动 curl 失败: {type(exc).__name__}: {exc}"
            ) from exc

        stdout = completed.stdout or b""
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()

        if completed.returncode != 0:
            hint = CURL_ERROR_HINTS.get(completed.returncode, "")
            code_text = f"退出码 {completed.returncode}"
            if hint:
                code_text += f"，{hint}"
            detail = f": {stderr[:500]}" if stderr else ""
            raise FetchWebError(
                f"curl 获取网页失败（{code_text}）{detail}",
                exit_code=completed.returncode,
            )

        if not stdout.strip():
            raise FetchWebError("curl 返回了空内容，无法提取正文。")

        html_size = len(stdout)
        truncated = html_size > MAX_HTML_BYTES
        return stdout[:MAX_HTML_BYTES], html_size, truncated

    @staticmethod
    def _extract_metadata(html_text: str, url: str) -> tuple[str, str, str]:
        try:
            metadata = trafilatura.extract_metadata(html_text, default_url=url)
        except Exception:  # noqa: BLE001 - 元数据不是正文提取的必要条件
            return "", "", ""

        if metadata is None:
            return "", "", ""

        return (
            _clean_metadata(metadata.title),
            _clean_metadata(metadata.date),
            _clean_metadata(metadata.author),
        )

    # ------------------------------------------------------------------
    # 结果整理
    # ------------------------------------------------------------------
    @staticmethod
    def _compose_content(page: FetchedPage) -> str:
        body = page.markdown.strip()
        header_parts: list[str] = []
        if page.title and not _markdown_starts_with_title(body, page.title):
            header_parts.append(f"# {page.title}")
        header_parts.append(f"原始 URL: {page.url}")
        header = "\n\n".join(header_parts)

        content = f"{header}\n\n{body}" if body else header
        notes: list[str] = []
        if page.html_truncated:
            notes.append(
                f"原始 HTML 超过 {MAX_HTML_BYTES} 字节，仅解析了截断后的内容。"
            )
        if page.markdown_truncated:
            notes.append(f"正文超过 {MAX_MARKDOWN_CHARS} 字符，已截断。")
        if notes:
            content += "\n\n> 注意：" + " ".join(notes)
        return content


# ----------------------------------------------------------------------
# 文本辅助函数
# ----------------------------------------------------------------------
def _clean_metadata(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:MAX_TITLE_CHARS]


def _normalize_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate_markdown(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text

    cut = text[:limit]
    newline = cut.rfind("\n")
    if newline >= int(limit * 0.8):
        cut = cut[:newline]
    return cut.rstrip() + f"\n\n…（正文超过 {limit} 字符，已截断）"


def _markdown_starts_with_title(markdown: str, title: str) -> bool:
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            return False

        heading = stripped.lstrip("#").strip()
        # 去掉部分文档站标题末尾的 [¶](...) 锚点。
        heading = re.sub(r"\s*\[[^\]]*\]\([^)]*\)\s*$", "", heading).strip()
        return heading == title
    return False


__all__ = [
    "CURL_PROCESS_TIMEOUT",
    "FetchWebError",
    "FetchWebTool",
    "MAX_HTML_BYTES",
    "MAX_MARKDOWN_CHARS",
]
