"""技能加载的离线测试：解析、发现、配置与系统提示词注入。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from core.agent.events import SystemPromptUpdated
from core.agent.runner import Agent
from core.config.settings import (
    AppConfig,
    LLMConfig,
    SkillsConfig,
    ToolsConfig,
    load_config,
)
from core.factory import build_agent, load_agent_skills
from core.llm.types import StreamDelta, ToolCallDelta
from core.skills import (
    SkillLibrary,
    SkillRuntime,
    apply_skill_catalog,
    apply_skills,
    discover_skill_metadata,
    discover_skills,
    parse_skill_text,
    resolve_skills_roots,
    skills_fingerprint,
    split_skill_roots,
)
from core.tools.builtin.skill_tools import CreateSkillTool, LoadSkillTool
from core.tools.registry import ToolRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOTICE_SKILL = PROJECT_ROOT / "office" / ".agents" / "skills" / "notice" / "SKILL.md"


def build_config(
    root: Path,
    *,
    enabled: bool = True,
    skill_roots: tuple[Path, ...] = (Path(".agents/skills"),),
) -> AppConfig:
    return AppConfig(
        llm=LLMConfig(base_url="http://localhost", api_key="test", model="fake-model"),
        tools=ToolsConfig(root=root),
        skills=SkillsConfig(enabled=enabled, root=skill_roots),
    )


class ParseSkillTests(unittest.TestCase):
    def test_notice_skill_is_parsed(self) -> None:
        """真实 notice 技能：容忍 `\\---`、全角冒号与中文引号。"""
        self.assertTrue(NOTICE_SKILL.is_file(), f"技能文件不存在: {NOTICE_SKILL}")
        skill = parse_skill_text(
            NOTICE_SKILL.read_text(encoding="utf-8-sig"),
            fallback_name=NOTICE_SKILL.parent.name,
            path=NOTICE_SKILL,
        )
        self.assertEqual(skill.name, "撰写通知")
        self.assertEqual(skill.description, "撰写公司内部通知")
        self.assertEqual(skill.version, "1.0.0")
        self.assertIn("部门名称", skill.body)
        self.assertIn("起草人", skill.body)
        self.assertNotIn("\\---", skill.body)

    def test_missing_front_matter_falls_back_to_body(self) -> None:
        skill = parse_skill_text("直接写的规范", fallback_name="fallback")
        self.assertEqual(skill.name, "fallback")
        self.assertEqual(skill.body, "直接写的规范")
        self.assertEqual(skill.description, "")


class DiscoverSkillsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, content: str) -> Path:
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "SKILL.md"
        path.write_text(content, encoding="utf-8")
        return path

    def test_discover_only_metadata_and_skip_missing_required_fields(self) -> None:
        self._write(
            "b",
            "---\nname: B\ndescription: 第二个\n---\n正文不应该进入提示词",
        )
        self._write("a", "---\nname: A\ndescription: 第一个\n---\n规则 A")
        self._write("no-desc", "---\nname: NoDesc\n---\n规则")
        self._write("no-name", "---\ndescription: 缺少名称\n---\n规则")

        skills = discover_skills(self.root)
        self.assertEqual([skill.name for skill in skills], ["A", "B"])
        self.assertEqual(skills[0].description, "第一个")
        # 元数据对象没有正文；正文只会在 loadSkill 时读取。
        self.assertFalse(hasattr(skills[0], "body"))

    def test_frontmatter_only_is_read_at_discovery(self) -> None:
        path = self._write(
            "a",
            "---\nname: A\ndescription: >\n  第一行\n  第二行\n---\nSECRET BODY",
        )
        skills = discover_skill_metadata(self.root)
        self.assertEqual(skills[0].description, "第一行 第二行")

        # 只读取 Front Matter：即使正文无法解码，元数据仍然可用。
        path.write_bytes(path.read_bytes().replace(b"SECRET BODY", b"\xff\xfe"))
        self.assertEqual([skill.name for skill in discover_skill_metadata(self.root)], ["A"])

    def test_missing_root_is_empty(self) -> None:
        self.assertEqual(discover_skills(self.root / "missing"), [])

    def test_discovers_across_multiple_roots(self) -> None:
        """跨文件夹：两个根目录中的技能都会被发现，且保持根目录顺序。"""
        second = Path(self._tmp.name) / "second"
        first = Path(self._tmp.name) / "first"
        for root, name, body in ((first, "A", "规则 A"), (second, "B", "规则 B")):
            directory = root / name.lower()
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {body}\n---\n{body}",
                encoding="utf-8",
            )

        skills = discover_skills((first, second))
        self.assertEqual([skill.name for skill in skills], ["A", "B"])

        # 重复根目录不会导致技能被注入两次。
        self.assertEqual(len(discover_skills((first, first, second))), 2)

    def test_single_skill_directory_as_root(self) -> None:
        """根目录本身也可以是单个技能目录（直接包含 SKILL.md）。"""
        directory = self.root / "role"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: 角色\ndescription: 保持角色\n---\n保持角色",
            encoding="utf-8",
        )

        skills = discover_skills(directory)
        self.assertEqual([skill.name for skill in skills], ["角色"])

    def test_split_and_resolve_roots(self) -> None:
        self.assertEqual(
            split_skill_roots("a, b;c\nd"),
            (Path("a"), Path("b"), Path("c"), Path("d")),
        )
        base = Path("E:/workspace") if Path("E:/").exists() else Path("/workspace")
        resolved = resolve_skills_roots("a, a, ../b", base)
        self.assertEqual(resolved, (base / "a", base / ".." / "b"))

    def test_apply_skills_injects_listing_without_body(self) -> None:
        self._write("a", "---\nname: A\ndescription: 说明\n---\n规则 A")
        skills = discover_skills(self.root)
        prompt = apply_skills("基础提示词", skills)
        self.assertTrue(prompt.startswith("基础提示词"))
        self.assertIn("You have access to the following skills", prompt)
        self.assertIn("- A: 说明", prompt)
        self.assertIn("loadSkill", prompt)
        self.assertNotIn("规则 A", prompt)
        self.assertEqual(apply_skills("基础提示词", []), "基础提示词")


class _RecordingClient:
    """记录每次请求实际收到的系统提示词。"""

    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, messages, tools=()):
        del tools
        self.system_prompts.append(
            next(message.content for message in messages if message.role == "system")
        )
        yield StreamDelta(content="ok")


class SkillRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, content: str) -> Path:
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "SKILL.md"
        path.write_text(content, encoding="utf-8")
        return path

    def test_refresh_only_when_metadata_changes(self) -> None:
        path = self._write("a", "---\nname: A\ndescription: 旧说明\n---\n规则 A")
        runtime = SkillRuntime("基础提示词", self.root)

        self.assertIn("A: 旧说明", runtime.prompt)
        self.assertNotIn("规则 A", runtime.prompt)
        self.assertFalse(runtime.refresh(), "元数据没变时不应重建提示词")

        # 只改正文不会影响系统提示词。
        path.write_text(
            "---\nname: A\ndescription: 旧说明\n---\n规则 A 已经更新", encoding="utf-8"
        )
        self.assertFalse(runtime.refresh())

        # description 变化会触发提示词更新。
        path.write_text(
            "---\nname: A\ndescription: 新说明\n---\n规则 A 已经更新", encoding="utf-8"
        )
        self.assertTrue(runtime.refresh())
        self.assertIn("A: 新说明", runtime.prompt)
        self.assertFalse(runtime.refresh(), "刷新后元数据应同步")

    def test_refresh_detects_added_and_removed_skill(self) -> None:
        runtime = SkillRuntime("基础提示词", self.root)
        self.assertEqual(runtime.skills, ())
        self.assertNotIn("You have access", runtime.prompt)

        path = self._write("b", "---\nname: B\ndescription: 第二个\n---\n规则 B")
        self.assertTrue(runtime.refresh())
        self.assertIn("B: 第二个", runtime.prompt)
        self.assertNotIn("规则 B", runtime.prompt)

        path.unlink()
        self.assertTrue(runtime.refresh())
        self.assertNotIn("B: 第二个", runtime.prompt)

    def test_fingerprint_tracks_paths(self) -> None:
        path = self._write("a", "---\nname: A\ndescription: 说明\n---\n规则 A")
        fingerprint = skills_fingerprint(self.root)
        self.assertEqual(len(fingerprint), 1)
        self.assertEqual(fingerprint[0][0], os.path.normcase(str(path.resolve())))

    def test_agent_reloads_prompt_between_turns(self) -> None:
        """Agent 在每轮开始前调用 provider，元数据改动下一轮生效。"""
        path = self._write("a", "---\nname: A\ndescription: 旧说明\n---\n旧正文")
        runtime = SkillRuntime("基础提示词", self.root)
        client = _RecordingClient()
        agent = Agent(
            client=client,
            registry=ToolRegistry(),
            system_prompt=runtime.prompt,
            system_prompt_provider=runtime.refresh_prompt,
        )

        async def scenario() -> list[object]:
            await agent.ask("第一轮")
            path.write_text(
                "---\nname: A\ndescription: 新说明\n---\n新正文", encoding="utf-8"
            )
            events = [event async for event in agent.run("第二轮")]
            return events

        events = asyncio.run(scenario())

        self.assertIn("旧说明", client.system_prompts[0])
        self.assertIn("新说明", client.system_prompts[1])
        self.assertTrue(
            any(isinstance(event, SystemPromptUpdated) for event in events),
            "热重载时应产出 SystemPromptUpdated 事件",
        )


class SkillConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _config_file(self, skills_section: str) -> Path:
        path = self.root / "config.ini"
        path.write_text(
            "[llm]\n"
            "base_url = http://localhost/v1/chat/completions\n"
            "api_key = test\n"
            "model = fake-model\n\n" + skills_section,
            encoding="utf-8",
        )
        return path

    def test_default_is_enabled(self) -> None:
        config = load_config(self._config_file(""))
        self.assertTrue(config.skills.enabled)
        self.assertEqual(config.skills.root, (Path(".agents/skills"),))
        self.assertTrue(config.skills.hot_reload)

    def test_reads_section(self) -> None:
        config = load_config(
            self._config_file("[skills]\nenabled = true\nroot = office/.agents/skills\n")
        )
        self.assertTrue(config.skills.enabled)
        self.assertEqual(config.skills.root, (Path("office/.agents/skills"),))

    def test_reads_multiple_roots(self) -> None:
        """`root` 支持逗号 / 分号 / 换行分隔的多个目录。"""
        config = load_config(
            self._config_file(
                "[skills]\nenabled = true\n"
                "root = office/.agents/skills, new skill/role\n"
            )
        )
        self.assertEqual(
            config.skills.root,
            (Path("office/.agents/skills"), Path("new skill/role")),
        )

    def test_reads_hot_reload_switch(self) -> None:
        config = load_config(
            self._config_file("[skills]\nenabled = true\nhot_reload = false\n")
        )
        self.assertFalse(config.skills.hot_reload)

    def test_disabled_does_not_inject(self) -> None:
        directory = self.root / ".agents" / "skills" / "a"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: A\ndescription: 说明\n---\n规则 A", encoding="utf-8"
        )

        agent = build_agent(build_config(self.root, enabled=False))
        system_prompt = agent.session.to_payload_messages()[0].content
        self.assertNotIn("You have access to the following skills", system_prompt)
        self.assertEqual(load_agent_skills(build_config(self.root, enabled=False)), [])

    def test_enabled_injects_metadata_and_registers_load_skill(self) -> None:
        directory = self.root / ".agents" / "skills" / "notice"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: 撰写通知\ndescription: 写通知\n---\n必须写部门名称",
            encoding="utf-8",
        )

        agent = build_agent(build_config(self.root, enabled=True))
        system_prompt = agent.session.to_payload_messages()[0].content
        self.assertIn("You have access to the following skills", system_prompt)
        self.assertIn("- 撰写通知: 写通知", system_prompt)
        self.assertIn("loadSkill", system_prompt)
        # 正文只在 loadSkill 时才进入上下文。
        self.assertNotIn("必须写部门名称", system_prompt)
        self.assertIn("loadSkill", agent.tool_names)

        result = asyncio.run(
            agent.registry.execute("loadSkill", {"name": "撰写通知"})
        )
        self.assertTrue(result.ok)
        self.assertIn("必须写部门名称", result.content)

    def test_injects_skills_from_different_folders(self) -> None:
        """跨文件夹发现：相对路径各自以 [tools] root 为基准解析。"""
        first = self.root / "office" / ".agents" / "skills" / "notice"
        second = self.root / "new skill" / "role"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        (first / "SKILL.md").write_text(
            "---\nname: 撰写通知\ndescription: 写通知\n---\n必须写部门名称",
            encoding="utf-8",
        )
        (second / "SKILL.md").write_text(
            "---\nname: 丰川祥子\ndescription: 角色语气\n---\n保持角色语气",
            encoding="utf-8",
        )

        agent = build_agent(
            build_config(
                self.root,
                enabled=True,
                skill_roots=(
                    Path("office/.agents/skills"),
                    Path("new skill/role"),
                ),
            )
        )
        system_prompt = agent.session.to_payload_messages()[0].content
        self.assertIn("撰写通知", system_prompt)
        self.assertIn("丰川祥子", system_prompt)
        self.assertNotIn("必须写部门名称", system_prompt)
        self.assertNotIn("保持角色语气", system_prompt)


class LoadSkillToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.skill_dir = self.root / ".agents" / "skills" / "demo"
        (self.skill_dir / "references").mkdir(parents=True)
        (self.skill_dir / "scripts").mkdir(parents=True)
        self.skill_file = self.skill_dir / "SKILL.md"
        self.skill_file.write_text(
            "---\nname: Demo\ndescription: 演示技能\n---\n完整正文\n",
            encoding="utf-8",
        )
        (self.skill_dir / "references" / "guide.md").write_text(
            "GUIDE", encoding="utf-8"
        )
        (self.skill_dir / "scripts" / "run.py").write_text(
            "print('ok')", encoding="utf-8"
        )
        (self.skill_dir / "references" / "dup.md").write_text(
            "REF-DUP", encoding="utf-8"
        )
        (self.skill_dir / "scripts" / "dup.md").write_text(
            "SCRIPT-DUP", encoding="utf-8"
        )
        self.library = SkillLibrary(self.root)
        self.tool = LoadSkillTool(self.library)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, arguments):
        return asyncio.run(self.tool.execute(arguments))

    def test_name_only_loads_skill_body(self) -> None:
        result = self._run({"name": "Demo"})
        self.assertTrue(result.ok)
        self.assertIn("完整正文", result.content)
        self.assertEqual(result.metadata["filename"], "SKILL.md")

    def test_filename_supports_skill_relative_and_project_relative(self) -> None:
        by_skill_path = self._run({"name": "Demo", "filename": "references/guide.md"})
        self.assertTrue(by_skill_path.ok)
        self.assertEqual(by_skill_path.content, "GUIDE")

        project_relative = (
            self.root / ".agents" / "skills" / "demo" / "references" / "guide.md"
        )
        by_project = self._run({"name": "Demo", "filename": str(project_relative)})
        self.assertTrue(by_project.ok)
        self.assertEqual(by_project.content, "GUIDE")

    def test_absolute_path_inside_skill_is_allowed(self) -> None:
        absolute = self.skill_dir / "scripts" / "run.py"
        result = self._run({"name": "Demo", "filename": str(absolute)})
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "print('ok')")

    def test_bare_filename_recursively_searches(self) -> None:
        result = self._run({"name": "Demo", "filename": "run.py"})
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "print('ok')")

    def test_ambiguous_bare_filename_returns_candidates(self) -> None:
        result = self._run({"name": "Demo", "filename": "dup.md"})
        self.assertFalse(result.ok)
        self.assertIn("references/dup.md", result.content)
        self.assertIn("scripts/dup.md", result.content)
        self.assertEqual(len(result.metadata["candidates"]), 2)

    def test_paths_outside_skill_directory_are_rejected(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_text("SECRET", encoding="utf-8")

        absolute = self._run({"name": "Demo", "filename": str(outside)})
        self.assertFalse(absolute.ok)
        self.assertNotIn("SECRET", absolute.content)

        traversal = self._run(
            {"name": "Demo", "filename": "../../../outside.txt"}
        )
        self.assertFalse(traversal.ok)
        self.assertNotIn("SECRET", traversal.content)

    def test_unknown_skill_reports_available_names(self) -> None:
        result = self._run({"name": "missing"})
        self.assertFalse(result.ok)
        self.assertIn("Demo", result.content)


SKILL_CONTEXT = (
    "---\n"
    "name: pdf-tools\n"
    "description: 处理 PDF 文件\n"
    "---\n\n"
    "## 使用步骤\n\n1. 读取文件\n"
)


class CreateSkillToolTests(unittest.TestCase):
    """createSkill：规范校验、防覆盖与创建后立即可用。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.library = SkillLibrary(self.root)
        self.tool = CreateSkillTool(self.library)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, arguments):
        return asyncio.run(self.tool.execute(arguments))

    def _create(self, name: str = "pdf-tools", context: str = SKILL_CONTEXT):
        return self._run({"name": name, "skillContext": context})

    def test_creates_skill_under_agents_skills_and_refreshes(self) -> None:
        result = self._create()
        self.assertTrue(result.ok, result.content)
        path = self.root / ".agents" / "skills" / "pdf-tools" / "SKILL.md"
        self.assertTrue(path.is_file())
        self.assertIn("pdf-tools", self.library.names)
        self.assertTrue(result.metadata["refreshed"])

        loaded = self.library.load("pdf-tools")
        self.assertTrue(loaded.ok)
        self.assertIn("使用步骤", loaded.content)

    def test_invalid_names_are_rejected(self) -> None:
        for bad in ("PDF Tools", "pdf_tools", "-pdf", "pdf-", "中文名", ""):
            with self.subTest(name=bad):
                result = self._create(name=bad)
                self.assertFalse(result.ok)
                self.assertIn("名称", result.content)

    def test_existing_skill_is_not_overwritten(self) -> None:
        first = self._create()
        self.assertTrue(first.ok)
        path = self.root / ".agents" / "skills" / "pdf-tools" / "SKILL.md"
        original = path.read_text(encoding="utf-8")

        second = self._create(context=SKILL_CONTEXT.replace("使用步骤", "新内容"))
        self.assertFalse(second.ok)
        self.assertIn("已存在", second.content)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_requires_front_matter(self) -> None:
        result = self._create(context="直接写正文，没有 Front Matter")
        self.assertFalse(result.ok)
        self.assertIn("Front Matter", result.content)
        self.assertFalse((self.root / ".agents" / "skills" / "pdf-tools").exists())

    def test_requires_description(self) -> None:
        result = self._create(context="---\nname: pdf-tools\n---\n正文")
        self.assertFalse(result.ok)
        self.assertIn("description", result.content)

    def test_missing_name_is_filled_from_argument(self) -> None:
        result = self._create(context="---\ndescription: 说明\n---\n正文")
        self.assertTrue(result.ok, result.content)
        text = (
            self.root / ".agents" / "skills" / "pdf-tools" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("name: pdf-tools", text)

    def test_mismatched_name_is_normalized(self) -> None:
        result = self._create(context="---\nname: PDF工具\ndescription: 说明\n---\n正文")
        self.assertTrue(result.ok, result.content)
        text = (
            self.root / ".agents" / "skills" / "pdf-tools" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("name: pdf-tools", text)
        self.assertNotIn("PDF工具", text)

    def test_empty_context_is_rejected(self) -> None:
        result = self._create(context="   ")
        self.assertFalse(result.ok)


class _CreateSkillClient:
    """第一轮要求创建技能，第二轮返回结果。"""

    def __init__(self, arguments: dict) -> None:
        self._arguments = arguments
        self.system_prompts: list[str] = []
        self.calls = 0

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, messages, tools=()):
        del tools
        self.system_prompts.append(
            next(message.content for message in messages if message.role == "system")
        )
        self.calls += 1
        if self.calls == 1:
            yield StreamDelta(
                tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="call-1",
                        name="createSkill",
                        arguments=json.dumps(self._arguments, ensure_ascii=False),
                    )
                ]
            )
        else:
            yield StreamDelta(content="完成")


