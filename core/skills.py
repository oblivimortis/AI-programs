"""技能（Agent Skills）发现与按需加载。

技能位于 ``<skills_root>/<skill_name>/SKILL.md``。Agent Skills 的核心约定是
**渐进式加载**：

    1. 初始化只读取 YAML Front Matter 中的 ``name`` 和 ``description``；
    2. 系统提示词中只放技能清单，以及 ``loadSkill`` 的使用说明；
    3. 模型真正需要某个技能时，才调用 ``loadSkill`` 读取正文或附加文件。

一个 ``skills_root`` 既可以是一个“技能集合目录”（下面有多个
``<skill_name>/SKILL.md``），也可以直接是一个技能目录（自身包含
``SKILL.md``）。同时支持配置多个根目录（用逗号、分号或换行分隔），
因此可以把散落在不同文件夹里的技能一起发现，而不需要搬动文件。

设计要点：
    * 不引入任何第三方 YAML 依赖，容忍常见的 Front Matter 写法差异
      （``---`` / ``\\---``、半角/全角冒号、中英文引号、``|`` / ``>`` 块文本）；
    * 元数据发现只读到结束分隔线为止，不把正文载入内存；
    * 附加文件读取严格限制在技能目录内，并处理同名文件歧义；
    * 该模块不依赖任何 UI 框架，前端与内核都可以复用。
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

#: 每个技能目录中的入口文件名。
SKILL_FILENAME = "SKILL.md"

#: Front Matter 分隔线：``---`` 或 ``\---``。
_DELIMITER_RE = re.compile(r"^\\?-{3,}$")

#: 解析 Front Matter 时允许被去掉的引号（含中文全角引号）。
_QUOTES = "\"'“”‘’"

#: 多个技能根目录之间的分隔符：逗号、分号或换行。
#: 不复用 ``os.pathsep``，因为 POSIX 下它是 ``:``，会误伤 Windows 盘符。
_ROOT_SPLIT_RE = re.compile(r"[,;\r\n]+")

#: 技能目录名规范：小写字母、数字和连字符，不能以连字符开头或结尾。
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: 技能目录名的最大长度，避免生成过深的路径。
MAX_SKILL_NAME_LENGTH = 64


@dataclass(frozen=True)
class Skill:
    """一个已解析的技能。"""

    name: str
    description: str
    version: str
    body: str
    path: Path

    @property
    def identifier(self) -> str:
        """稳定的技能标识：优先用目录名，缺失时退回标题。"""
        return self.path.parent.name if self.path else self.name


def _clean(value: str) -> str:
    return value.strip().strip(_QUOTES).strip()


def _split_meta(line: str) -> tuple[str, str] | None:
    """拆分 ``key: value``，同时兼容全角冒号。"""
    for separator in (":", "："):
        key, found, value = line.partition(separator)
        if found:
            key = key.strip().lower()
            if key:
                return key, _clean(value)
    return None


def parse_skill_text(
    text: str,
    *,
    fallback_name: str = "",
    path: Path | None = None,
) -> Skill:
    """解析 SKILL.md 文本。

    Args:
        text: 文件内容。
        fallback_name: Front Matter 没有 ``name`` 时使用的名称（通常是目录名）。
        path: 技能文件路径，仅用于标识和排错。
    """
    lines = text.splitlines()

    meta: dict[str, str] = {}
    body_lines = lines

    start = next(
        (index for index, line in enumerate(lines) if _DELIMITER_RE.match(line.strip())),
        None,
    )
    if start is not None:
        end = next(
            (
                index
                for index in range(start + 1, len(lines))
                if _DELIMITER_RE.match(lines[index].strip())
            ),
            None,
        )
        if end is not None:
            for raw in lines[start + 1 : end]:
                if not raw.strip():
                    continue
                pair = _split_meta(raw)
                if pair is not None:
                    meta[pair[0]] = pair[1]
            body_lines = lines[end + 1 :]

    body = "\n".join(body_lines).strip()
    return Skill(
        name=_clean(meta.get("name", "")) or fallback_name,
        description=_clean(meta.get("description", "")),
        version=_clean(meta.get("version", "")),
        body=body,
        path=path or Path(SKILL_FILENAME),
    )


def split_skill_roots(
    value: str | Path | Iterable[str | Path],
) -> tuple[Path, ...]:
    """把配置里的技能根目录拆成路径元组。

    Args:
        value: 单个路径、用逗号/分号/换行分隔的字符串，或路径序列。

    Returns:
        去空、去重（保持首次出现顺序）后的路径元组。
    """
    raw_items: list[str | Path] = []
    if isinstance(value, str):
        raw_items.extend(part for part in _ROOT_SPLIT_RE.split(value) if part.strip())
    elif isinstance(value, Path):
        raw_items.append(value)
    else:
        for item in value:
            raw_items.extend(split_skill_roots(item))

    roots: list[Path] = []
    seen: set[str] = set()
    for item in raw_items:
        text = item.strip() if isinstance(item, str) else os.fspath(item)
        if not text:
            continue
        path = Path(text).expanduser()
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        roots.append(path)
    return tuple(roots)


def _iter_skill_files(root: Path) -> Iterable[Path]:
    """列出某个根目录下的 SKILL.md。

    同时兼容两种布局：
        * 技能集合目录：``<root>/<skill>/SKILL.md``；
        * 单个技能目录：``<root>/SKILL.md``。
    """
    direct = root / SKILL_FILENAME
    if direct.is_file():
        yield direct
    yield from sorted(root.glob(f"*/{SKILL_FILENAME}"))


def discover_skills(
    roots: str | Path | Iterable[str | Path],
) -> list[SkillMetadata]:
    """扫描技能根目录，只返回轻量元数据（name / description）。

    这是动态加载机制的入口：初始化阶段不会读取 SKILL.md 正文。
    需要正文时调用 :class:`SkillLibrary` 的 ``load`` / ``read_skill``。
    """
    return discover_skill_metadata(roots)


def format_skills_prompt(skills: Iterable[SkillMetadata]) -> str:
    """把技能元数据渲染成系统提示词中的技能清单。"""
    return format_skill_catalog(skills)


def apply_skills(system_prompt: str, skills: Iterable[SkillMetadata]) -> str:
    """把技能清单追加到系统提示词末尾；没有技能时原样返回。"""
    return apply_skill_catalog(system_prompt, skills)


#: 技能文件指纹：(绝对路径, 内容短哈希)。
#: 使用内容哈希而不是 mtime，避免“同一时间戳内保存、且文件大小不变”漏检。
SkillFingerprint = tuple[tuple[str, str], ...]


def skills_fingerprint(
    roots: str | Path | Iterable[str | Path],
) -> SkillFingerprint:
    """计算技能文件的指纹，用于判断是否需要热重载。

    指纹包含每个 ``SKILL.md`` 的绝对路径和内容哈希：
    新增/删除技能会改变路径集合，修改内容会改变哈希。
    技能文件通常很小，因此每轮读取一次内容仍然很廉价。
    """
    entries: list[tuple[str, str]] = []
    for root in split_skill_roots(roots):
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for skill_file in _iter_skill_files(root_path):
            try:
                data = skill_file.read_bytes()
            except OSError:
                continue
            digest = hashlib.blake2b(data, digest_size=8).hexdigest()
            entries.append((os.path.normcase(str(skill_file.resolve())), digest))
    return tuple(entries)


class SkillRuntime:
    """技能运行时：元数据发现 + 系统提示词热重载。

    与 :class:`SkillLibrary` 相同，初始化只读取 name / description；
    :meth:`refresh_prompt` 可在每轮对话前重新扫描元数据。技能正文仍然
    只在模型调用 ``loadSkill`` 时读取。

    需要立即感知文件变化时，可以在外部用 watchdog 监听目录，然后调用
    :meth:`refresh`。
    """

    def __init__(
        self,
        base_prompt: str,
        roots: str | Path | Iterable[str | Path],
        *,
        enabled: bool = True,
        project_root: str | Path | None = None,
    ) -> None:
        self._base_prompt = base_prompt
        self._lock = threading.RLock()
        self._library = SkillLibrary(project_root, roots, enabled=enabled)
        self._prompt = apply_skill_catalog(base_prompt, self._library.skills)

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------
    @property
    def roots(self) -> tuple[Path, ...]:
        return self._library.roots

    @property
    def skills(self) -> tuple[SkillMetadata, ...]:
        return self._library.skills

    @property
    def library(self) -> SkillLibrary:
        return self._library

    @property
    def prompt(self) -> str:
        """当前系统提示词（基础提示词 + 技能清单）。"""
        return self._prompt

    @property
    def fingerprint(self) -> SkillFingerprint:
        """技能文件指纹（兼容旧接口，仅在被显式读取时计算）。"""
        return skills_fingerprint(self._library.roots)

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------
    def refresh(self, *, force: bool = False) -> bool:
        """重新扫描技能元数据，必要时重建系统提示词。

        Args:
            force: 保留参数以兼容旧调用；元数据扫描本身很轻量。

        Returns:
            系统提示词是否真的发生了变化（内容相同则返回 ``False``）。
        """
        del force
        with self._lock:
            self._library.refresh()
            prompt = apply_skill_catalog(self._base_prompt, self._library.skills)
            changed = prompt != self._prompt
            self._prompt = prompt
            return changed

    def refresh_prompt(self) -> str:
        """刷新后返回最新提示词（供 Agent 在每轮对话前调用）。"""
        self.refresh()
        return self._prompt


def resolve_skills_roots(
    roots: str | Path | Iterable[str | Path],
    base: str | Path,
) -> tuple[Path, ...]:
    """解析多个技能根目录：相对路径以 ``base``（通常是工作区根目录）为基准。

    解析后会按绝对路径再次去重，``a/../a`` 这类写法不会被加载两遍。
    """
    base_path = Path(base).expanduser()
    resolved: list[Path] = []
    seen: set[str] = set()
    for root in split_skill_roots(roots):
        path = root if root.is_absolute() else base_path / root
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return tuple(resolved)


def resolve_skills_root(root: str | Path, base: str | Path) -> Path:
    """解析单个技能根目录（兼容旧调用）；多根目录请用 :func:`resolve_skills_roots`。"""
    roots = resolve_skills_roots(root, base)
    return roots[0] if roots else Path(base).expanduser()


# ======================================================================
# Agent Skills：元数据发现 + 按需加载
# ======================================================================
#
# 下面这部分实现的是“动态技能”机制：
#     * 初始化时只读取 ``SKILL.md`` 的 YAML Front Matter（name / description）；
#     * 系统提示词中只放技能清单和 loadSkill 的使用说明；
#     * 完整正文和附加文件只在模型调用 loadSkill 时才读取。
#
# 旧的 ``Skill`` / ``discover_skills`` / ``SkillRuntime`` 仍然保留，供已有
# 集成和实验使用；新代码应优先使用 ``SkillMetadata`` / ``SkillLibrary``。

#: Agent Skills 约定的项目内技能目录（相对于程序启动时的工作目录）。
DEFAULT_SKILLS_DIR = Path(".agents") / "skills"


@dataclass(frozen=True)
class SkillMetadata:
    """一个技能的轻量元数据（不包含 SKILL.md 正文）。"""

    name: str
    description: str
    path: Path
    version: str = ""

    @property
    def identifier(self) -> str:
        """稳定的技能标识：优先用目录名。"""
        return self.path.parent.name if self.path.parent.name else self.name

    @property
    def directory(self) -> Path:
        """技能目录；所有附加文件都必须位于该目录内。"""
        return self.path.parent

    def read_full_text(self) -> str:
        """读取 SKILL.md 全文（仅在显式需要时调用）。"""
        return self.path.read_text(encoding="utf-8-sig", errors="replace")


def _dedent_block(lines: list[str]) -> list[str]:
    """去掉 YAML 块内容的最小公共缩进，同时保留相对缩进。"""
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    cut = min(indents) if indents else 0
    return [line[cut:] if len(line) >= cut else "" for line in lines]


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, str]:
    """解析 Front Matter 的顶层 ``key: value``。

    只实现 Agent Skills 需要的 YAML 子集，但覆盖常见写法：
    引号、全角冒号、注释、``|`` / ``>`` 块文本。不引入 PyYAML 依赖。
    """
    meta: dict[str, str] = {}
    index = 0
    total = len(lines)

    while index < total:
        raw = lines[index]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue

        # 顶层键必须从第 0 列开始；缩进行属于上一个块文本。
        if raw[:1].isspace():
            index += 1
            continue

        pair = _split_meta(raw)
        if pair is None:
            index += 1
            continue

        key, value = pair
        if value in {"|", "|-", "|+", ">", ">-", ">+"}:
            style = value[0]
            block: list[str] = []
            index += 1
            while index < total:
                line = lines[index]
                if line.strip() and not line[:1].isspace():
                    break
                block.append(line)
                index += 1
            block = _dedent_block(block)
            if style == "|":
                value = "\n".join(block).strip("\n")
            else:
                paragraphs: list[str] = []
                current: list[str] = []
                for line in block:
                    if line.strip():
                        current.append(line.strip())
                    elif current:
                        paragraphs.append(" ".join(current))
                        current = []
                if current:
                    paragraphs.append(" ".join(current))
                value = "\n".join(paragraphs).strip()
        else:
            index += 1

        meta[key] = value

    return meta


def read_skill_frontmatter(path: str | Path) -> dict[str, str]:
    """只读取 SKILL.md 开头的 YAML Front Matter。

    读取会在结束分隔线处停止，不会把正文读入内存；文件不存在、没有
    Front Matter 或读取失败时返回空字典。
    """
    skill_path = Path(path)
    try:
        with skill_path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            first = handle.readline()
            if not first:
                return {}

            # 允许 BOM 之外的少量空行；正常情况第一行就是分隔线。
            line = first
            while line and not line.strip():
                line = handle.readline()

            if not _DELIMITER_RE.match(line.strip()):
                return {}

            body: list[str] = []
            for raw in handle:
                text = raw.rstrip("\r\n")
                if _DELIMITER_RE.match(text.strip()):
                    return _parse_frontmatter_lines(body)
                body.append(text)
    except (OSError, UnicodeError):
        return {}
    return {}


def _skill_from_metadata_file(skill_file: Path) -> SkillMetadata | None:
    """从单个 SKILL.md 构建元数据；name / description 缺失时返回 None。"""
    meta = read_skill_frontmatter(skill_file)
    name = _clean(meta.get("name", ""))
    description = _clean(meta.get("description", ""))
    if not name or not description:
        return None
    return SkillMetadata(
        name=name,
        description=description,
        path=skill_file,
        version=_clean(meta.get("version", "")),
    )


def discover_skill_metadata(
    roots: str | Path | Iterable[str | Path],
) -> list[SkillMetadata]:
    """扫描技能根目录，只返回 Front Matter 中的轻量元数据。

    只识别 ``<root>/<skill_name>/SKILL.md``（同时兼容根目录本身就是单个
    技能目录的写法）。缺少 ``name`` 或 ``description`` 的技能会被跳过，
    因为它们是 loadSkill 定位技能和注入系统提示词所必需的字段。
    """
    skills: list[SkillMetadata] = []
    seen_files: set[str] = set()
    seen_names: set[str] = set()

    for root in split_skill_roots(roots):
        root_path = Path(root).expanduser()
        if not root_path.is_dir():
            continue
        root_path = root_path.resolve()

        candidates: list[Path] = []
        direct = root_path / SKILL_FILENAME
        if direct.is_file():
            candidates.append(direct)
        candidates.extend(sorted(root_path.glob(f"*/{SKILL_FILENAME}")))

        for skill_file in candidates:
            key = os.path.normcase(str(skill_file.resolve()))
            if key in seen_files:
                continue
            seen_files.add(key)

            skill = _skill_from_metadata_file(skill_file)
            if skill is None:
                continue

            # 同名技能只保留第一次发现的，避免系统提示词里出现重复条目。
            name_key = skill.name.casefold()
            if name_key in seen_names:
                continue
            seen_names.add(name_key)
            skills.append(skill)

    skills.sort(key=lambda item: (item.name.casefold(), item.name, str(item.path)))
    return skills


def format_skill_catalog(skills: Iterable[SkillMetadata]) -> str:
    """把技能元数据渲染成系统提示词中的“技能清单”。"""
    items = list(skills)
    if not items:
        return ""

    lines = ["You have access to the following skills:"]
    for skill in items:
        description = " ".join(skill.description.split())
        lines.append(f"- {skill.name}: {description}")
    lines.append("")
    lines.append(
        "Use the 'loadSkill' tool to load the full instructions of a skill when needed. "
        "You can also load additional files referenced by a skill."
    )
    return "\n".join(lines)


def apply_skill_catalog(system_prompt: str, skills: Iterable[SkillMetadata]) -> str:
    """把技能清单追加到系统提示词末尾；没有技能时原样返回。"""
    block = format_skill_catalog(skills)
    if not block:
        return system_prompt
    return f"{system_prompt.rstrip()}\n\n{block}\n"


class SkillError(Exception):
    """技能加载相关的基类异常。"""


class SkillNotFoundError(SkillError, LookupError):
    """找不到指定名称的技能。"""

    def __init__(self, name: str, available: Iterable[str] = ()) -> None:
        self.name = name
        self.available = tuple(available)
        hint = f"可用技能: {', '.join(self.available)}" if self.available else "当前没有可用技能"
        super().__init__(f"未找到技能: {name}（{hint}）")


class SkillFileNotFoundError(SkillError, FileNotFoundError):
    """技能目录内找不到指定文件。"""


class SkillFileAmbiguousError(SkillError, LookupError):
    """文件名匹配到多个文件，需要模型提供更精确的路径。"""

    def __init__(self, filename: str, candidates: Iterable[Path]) -> None:
        self.filename = filename
        self.candidates = tuple(candidates)
        super().__init__(f"文件名不唯一: {filename}，共找到 {len(self.candidates)} 个文件")


class SkillPathViolationError(SkillError, PermissionError):
    """路径解析后越出了技能目录。"""


class SkillNameError(SkillError, ValueError):
    """技能名称不符合目录名规范。"""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"技能名称不合法: {name!r}；只能使用小写字母、数字和连字符，"
            "且不能以连字符开头或结尾（例如 my-skill、pdf-tools）"
        )


@dataclass(frozen=True)
class SkillLoadResult:
    """一次 loadSkill 调用的结果。"""

    ok: bool
    content: str = ""
    path: Path | None = None
    skill: SkillMetadata | None = None
    error: str = ""
    candidates: tuple[Path, ...] = ()


@dataclass(frozen=True)
class SkillCreateResult:
    """一次 createSkill 调用的结果。"""

    ok: bool
    name: str = ""
    path: Path | None = None
    directory: Path | None = None
    error: str = ""
    #: 本次是否真的新建了文件。
    created: bool = False
    #: 创建后技能清单是否发生了变化（用于决定是否需要刷新系统提示词）。
    refreshed: bool = False
    #: 工具对传入内容的规范化说明（例如自动补上了 name 字段）。
    notes: tuple[str, ...] = ()


def normalize_skill_name(name: str) -> str:
    """校验技能目录名并返回规范化结果。

    Args:
        name: 模型给出的技能名称。

    Returns:
        去掉首尾空白后的名称。

    Raises:
        SkillNameError: 名称不符合 ``^[a-z0-9]+(?:-[a-z0-9]+)*$`` 或超长。
    """
    candidate = (name or "").strip()
    if (
        not candidate
        or len(candidate) > MAX_SKILL_NAME_LENGTH
        or not _SKILL_NAME_RE.match(candidate)
    ):
        raise SkillNameError(candidate or name or "")
    return candidate


def _frontmatter_span(lines: list[str]) -> tuple[int, int] | None:
    """返回 YAML Front Matter 的 ``(开始分隔线, 结束分隔线)`` 行号。"""
    start = next(
        (index for index, line in enumerate(lines) if _DELIMITER_RE.match(line.strip())),
        None,
    )
    if start is None:
        return None
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if _DELIMITER_RE.match(lines[index].strip())
        ),
        None,
    )
    if end is None:
        return None
    return start, end


def _replace_frontmatter_key(
    lines: list[str], start: int, end: int, key: str, value: str
) -> bool:
    """把 Front Matter 中某个顶层键改成新值；没有该键时返回 ``False``。"""
    for index in range(start + 1, end):
        pair = _split_meta(lines[index])
        if pair is not None and pair[0] == key.casefold():
            lines[index] = f"{key}: {value}"
            return True
    return False


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


class SkillLibrary:
    """技能库：保存技能元数据，并按需读取技能正文/附加文件。

    Args:
        project_root: 项目根目录，通常是程序启动时的 ``Path.cwd()``。
        roots: 技能根目录；相对路径以 ``project_root`` 为基准。
            传入空序列表示不扫描任何目录。
        enabled: 为 ``False`` 时技能库为空。
    """

    def __init__(
        self,
        project_root: str | Path | None = None,
        roots: str | Path | Iterable[str | Path] | None = None,
        *,
        enabled: bool = True,
    ) -> None:
        self._project_root = Path(project_root or Path.cwd()).expanduser().resolve()
        self._enabled = enabled

        if roots is None:
            roots = (DEFAULT_SKILLS_DIR,)

        resolved_roots: list[Path] = []
        seen: set[str] = set()
        for root in split_skill_roots(roots):
            path = root.expanduser()
            if not path.is_absolute():
                path = self._project_root / path
            path = path.resolve()
            key = os.path.normcase(str(path))
            if key in seen:
                continue
            seen.add(key)
            resolved_roots.append(path)

        self._roots = tuple(resolved_roots)
        self._skills: tuple[SkillMetadata, ...] = ()
        self.refresh()

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------
    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    @property
    def skills(self) -> tuple[SkillMetadata, ...]:
        return self._skills

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(skill.name for skill in self._skills)

    # ------------------------------------------------------------------
    # 发现与查找
    # ------------------------------------------------------------------
    def refresh(self) -> bool:
        """重新扫描元数据；技能清单发生变化时返回 ``True``。"""
        new_skills = (
            tuple(discover_skill_metadata(self._roots)) if self._enabled else ()
        )
        changed = new_skills != self._skills
        self._skills = new_skills
        return changed

    def find(self, name: str) -> SkillMetadata:
        """按 name 查找技能；也会兼容目录名和大小写差异。"""
        query = (name or "").strip()
        if not query:
            raise SkillNotFoundError(query, self.names)

        matches = [skill for skill in self._skills if skill.name == query]
        if not matches:
            matches = [skill for skill in self._skills if skill.identifier == query]
        if not matches:
            folded = query.casefold()
            matches = [
                skill
                for skill in self._skills
                if skill.name.casefold() == folded or skill.identifier.casefold() == folded
            ]
        if not matches:
            raise SkillNotFoundError(query, self.names)
        return matches[0]

    def get(self, name: str) -> SkillMetadata | None:
        try:
            return self.find(name)
        except SkillNotFoundError:
            return None

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def read_skill(self, name: str) -> str:
        """读取指定技能 SKILL.md 的全文。"""
        skill = self.find(name)
        try:
            return skill.read_full_text()
        except OSError as exc:
            raise SkillFileNotFoundError(f"读取技能文件失败: {skill.path}: {exc}") from exc

    def _exact_file(self, skill: SkillMetadata, filename: str) -> tuple[Path | None, bool]:
        """返回 (命中的安全路径, 是否遇到了目录外的同名精确路径)。"""
        raw = filename.strip()
        normalized = raw.replace("\\", os.sep) if os.sep != "\\" else raw
        path = Path(normalized).expanduser()

        if path.is_absolute():
            raw_candidates = [path]
        else:
            raw_candidates = [
                self._project_root / path,
                skill.directory / path,
            ]

        outside_found = False
        seen: set[str] = set()
        for candidate in raw_candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate.absolute()
            key = os.path.normcase(str(resolved))
            if key in seen:
                continue
            seen.add(key)

            if not _is_within(resolved, skill.directory):
                if resolved.exists():
                    outside_found = True
                continue
            if resolved.is_file():
                return resolved, outside_found
        return None, outside_found

    @staticmethod
    def _search_by_name(skill: SkillMetadata, filename: str) -> list[Path]:
        """在技能目录内递归查找同名文件。"""
        exact: list[Path] = []
        folded: list[Path] = []
        target = filename.casefold()

        for current, directories, files in os.walk(skill.directory, followlinks=False):
            # 不跟随符号链接目录，避免通过链接越出技能目录。
            directories[:] = [
                name
                for name in directories
                if not Path(current, name).is_symlink()
            ]
            for name in files:
                if name == filename:
                    candidate = Path(current, name)
                elif name.casefold() == target:
                    candidate = Path(current, name)
                else:
                    continue
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if not _is_within(resolved, skill.directory) or not resolved.is_file():
                    continue
                (exact if name == filename else folded).append(resolved)

        found = exact or folded
        # 去重后按相对路径排序，便于稳定输出候选。
        unique: dict[str, Path] = {}
        for path in found:
            unique[os.path.normcase(str(path))] = path
        return sorted(unique.values(), key=lambda item: str(item).casefold())

    def resolve_file(self, name: str, filename: str) -> Path:
        """解析技能目录内的附加文件路径。

        Raises:
            SkillFileNotFoundError: 文件不存在。
            SkillFileAmbiguousError: 只给文件名且匹配到多个文件。
            SkillPathViolationError: 解析结果越出技能目录。
        """
        skill = self.find(name)
        raw = (filename or "").strip()
        if not raw:
            raise SkillFileNotFoundError("filename 不能为空")

        exact, outside_found = self._exact_file(skill, raw)
        if exact is not None:
            return exact

        path = Path(raw.replace("\\", os.sep) if os.sep != "\\" else raw)
        if path.is_absolute():
            # 绝对路径已经精确检查过；越界时明确拒绝，不再递归搜索。
            raise SkillPathViolationError(
                f"文件不在技能 '{skill.name}' 的目录内，已拒绝读取: {raw}"
            )

        if len(path.parts) == 1:
            matches = self._search_by_name(skill, path.name)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise SkillFileAmbiguousError(path.name, matches)

        if outside_found:
            raise SkillPathViolationError(
                f"文件不在技能 '{skill.name}' 的目录内，已拒绝读取: {raw}"
            )
        raise SkillFileNotFoundError(
            f"技能 '{skill.name}' 中不存在文件: {raw}（已搜索其目录）"
        )

    def read_file(self, name: str, filename: str) -> str:
        """读取技能目录内的附加文件。"""
        target = self.resolve_file(name, filename)
        try:
            return target.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            raise SkillFileNotFoundError(f"读取文件失败: {target}: {exc}") from exc

    def load(self, name: str, filename: str | None = None) -> SkillLoadResult:
        """按名称读取 SKILL.md；提供 filename 时改为读取附加文件。"""
        try:
            skill = self.find(name)
        except SkillNotFoundError as exc:
            return SkillLoadResult(ok=False, error=str(exc))

        try:
            if filename is None or not str(filename).strip():
                content = skill.read_full_text()
                return SkillLoadResult(ok=True, content=content, path=skill.path, skill=skill)

            target = self.resolve_file(skill.name, str(filename))
            content = target.read_text(encoding="utf-8-sig", errors="replace")
            return SkillLoadResult(ok=True, content=content, path=target, skill=skill)
        except SkillFileAmbiguousError as exc:
            return SkillLoadResult(
                ok=False,
                error=str(exc),
                skill=skill,
                candidates=exc.candidates,
            )
        except (SkillError, OSError) as exc:
            return SkillLoadResult(ok=False, error=str(exc), skill=skill)

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------
    @property
    def skills_root(self) -> Path:
        """创建新技能时使用的根目录。

        优先使用已注册根目录中形如 ``<项目>/.agents/skills`` 的那个；
        没有时退回 ``<project_root>/.agents/skills``。
        """
        for root in self._roots:
            if root.name == "skills" and root.parent.name == ".agents":
                return root
        return self._project_root / DEFAULT_SKILLS_DIR

    def create_skill(self, name: str, context: str) -> SkillCreateResult:
        """按 Agent Skills 规范创建一个新技能。

        写入位置为 ``<skills_root>/<name>/SKILL.md``：

            * 名称必须匹配 ``^[a-z0-9]+(?:-[a-z0-9]+)*$``；
            * ``context`` 必须以 YAML Front Matter 开头，且包含 ``description``；
              缺少 ``name`` 时自动补上，与目录名不一致时自动改正；
            * 目标目录已存在时拒绝创建，避免覆盖原有技能；
            * 写入成功后立即刷新技能清单，返回值里的 ``refreshed``
              表示清单是否真的发生了变化。
        """
        try:
            normalized = normalize_skill_name(name)
        except SkillNameError as exc:
            return SkillCreateResult(ok=False, error=str(exc))

        if not self._enabled:
            return SkillCreateResult(
                ok=False,
                name=normalized,
                error="技能功能未启用（skills.enabled = false）",
            )

        text = (context or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return SkillCreateResult(
                ok=False, name=normalized, error="skillContext 不能为空"
            )

        lines = text.split("\n")
        first_content = next(
            (index for index, line in enumerate(lines) if line.strip()), None
        )
        span = _frontmatter_span(lines)
        if span is None or span[0] != first_content:
            return SkillCreateResult(
                ok=False,
                name=normalized,
                error=(
                    "skillContext 必须以 YAML Front Matter 开头，例如：\n"
                    "---\nname: <名称>\ndescription: <一句话说明>\n---\n\n正文"
                ),
            )

        start, end = span
        meta = _parse_frontmatter_lines(lines[start + 1 : end])
        if not _clean(meta.get("description", "")):
            return SkillCreateResult(
                ok=False,
                name=normalized,
                error="Front Matter 缺少必需的 description 字段（一句话说明技能用途）",
            )

        notes: list[str] = []
        frontmatter_name = _clean(meta.get("name", ""))
        if not frontmatter_name:
            lines.insert(start + 1, f"name: {normalized}")
            end += 1
            notes.append(f"已自动补上 name: {normalized}")
        elif frontmatter_name != normalized:
            if not _replace_frontmatter_key(lines, start, end, "name", normalized):
                lines.insert(start + 1, f"name: {normalized}")
                end += 1
            notes.append(f"name 已统一为目录名: {normalized}")

        final_text = "\n".join(lines).rstrip() + "\n"
        parsed = parse_skill_text(final_text, fallback_name=normalized)
        if not parsed.name or not parsed.description or not parsed.body.strip():
            return SkillCreateResult(
                ok=False,
                name=normalized,
                error="生成的内容不完整：需要 Front Matter 的 name、description 和正文",
            )

        target_directory = self.skills_root / normalized
        skill_file = target_directory / SKILL_FILENAME
        if target_directory.exists() or skill_file.exists():
            return SkillCreateResult(
                ok=False,
                name=normalized,
                directory=target_directory,
                error=(
                    f"技能 '{normalized}' 已存在（{target_directory}），"
                    "为避免覆盖原有内容已拒绝创建；如需修改请使用文件编辑工具"
                ),
            )

        try:
            target_directory.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            return SkillCreateResult(
                ok=False, name=normalized, error=f"创建技能目录失败: {exc}"
            )

        try:
            skill_file.write_text(final_text, encoding="utf-8")
        except OSError as exc:
            try:
                target_directory.rmdir()
            except OSError:
                pass
            return SkillCreateResult(
                ok=False, name=normalized, error=f"写入 SKILL.md 失败: {exc}"
            )

        changed = self.refresh()
        return SkillCreateResult(
            ok=True,
            name=normalized,
            path=skill_file,
            directory=target_directory,
            created=True,
            refreshed=changed,
            notes=tuple(notes),
        )

    # 兼容更直白的调用命名。
    load_skill = load


#: 便于外部使用更符合领域语言的别名。
SkillCatalog = SkillLibrary


__all__ = [
    "DEFAULT_SKILLS_DIR",
    "MAX_SKILL_NAME_LENGTH",
    "SKILL_FILENAME",
    "Skill",
    "SkillCatalog",
    "SkillCreateResult",
    "SkillError",
    "SkillFileAmbiguousError",
    "SkillFileNotFoundError",
    "SkillFingerprint",
    "SkillLibrary",
    "SkillLoadResult",
    "SkillMetadata",
    "SkillNameError",
    "SkillNotFoundError",
    "SkillPathViolationError",
    "SkillRuntime",
    "apply_skill_catalog",
    "apply_skills",
    "discover_skill_metadata",
    "discover_skills",
    "format_skill_catalog",
    "format_skills_prompt",
    "normalize_skill_name",
    "parse_skill_text",
    "read_skill_frontmatter",
    "resolve_skills_root",
    "resolve_skills_roots",
    "skills_fingerprint",
    "split_skill_roots",
]
