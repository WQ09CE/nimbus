# Nimbus Skill System Proposal (Inspired by Claude Code)

## 背景

当前，Nimbus 的工具系统（`ToolRegistry`）主要围绕 Python 函数进行硬编码注册。虽然稳定，但在扩展性上略显不足，特别是对于非 Python 开发者或者希望通过简单的文件结构来扩展 Agent 能力的场景（如 Prompt + Scripts）。

Claude Code 提出的 **Skill** 概念非常先进：只要按照特定结构放置文件，Agent 就能自动获得相应的能力。这极大地降低了扩展门槛。

## 核心设计理念

**"File System as API"**：我们遵循 [Agent Skills Open Standard](https://github.com/anthropics/agent-skills)，通过文件系统结构来定义能力。

一个 Skill 是一个自包含的目录，包含：
1.  **Manifest (`SKILL.md`)**: 
    - **YAML Frontmatter**: 定义元数据（Name, Description, Tools）。其中 `Name` 对应 CLI 中的 `/slash-command`。
    - **Markdown Content**: 详细的 System Prompt 指令。
2.  **Capabilities (`scripts/`)**: 可直接执行的脚本文件。
3.  **Knowledge (`docs/`)**: 可选，用于 RAG 的文档。

## 目录结构示例

```
/nimbus/skills/
  ├── postgres_expert/             # Skill Name -> /postgres_expert
  │   ├── SKILL.md                 # Manifest
  │   ├── scripts/
  │   │   ├── analyze_table.py
  │   │   └── explain_query.sh
  │   └── docs/
  │       └── analysis_guide.md
  │
  └── git_flow/
      ├── SKILL.md
      └── scripts/
          └── feature_start.sh
```

## SKILL.md 规范 (Agent Skills Standard Compatible)

```markdown
---
name: postgres-expert           # Maps to /postgres-expert
description: Advanced PostgreSQL analysis and optimization tools
version: 1.0.0
tools:                          # Nimbus Extension: Explicit tool definition
  - name: analyze_table
    description: Analyze table statistics and distribution
    entrypoint: scripts/analyze_table.py
    args:
      table_name:
        type: string
        description: Name of the table to analyze
  - name: explain_query
    description: Visualize query execution plan
    entrypoint: scripts/explain_query.sh
    args:
      query:
        type: string
        description: SQL query to explain
---

# Postgres Expert Guidelines

## Role
You are an expert PostgreSQL DBA. When the user asks about database performance:
1. Always run `analyze_table` first to understand data distribution.
2. Use `explain_query` for slow queries.

## Best Practices
- Never run `DROP TABLE` without explicit confirmation.
- Always check indexes before proposing schema changes.
```

## 实现方案

我们需要在 `nimbus` 中引入 `SkillManager`：

### 1. Skill Loader
- **扫描**: 并在启动时扫描配置的 `skill_dirs`。
- **解析**: 读取 `SKILL.md` 的 YAML header，注册 Tool 定义。
- **构建**: 
    - 将 `scripts/` 下的脚本自动包装为 `Bash` 工具的调用（Sandbox 安全执行）。
    - 将 `SKILL.md` 的 Markdown 正文合并到 System Prompt（或作为动态 Context Inject）。

### 2. Context Injection
- **Dynamic Prompting**: 与其把所有 Skill 的 Instructions 都塞进 System Prompt（导致 Context 爆炸），不如在检测到相关意图时动态加载。
- **Simple Route**: 如果 User 为了某个 Task 显式启用某个 Skill（如 `/use postgres`），则加载该 Skill。

### 3. AgentOS 集成

```python
# agentos.py (伪代码)
class AgentOS:
    def __init__(self, config):
        self.skill_manager = SkillManager(config.skill_dirs)
        
        # Load active skills
        for skill in self.skill_manager.load_all():
             self._tools.register_skill(skill)
             self.system_prompt += f"\n\n## Skill: {skill.name}\n{skill.instructions}"
```

## 优势

1.  **低代码扩展**: 用户只需写 Markdown 和简单的脚本即可添加能力。
2.  **解耦**: Skill 可以独立分发（git submodule），甚至通过 MCP (Model Context Protocol) 共享。
3.  **多语言支持**: `scripts/` 可以是 Python, Bash, Node.js 等任何可执行文件。
4.  **更好的 Prompt 管理**: 相关 Instructions 跟随 Skill 定义，而不是散落在代码里。

## 下一步计划 (Roadmap)

1.  **Phase 1**: 实现 `SkillManager` 和 `Skill` 数据结构，支持读取 `SKILL.md`。
2.  **Phase 2**: 实现 `ScriptToolWrapper`，自动将脚本转化为 Agent 工具。
3.  **Phase 3**: 在 Client 端 (CLI/WebUI) 支持 `skill add <path>` 命令动态加载。