class CreateSkillHotReloadTests(unittest.TestCase):
    """同一轮工具调用里创建的技能，下一次请求就能出现在系统提示词中。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_created_skill_is_visible_on_next_llm_request(self) -> None:
        library = SkillLibrary(self.root)
        registry = ToolRegistry([CreateSkillTool(library)])
        client = _CreateSkillClient(
            {"name": "pdf-tools", "skillContext": SKILL_CONTEXT}
        )

        def provider() -> str:
            library.refresh()
            return apply_skill_catalog("基础提示词", library.skills)

        agent = Agent(
            client=client,
            registry=registry,
            system_prompt=provider(),
            system_prompt_provider=provider,
            max_tool_rounds=3,
            skill_library=library,
        )

        async def scenario() -> list[object]:
            return [event async for event in agent.run("帮我创建一个 pdf 技能")]

        events = asyncio.run(scenario())

        self.assertEqual(len(client.system_prompts), 2)
        self.assertNotIn("pdf-tools", client.system_prompts[0])
        self.assertIn("pdf-tools", client.system_prompts[1])
        self.assertTrue(
            any(isinstance(event, SystemPromptUpdated) for event in events),
            "工具创建技能后应产出 SystemPromptUpdated 事件",
        )
        self.assertIn("pdf-tools", agent.skill_names)


if __name__ == "__main__":
    unittest.main()
