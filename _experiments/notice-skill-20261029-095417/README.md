# 回滚点：加载 notice 技能之前

- 创建时间：2026-10-29 09:54:17
- 目的：把 `office/.agents/skills/notice/SKILL.md` 中的“撰写通知”技能加载到 Agent 系统提示词中。
- 原始文件备份：`backup/`（保持项目内的相对路径）。
- 变更清单：`manifest.json`。

## 两种回退方式

### 1. 运行时关闭（最快，不动代码）

把 `config.ini` 中的 `[skills]` 改为：

```ini
[skills]
enabled = false
```

或临时设置环境变量：

```powershell
$env:AGENT_SKILLS_ENABLED = "0"
py -m tui
```

技能立刻不再注入，其它功能不受影响。

### 2. 完全回滚（恢复代码与配置）

双击 `restore.cmd`，或执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\restore.ps1
```

脚本会：

1. 用 `backup/` 中的副本覆盖本次修改过的文件；
2. 删除本次新增的 `core/skills.py`、`tests/test_skills.py`；
3. 清理对应的 `.pyc` 缓存；
4. 删除自身快照目录（加 `-KeepSnapshot` 可保留）。

回滚后建议执行一次测试：

```powershell
cd "E:\programs1\Agent\this is an Agent"
py -m unittest discover -s tests -t . -v
```
