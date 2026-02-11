---
name: nimbus-dev
version: 1.0.0
description: Nimbus 项目开发辅助工具集 — 快速了解架构、定位代码、跑测试、检查健康状态
tools:
  - name: NimbusArchMap
    description: "输出 Nimbus 项目的架构全景图：模块结构、关键文件、代码量统计、依赖关系。帮助快速建立对项目的整体认知。"
    entrypoint: scripts/arch_map.py
    args:
      focus:
        type: string
        description: "可选聚焦模块：core, tools, orchestration, skills, server, all (默认 all)"
        required: false
  - name: NimbusHealthCheck
    description: "运行 Nimbus 项目健康检查：import 检查、测试运行、lint、关键文件完整性。快速发现当前代码库的问题。"
    entrypoint: scripts/health_check.py
    args:
      scope:
        type: string
        description: "检查范围：quick (仅 import+关键文件), test (运行测试), full (全部) 默认 quick"
        required: false
  - name: NimbusWhereIs
    description: "在 Nimbus 代码库中定位某个概念/类/函数的实现位置。比 grep 更智能——理解 nimbus 的模块结构，返回文件路径+行号+上下文。"
    entrypoint: scripts/where_is.py
    args:
      query:
        type: string
        description: "要查找的内容：类名、函数名、概念（如 'tool registry', 'dispatch', 'vcpu cycle'）"
---

# Nimbus Dev — 项目开发辅助指南

你正在开发 **Nimbus Agent Framework**（v0.2.0），一个基于 von Neumann 架构的 AI Agent 框架。

## 项目核心架构

```
nimbus/
├── core/           # 核心引擎：vCPU (Think-Act-Observe), MMU (内存管理), Scheduler, Session
├── tools/          # 工具系统：base (ToolRegistry/ToolDefinition), read/write/edit/bash, composite
├── orchestration/  # 编排层：Dispatch (双Agent), Verify, ReviewCommittee, Prompts
├── skills/         # 技能系统：SKILL.md 加载器, SkillManager, ScriptTool
├── server/         # HTTP 服务：FastAPI, session_v2, SSE streaming
├── os/             # OS 抽象：KernelGate (权限隔离)
├── adapters/       # LLM 适配器：pi-ai bridge
├── cli/            # CLI 入口：nimbus serve
└── storage/        # 持久化：SQLite
```

## 工具分类体系 (三域模型)

- **Core Tools** (core): Read, Write, Edit, Bash — 不可替换的 OS 原语
- **Extension Tools** (extension): Dispatch, Verify, ReviewCommittee, Memo, ReloadSkills — 按 profile 挂载
- **Skill Tools** (skill): 从 SKILL.md 动态加载 — 热插拔

## 开发常用操作

### 跑测试
```bash
pytest tests/ -x -q                          # 快速全量
pytest tests/test_tools_base.py -x -q        # 工具系统
pytest tests/test_skill_integration_v2.py     # 技能集成
pytest tests/core/ -x -q                     # 核心引擎
```

### 启动服务
```bash
nimbus serve                                  # 默认启动
nimbus serve --port 3000 --reload             # 开发模式
```

### 关键设计文档
- `docs/design/tools-category-proposal.md` — 工具三域分类提案
- `docs/design/tools-skills-system.md` — Tools & Skills 系统全景
- `docs/design/multi-agent-architecture.md` — 多 Agent 架构设计

## 开发注意事项

1. **工具注册**：新增工具必须显式声明 `category`（core/extension/skill），默认 None 会触发启动告警
2. **依赖方向**：skill → extension → core，禁止逆向依赖
3. **Core 工具名是保留字**：Read/Write/Edit/Bash，skill 不可覆盖（ToolNameConflictError）
4. **安全哲学**：不搞工具层安全限制（沙箱/黑名单），安全边界在人机交互层
5. **Prompt 理念**：Core Agent 有全套工具，用判断力决定自己做还是 Dispatch
