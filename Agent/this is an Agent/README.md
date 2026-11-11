# Agent（core + tui）

一个分层架构的本地 Agent 应用：

- **core**：与界面完全无关的内核（配置、LLM 接入、工具、会话、编排、事件契约）。
- **tui**：基于 [Textual](https://textual.textualize.io/) 的终端界面。
- **事件流**：core 只产出 `AgentEvent`，前端按需渲染。未来新增 Web / Desktop / Mobile 前端时，不需要改动内核。

本项目由 `practice02/chat-tool-fileops.py` 重构而来，保留了原有的 11 个工具与 OpenAI 兼容流式协议，并新增 `searchWeb` 联网搜索、`fetchWeb` 网页正文抓取和 `RunCli` 本地命令执行工具；同时修正了原脚本中重复写入 assistant 消息的问题，并用标准库 `datetime` 取代了 `cmd /c date /t` 子进程。

---

## 快速开始

要求：Python 3.10+（推荐 3.13）。Windows 下请使用 `py` 启动器，避免命中 Microsoft Store 的占位程序。

```powershell
cd "E:\programs1\Agent\this is an Agent"

py -m pip install -r requirements.txt

# 首次运行：生成本地配置并填写 API Key
# [llm] 为对话模型；[search] 为百度千帆联网搜索（可选，但 searchWeb 需要）
Copy-Item config.ini.example config.ini
notepad config.ini

# 启动 TUI
py -m tui

# 无界面模式（便于脚本化、快速验证配置）
py -m tui --prompt "列出当前目录下的文件"

# 指定配置文件
py -m tui --config "D:\path\to\config.ini"
```

也可以安装为命令行工具：

```powershell
py -m pip install -e .
agent-tui
```

### TUI 快捷键

| 按键 | 作用 |
| --- | --- |
| `Enter` | 发送当前输入 |
| `Ctrl+L` | 清空界面显示（保留后端会话历史） |
| `Ctrl+N` | 新建会话（同时清空后端历史） |
| `Ctrl+B` | 显示 / 隐藏侧边栏 |
| `Ctrl+C` | 退出 |

---

## 目录结构

```text
this is an Agent/
├─ core/                         # 内核：不依赖任何 UI 框架
│  ├─ agent/
│  │  ├─ events.py               # 事件契约（所有前端共享）
│  │  ├─ session.py              # 协议级会话历史
│  │  └─ runner.py               # Agent 编排：流式回复 + 工具循环
│  ├─ config/
│  │  └─ settings.py             # 配置加载、环境变量覆盖、强类型配置对象
│  ├─ skills.py                  # Agent Skills：元数据发现 + loadSkill 按需加载
│  ├─ llm/
│  │  ├─ base.py                 # ChatClient 端口（Protocol）
│  │  ├─ types.py                # Message / ToolCall / ToolSpec / StreamDelta
│  │  ├─ sse.py                  # SSE 解析 + 线程到 asyncio 的桥接
│  │  └─ openai_compat.py        # OpenAI 兼容流式客户端适配器
│  ├─ tools/
│  │  ├─ base.py                 # Tool 端口与 ToolResult
│  │  ├─ registry.py             # 工具注册、schema 生成、统一调度
│  │  ├─ paths.py                # 路径策略（工作区根目录 / 沙箱）
│  │  └─ builtin/
│  │     ├─ cli_tools.py         # RunCli（本地命令执行）
│  │     ├─ datetime_tools.py    # getDate / isLeapYear
│  │     ├─ filesystem_tools.py  # 9 个文件系统工具
│  │     ├─ skill_tools.py       # loadSkill / createSkill（技能加载与创建）
│  │     ├─ web_search_tools.py  # searchWeb（百度千帆智能搜索生成）
│  │     └─ web_fetch_tools.py   # fetchWeb（curl + trafilatura 提取正文）
│  ├─ errors.py                  # 统一领域异常
│  └─ factory.py                 # 组合根：装配 Agent（依赖注入）
│
├─ tui/                          # 终端前端：只依赖 core 的公开 API
│  ├─ app.py                     # Textual App 外壳
│  ├─ __main__.py                # 入口：TUI / --prompt 无界面模式
│  ├─ formatting.py              # 纯展示格式化函数
│  ├─ theme.tcss                 # 布局与样式
│  ├─ screens/
│  │  └─ chat_screen.py          # 事件流 -> 界面组件
│  └─ widgets/                   # 可复用的界面组件
│     ├─ chat_log.py
│     ├─ message_view.py
│     ├─ prompt_input.py
│     ├─ sidebar.py
│     ├─ status_bar.py
│     └─ tool_call_view.py
│
├─ tests/
│  ├─ test_core.py               # 工具、编排、事件序列化（离线）
│  ├─ test_web_search.py         # searchWeb、搜索配置、错误处理（离线）
│  ├─ test_web_fetch.py          # fetchWeb、curl 参数、HTML 解析与错误处理（离线）
│  ├─ test_run_cli.py            # RunCli、命令参数、超时、截断与错误处理（离线）
│  ├─ test_skills.py             # 技能元数据发现、loadSkill 与动态注入（离线）
│  ├─ test_tui.py                # Textual headless pilot 测试
│  └─ test_integration.py        # 本地模拟 SSE 服务的端到端测试
│
├─ _experiments/                 # 一次性实验的快照与回滚脚本
├─ config.ini.example            # 配置模板（可提交）
├─ config.ini                    # 本地配置（含密钥，已 gitignore）
├─ pyproject.toml
└─ requirements.txt
```

---

## 分层设计

### 依赖方向

```text
┌─────────────────────────────────────────────────┐
│  前端 frontends                                 │
│  tui/ （Textual）                               │
│  web/ desktop/ mobile/ （未来）                 │
└───────────────────────┬─────────────────────────┘
                        │ AgentEvent 事件流
┌───────────────────────▼─────────────────────────┐
│  core/agent   Agent 编排器                       │
│  ├─ session   会话历史                           │
│  └─ events    事件契约                           │
├─────────────────────────────────────────────────┤
│  core/llm     ChatClient 端口 + OpenAI 适配器    │
│  core/tools   Tool 端口 + 注册表 + 内置工具      │
├─────────────────────────────────────────────────┤
│  core/config  配置                               │
│  core/factory 组合根（依赖注入）                 │
└─────────────────────────────────────────────────┘
```

关键规则：

| 层 | 可以依赖 | 禁止依赖 |
| --- | --- | --- |
| `core/agent` | `core/llm`、`core/tools`、`core/config` | `tui`、Textual、任何 UI |
| `core/llm` | `core/config`、`core/errors`、`core/llm/types` | `core/agent`、`tui` |
| `core/tools` | `core/errors`、`core/llm/types` | `tui`、任何 UI |
| `tui` | `core` 的公开 API、Textual | 直接发 HTTP 请求、直接操作文件 |
| `core/factory` | `core` 内所有模块 | `tui` |

TUI 不关心模型协议、工具实现、会话存储；它只做三件事：

1. 通过 `build_default_agent()` 拿到一个 `Agent`；
2. 把用户输入交给 `agent.run(prompt)`；
3. 把产出的事件渲染成界面。

### 一次问答的数据流

```text
用户输入
  │
  ▼
Agent.run(prompt)                         ── async generator ──
  │  TurnStarted
  │
  ├─ ChatClient.stream(messages, tools)  ── HTTP/SSE ──► 大模型
  │      │  MessageDelta（逐段文本）
  │      └─ 工具调用分片 -> ToolCallAccumulator
  │
  ├─ ToolCallStarted
  ├─ ToolRegistry.execute(...)            ── 工具 ──► 文件系统 / 时间 / 网络搜索 / 网页抓取 / 本地 CLI
  ├─ ToolCallFinished
  │
  ├─（把工具结果回填会话，再次请求模型，可多轮）
  │
  └─ TurnFinished
  │
  ▼
前端逐事件渲染（TUI 直接更新组件；Web 可转 SSE/JSON）
```

---

## 事件契约

所有事件定义在 `core/agent/events.py`，都可以通过 `event.to_dict()` 得到可 `json.dumps` 的结构（包括嵌套的 `ToolResult`）。

| `type` | 主要字段 | 含义 |
| --- | --- | --- |
| `turn_started` | `prompt` | 一轮问答开始 |
| `system_prompt_updated` | 无 | 回合开始前系统提示词被热更新（例如技能文件变化） |
| `message_delta` | `text` | 助手回复的流式文本增量 |
| `tool_call_started` | `call_id` / `name` / `arguments` | 模型请求调用工具 |
| `tool_call_finished` | 同上 + `result` | 工具执行完成（`result.ok` 表示成功） |
| `turn_finished` | `reply` / `rounds` / `error` | 本轮结束；`reply` 是本轮全部文本 |
| `error` | `message` | 网络、配置、工具等错误 |

示例：

```json
{
  "type": "tool_call_finished",
  "call_id": "call_1",
  "name": "readFile",
  "arguments": "{\"path\": \"README.md\"}",
  "result": {
    "content": "文件内容……",
    "ok": true,
    "metadata": { "path": "E:\\...\\README.md", "truncated": false }
  }
}
```

`Agent.ask(prompt)` 是便捷方法：忽略中间事件，直接返回最终文本。

---

## 配置

配置优先级：**显式参数 > 环境变量 > `config.ini` > 内置默认值**。

`config.ini` 已被 `.gitignore` 忽略，不会误提交密钥；`config.ini.example` 是可提交的模板。

### `[llm]`

| 键 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `base_url` | `AGENT_LLM_BASE_URL` | 无（必填） | 完整的 `/v1/chat/completions` 地址 |
| `api_key` | `AGENT_LLM_API_KEY` | 无（必填） | API Key |
| `model` | `AGENT_LLM_MODEL` | 无（必填） | 模型名，例如 `deepseek-chat` |
| `temperature` | `AGENT_LLM_TEMPERATURE` | `0.7` | 采样温度 |
| `max_tokens` | `AGENT_LLM_MAX_TOKENS` | 不发送 | 最大输出 token 数 |
| `max_tool_rounds` | `AGENT_LLM_MAX_TOOL_ROUNDS` | `5` | 单次提问最多工具轮次 |
| `request_timeout` | `AGENT_LLM_TIMEOUT` | `120` | 单次 HTTP 超时（秒） |
| `enable_tools` | `AGENT_LLM_ENABLE_TOOLS` | `true` | 是否携带工具 schema |

### `[tools]`

| 键 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `root` | `AGENT_TOOLS_ROOT` | 当前目录 | 工作区根目录，相对路径以此为基准 |
| `enforce_root` | `AGENT_TOOLS_ENFORCE_ROOT` | `false` | `true` 时拒绝一切越出 `root` 的路径 |
| `allow_delete` | `AGENT_TOOLS_ALLOW_DELETE` | `true` | 是否允许 `removeFile` / `removeDir` |
| `max_read_chars` | `AGENT_TOOLS_MAX_READ_CHARS` | `50000` | `readFile` 单次最大字符数 |

### `[agent]`

| 键 | 环境变量 | 说明 |
| --- | --- | --- |
| `system_prompt` | `AGENT_SYSTEM_PROMPT` | 系统提示词，留空使用内置提示词 |

### `[skills]`

| 键 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | `AGENT_SKILLS_ENABLED` | `true` | 是否启用技能发现与 `loadSkill`；启动时读取，改回 `false` 后下次启动生效 |
| `root` | `AGENT_SKILLS_ROOT` | `.agents/skills` | 额外技能根目录；可用逗号/分号分隔多个目录，相对路径以 `[tools] root` 为基准。启动目录下的 `.agents/skills` 始终会被扫描 |
| `hot_reload` | `AGENT_SKILLS_HOT_RELOAD` | `true` | 每轮提问前重新扫描技能元数据并更新清单；正文只在 `loadSkill` 时读取 |

### `[search]`

| 键 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `baidu_qianfan_api_key` | `AGENT_SEARCH_BAIDU_QIANFAN_API_KEY` | 空（未配置） | 百度千帆 API Key；缺失时 `searchWeb` 会返回清晰错误 |
| `model` | `AGENT_SEARCH_MODEL` | `ernie-4.5-turbo-32k` | 智能搜索生成使用的模型 |
| `request_timeout` | `AGENT_SEARCH_TIMEOUT` | `60` | 搜索请求超时（秒）；智能搜索包含模型总结，通常需要数十秒 |

也兼容旧写法 `search = <key>`（位于 `[agent]` 或 `[search]` 小节中）。

配置文件查找顺序：`--config` 参数 → `AGENT_CONFIG` → 当前目录 `config.ini` → 逐级向上查找。

PowerShell 中临时覆盖密钥：

```powershell
$env:AGENT_LLM_API_KEY = "sk-..."
$env:AGENT_SEARCH_BAIDU_QIANFAN_API_KEY = "..."
py -m tui
```

---

## 技能（Agent Skills）

技能是放在 `<skills_root>/<skill_name>/SKILL.md` 中的可复用工作规范。程序启动时会自动扫描当前工作目录下的 `.agents/skills`，也可以通过 `[skills] root` 追加其他技能目录。

动态加载分为两步：

1. **初始化只读取元数据**：只解析 `SKILL.md` 开头 YAML Front Matter 中的 `name` 和 `description`，不读取正文；
2. **模型按需加载正文**：系统提示词只包含技能清单和 `loadSkill` 的使用说明；模型需要某个技能时调用 `loadSkill`，完整正文才进入对话上下文。

```markdown
---
name: 撰写通知
description: 撰写公司内部通知
version: 1.0.0
---

1、通知标题必须包含部门名称……
```

`name` 和 `description` 是必需字段；缺少任意一个的技能会被跳过。解析器支持引号、全角冒号、注释以及 `|` / `>` 块文本，不依赖第三方 YAML 库。

### 技能发现

启动时会扫描：

- 程序启动目录下的 `.agents/skills`；
- `[skills] root` 配置的目录（逗号、分号或换行分隔，相对路径以 `[tools] root` 为基准）。

每个根目录支持两种布局：

- 技能集合目录：`<root>/<skill_name>/SKILL.md`；
- 单个技能目录：`<root>/SKILL.md`。

```text
.agents/skills/notice/SKILL.md          ← 默认位置
office/.agents/skills/bazi/SKILL.md     ← 额外配置的目录
new skill/role/SKILL.md                 ← 单个技能目录
```

```ini
[skills]
enabled = true
root = office/.agents/skills, new skill/role
```

### `loadSkill` 工具

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `string` | 必需，技能名称（来自系统提示词中的清单） |
| `filename` | `string` | 可选，技能目录内的附加文件 |

- 只传 `name`：返回该技能 `SKILL.md` 全文；
- 传 `filename`：只读取对应附加文件，不会再次加载 `SKILL.md`；
- `filename` 支持相对于技能目录的路径、项目相对路径和绝对路径；
- 只传文件名时会递归搜索技能目录：唯一命中直接读取，多个同名文件则返回候选路径让模型选择；
- 所有文件读取都被限制在指定技能目录内部，`..`、绝对路径越界和符号链接逃逸都会被拒绝。

例如八字技能的 `SKILL.md` 中引用了 `references/wuxing-tables.md`，模型可以这样按需读取：

```json
{"name": "bazi", "filename": "references/wuxing-tables.md"}
```

技能正文与 `loadSkill` 共用现有的工具循环，因此可以和 `RunCli`、`readFile` / `editFile` 等文件工具协同工作，不需要改变原有 Agent 工作流程。

### `createSkill` 工具

模型也可以根据用户的自然语言要求直接创建技能。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `string` | 必需，技能名称，同时作为目录名 |
| `skillContext` | `string` | 必需，要写入 `SKILL.md` 的完整内容 |

- `name` 只能使用小写字母、数字和连字符（如 `pdf-tools`、`weekly-report`），不合法时直接返回错误；
- `skillContext` 必须以 YAML Front Matter 开头，且包含 `description`；缺少 `name` 时会自动用参数补齐，与目录名不一致时会自动统一；
- 写入位置固定为 `<工作区>/.agents/skills/<name>/SKILL.md`；
- 目标目录或 `SKILL.md` 已存在时拒绝创建，避免覆盖原有技能；
- 创建成功后立即刷新技能清单，同一轮对话里就能调用 `loadSkill`，不需要重启；
- 需要 `references/`、`scripts/` 等附加文件时，继续用 `createFile` / `mkdir` 等文件工具在技能目录内创建。

```json
{
  "name": "pdf-tools",
  "skillContext": "---\nname: pdf-tools\ndescription: 处理 PDF 文件\n---\n\n## 使用步骤\n..."
}
```

### 热重载

`hot_reload = true`（默认）时，系统提示词会在两个时机自动刷新：

- 每轮提问开始前；
- 每次工具调用结束、再次请求模型前（因此 `createSkill` 创建的技能在同一轮对话里就能使用）。

刷新时只重新扫描技能元数据：

- 新增、修改、删除技能或修改 `description` → 下一次请求自动更新系统提示词；
- 只修改正文不会改变系统提示词，但下次 `loadSkill` 会读到最新正文；
- 没有变化时不会重复重建提示词；
- 变化后 `Agent` 会产出 `system_prompt_updated` 事件，TUI 侧栏会同步刷新技能列表。

`hot_reload = false` 时不会自动扫描文件系统，但 `createSkill` 写入后仍会立即刷新技能清单。配置项 `[skills] enabled` / `root` 本身仍只在启动时读取，修改后需要重启。

技能默认开启。需要完全关闭时，把 `config.ini` 的 `[skills] enabled` 改为 `false`，或设置 `AGENT_SKILLS_ENABLED=0`。

---

## 上下文压缩（`/compact`）

长时间对话会占满模型的上下文窗口。功能分三部分：

### 1. Token 监控

- 每次 LLM 响应后统计一次上下文占用：优先使用 API 返回的 `usage.total_tokens`，拿不到时用本地估算（中文约 1 字 1 Token，英文约 4 字符 1 Token）；
- 达到上下文窗口的 70% 时，TUI 底部会显示警告，并暂停普通提问，直到压缩完成；
- 窗口大小无法从模型 API 获取，一律按 128k 计算，界面上显示「默认」。

### 2. `/compact` 手动压缩

在输入框输入 `/compact`：

1. 先把当前会话完整写入 `.agents/context/<编号>.jsonl`（编号形如 `20261022-153000`）；
2. 归档成功之后才调用 LLM（使用专用压缩 Prompt）生成约 5,000 Tokens 的 Markdown 摘要；
3. 用摘要替换会话历史，保留 System Prompt 和工具定义；
4. 摘要末尾追加归档说明，提示模型改用 `recallContext` 查历史。

压缩期间禁止执行其他工具，也不能继续处理原有任务；压缩失败会保留原上下文，可以重新执行 `/compact`。

### 3. `recallContext` 工具

| 参数 | 说明 |
| --- | --- |
| `id` | 可选，指定归档编号；不提供时检索全部归档 |
| `text` | 必需，关键词或文本片段 |

- 在 `.agents/context/` 下做本地全文匹配，不需要向量数据库或 Embedding；
- 返回归档编号、行号、角色和命中片段，方便模型确认来源；
- 单次最多返回 6 条，避免检索结果再次撑满上下文。

---

## 斜杠命令

在输入框输入 `/` 会弹出补全面板：上下键选择、Tab/Enter 补全、Esc 关闭。

| 命令 | 作用 |
| --- | --- |
| `/new` | 保存当前会话并新建会话 |
| `/resume` | 列出历史会话，上下键选择、Enter 恢复、Esc 取消 |
| `/save` | 保存当前会话 |
| `/compact` | 压缩上下文并归档原始记录 |
| `/status` | 查看会话与上下文状态 |
| `/clear` | 清空界面（保留后端历史） |
| `/help` | 显示命令列表 |
| `/quit` | 退出程序 |

会话保存在 `.agents/sessions/<编号>.json`，编号形如 `20261105-100000`。

---

## MCP 服务器

在 `[mcp]` 段配置 stdio 方式的 MCP 服务器：

```ini
[mcp]
enabled = true
servers = filesystem|npx|-y;@modelcontextprotocol/server-filesystem;./
```

- 启动 TUI 后在后台异步连接，连上后侧栏自动刷新；
- 单个服务器失败不影响其他服务器，侧栏会显示错误原因；
- 每个服务器显示状态（连接中 / 已连接 / 连接失败）和可用工具列表；
- 连接成功的工具会注册为 `mcp__<服务器>__<工具>`，模型可以直接调用。

---

## AGENTS.md

会话首次加载时，如果当前项目根目录存在 `AGENTS.md`，会把它作为一条消息追加到聊天历史，并随会话一起保存。后续轮次直接复用这条消息，不会重复注入，前缀缓存保持稳定。

---

## 内置工具

| 工具 | 作用 |
| --- | --- |
| `searchWeb` | 搜索互联网，返回智能搜索摘要与参考来源标题/URL |
| `fetchWeb` | 抓取指定网页，返回 Markdown 正文与原始 URL |
| `RunCli` | 执行本地命令行程序，返回 stdout、stderr 与退出码 |
| `loadSkill` | 按需加载 Agent Skill 的完整正文或技能目录内的附加文件 |
| `createSkill` | 按名称和完整内容创建新的 Agent Skill（已存在时拒绝覆盖） |
| `recallContext` | 在上下文归档中检索压缩前的原始对话 |
| `getDate` | 获取当前日期 |
| `isLeapYear` | 判断闰年 |
| `getCwd` | 获取工作区根目录 |
| `listFiles` | 列出目录下的文件与子目录 |
| `getFileType` | 获取扩展名与 MIME 类型 |
| `readFile` | 读取 UTF-8 文本（超长截断） |
| `createFile` | 创建空文件 |
| `editFile` | 覆盖或追加写入 |
| `mkdir` | 创建目录（支持多级） |
| `removeDir` | 删除目录及其全部内容 |
| `removeFile` | 删除文件 |

新增工具只需实现 `core.tools.base.Tool`，然后在 `core/factory.py` 的 `build_tool_registry()` 中注册：

```python
class PingTool(Tool):
    name = "ping"
    description = "返回 pong"

    async def execute(self, arguments):
        return ToolResult.success("pong")
```

工具抛出的异常会被 `ToolRegistry` 捕获并作为失败结果回传给模型，不会中断整轮对话。

---

## 网络搜索（`searchWeb`）

`searchWeb` 通过 Function Calling 暴露给模型，入参为 `query`（关键词或完整问题）。工具调用百度千帆「智能搜索生成」接口：

```text
POST https://qianfan.baidubce.com/v2/ai_search/chat/completions
Authorization: Bearer <baidu_qianfan_api_key>
```

请求固定使用非流式模式，并强制执行搜索；返回给模型的内容包括：

1. 模型生成的搜索摘要；
2. 参考来源的标题、发布时间和 URL（最多 10 条）。

错误处理覆盖：

- 未配置 `baidu_qianfan_api_key`；
- `query` 为空；
- DNS / 连接失败 / 超时等网络异常；
- HTTP 4xx / 5xx；
- HTTP 200 但响应体包含业务错误码；
- 响应不是合法 JSON，或没有摘要与参考来源。

这些错误都会通过 `ToolResult.failure(...)` 返回给模型，由模型决定是否重试或向用户说明。API Key 只从配置/环境变量读取，代码中不包含密钥。

---

## 网页正文抓取（`fetchWeb`）

`fetchWeb` 接受一个完整的 `http://` 或 `https://` URL，通过本地 `curl` 下载 HTML，再用 [trafilatura](https://github.com/adbar/trafilatura) 提取正文并转换为 Markdown。

- 使用 `subprocess.run([...])` 参数列表调用 curl，**不使用 `shell=True`**；
- 保留正文中的标题、段落、列表、表格和链接；
- 关闭图片、评论等无关内容，减少上下文 Token；
- 自动解析相对链接为绝对链接；
- curl 超时 30 秒，subprocess 硬超时 35 秒；
- HTML 最多解析 8 MB，Markdown 最多返回 20,000 字符，超出会截断并附提示。

错误处理覆盖：

- URL 为空、不是完整地址、不是 http/https；
- 找不到 curl；
- DNS / 连接失败 / TLS 失败 / 超时 / HTTP 4xx/5xx；
- curl 返回空内容；
- HTML 解码失败或 trafilatura 解析异常；
- 页面主要由 JavaScript 渲染、需要登录或没有可提取正文。

返回内容形如：

```markdown
# 页面标题

原始 URL: https://example.com/article

正文 Markdown……
```

---

## 本地命令执行（`RunCli`）

`RunCli` 通过 Function Calling 暴露给模型，把程序名和参数分成两个字段：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `command` | `string` | 程序名或可执行文件路径，例如 `py`、`git`、`curl`；不要在这里包含参数 |
| `args` | `string[]` | 参数列表，每一项必须是字符串；没有参数时传 `[]` |

工具内部会把二者拼成参数列表，并始终以 `shell=False` 执行：

```python
argv = [command, *args]
subprocess.run(
    argv,
    capture_output=True,
    stdin=subprocess.DEVNULL,
    timeout=30,
    check=False,
    shell=False,
)
```

示例调用：

```json
{
  "command": "py",
  "args": ["-V"]
}
```

返回给模型的内容包括：

- 实际命令与参数；
- `stdout` 和 `stderr`；
- 退出码（启动失败或超时时为“无”）；
- 执行状态（成功、退出码非 0、超时、无法启动）。

其他约束：

- 单次命令最多执行 30 秒，超时后终止进程；
- 超时由 `subprocess.run` 终止直接子进程；如果命令自行派生了后台进程，可能需要手动清理；
- `stdout` 最多返回 12,000 字符，`stderr` 最多返回 8,000 字符；
- 超长输出会保留开头和结尾，并在中间插入“已截断”标记及原始字符数；
- 命令在当前工作目录、当前用户的环境变量和权限下运行；
- 标准输入连接到空设备，不支持需要交互式输入的程序；
- 命令不存在、权限不足、超时和退出码非 0 都会生成清晰的失败结果，由模型决定是否重试或向用户说明。

> ⚠️ **安全边界**：按本阶段的教学目标，`RunCli` **没有命令白名单、黑名单或审批机制**，会以运行 Agent 的当前用户权限执行任意已安装程序。它不是沙箱：`shell=False` 只能避免本工具把参数拼接成 shell 命令，模型仍可显式启动 `cmd`、`powershell`、`bash` 等解释器。该功能仅用于受控教学实验环境；不要直接接入不可信模型或生产环境。
>
> Windows 的 `dir`、`echo` 等内建命令不是独立可执行文件，不能直接作为 `command` 调用；需要时应调用对应的可执行程序，或显式通过解释器执行（后者会绕过本工具的直接保护）。脚本和批处理程序是否可直接启动取决于操作系统与文件关联。

---

## 扩展新前端

以 Web 为例，新增一个与 `tui/` 平级的 `web/` 目录即可，核心代码无需修改：

```text
web/
├─ server.py       # 例如 FastAPI：把事件流转成 SSE
└─ static/         # 浏览器页面
```

```python
# web/server.py（示意；需要自行安装 fastapi / uvicorn）
import json

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from core.factory import build_default_agent

app = FastAPI()


@app.get("/chat")
async def chat(prompt: str):
    agent, _config = build_default_agent()

    async def event_stream():
        async for event in agent.run(prompt):
            payload = json.dumps(event.to_dict(), ensure_ascii=False)
            yield f"data: {payload}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

要点：

- 每个用户会话应各自持有一个 `Agent`（内含独立的 `ConversationSession`）；
- 只消费 `core.agent.events`，不要让前端依赖 `core.llm.openai_compat`；
- `event.to_dict()` 的结果可直接序列化为 JSON；
- 如果前端运行在浏览器里，注意把工具产生的本地路径做脱敏或相对化处理。

---

## 测试

```powershell
cd "E:\programs1\Agent\this is an Agent"
py -m unittest discover -s tests -t . -v
```

| 测试 | 覆盖内容 |
| --- | --- |
| `test_core.py` | 工具行为、路径沙箱、Agent 工具循环、错误事件、事件 JSON 序列化 |
| `test_web_search.py` | searchWeb 成功/失败路径、配置读取、密钥不泄露 |
| `test_web_fetch.py` | fetchWeb 成功提取、curl 参数列表、URL 校验、超时/HTTP/解析错误 |
| `test_run_cli.py` | RunCli 参数拼装、shell=False、stdout/stderr/退出码、超时、截断与错误处理 |
| `test_skills.py` | 技能解析/发现、`loadSkill` 路径安全、`createSkill` 规范校验与同轮热重载 |
| `test_context.py` | Token 估算、70% 阈值、归档保存与检索、`/compact` 压缩与 `recallContext` |
| `test_tui.py` | Textual headless 启动、输入提交、工具调用组件渲染 |
| `test_integration.py` | 本地模拟 OpenAI SSE 服务，完整跑通 HTTP → SSE → 工具 → 回填 |

全部测试离线运行，不访问外网、不需要真实 API Key；RunCli 的集成测试只启动本机 Python 进程。

---

## 安全与限制

- **API Key**：只放在 `config.ini` 或环境变量中，`config.ini` 已加入 `.gitignore`。
- **删除工具**：默认 `allow_delete = true`，与原脚本行为一致。生产或演示环境建议改为 `false`。
- **路径沙箱**：默认 `enforce_root = false`（允许绝对路径，兼容原脚本）。处理不可信模型输出时建议改为 `true`。
- **网络搜索**：`searchWeb` 会把查询内容发送到百度千帆；返回的网页摘要属于外部不可信内容，不应被当作指令执行。
- **网页抓取**：`fetchWeb` 只允许 http/https，并限制重定向协议；但它仍可访问宿主机网络可达的地址（包括内网地址）。处理不可信模型输出时，建议增加域名白名单或内网地址拦截。
- **本地命令**：`RunCli` **不包含命令白名单、黑名单或审批机制**，会以运行 Agent 的当前用户权限执行任意可执行程序。它可以读写文件、访问网络，也可以显式启动 `cmd` / `powershell` / `bash` 等 shell（`shell=False` 只避免本工具拼接 shell 字符串，并不是沙箱）。该功能仅用于受控教学实验环境；接入不可信模型或生产环境前，必须增加白名单、路径限制、资源限制和人工审批。
- **搜索超时**：智能搜索生成包含模型总结，默认超时 60 秒；网络较慢时可通过 `[search] request_timeout` 调大。
- **抓取超时与体积**：curl 默认超时 30 秒，HTML 最多解析 8 MB，Markdown 最多返回 20,000 字符。
- **无人工确认**：当前工具会直接执行。若接入不可信模型，建议在 `ToolRegistry.execute()` 前增加审批/白名单策略。
- **文本文件**：`readFile` / `editFile` 仅面向 UTF-8 文本，二进制文件请勿直接读写。
- **会话持久化**：会话只保存在内存中，进程退出即丢失；可在 `ConversationSession` 之上扩展存储。
- **单轮工具轮次**：默认最多 5 轮，超过后会通过 `TurnFinished.error` 提示。
