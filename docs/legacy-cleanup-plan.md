# Legacy 清理计划

## 分析总结

v2 架构已经完成，以下是模块使用情况分析：

### 当前依赖图

```
v2/agentos.py (入口)
    └── v2/core/runtime/vcpu.py
    └── v2/adapters/pi_adapter.py
        └── v2/bridge/pi_ai_http.py
    └── v2/tools/*
        └── tools/read.py, edit.py, grep.py, sandbox.py (复用)
    └── core/logging.py (仅日志)

server/session_v2.py
    └── v2/agentos.py
```

### 模块状态

| 模块 | 文件数 | 状态 | 说明 |
|------|--------|------|------|
| `v2/` | 41 | ✅ 使用中 | 新架构核心 |
| `server/` | 12 | ✅ 使用中 | HTTP 服务 |
| `cli/` | 6 | ✅ 使用中 | 命令行工具 |
| `tools/` | 17 | ⚠️ 部分使用 | 基础工具被 v2 复用 |
| `core/logging.py` | 1 | ⚠️ 部分使用 | 仅日志模块被用 |
| `llm/` | 7 | ⚠️ 待评估 | v2 有自己的 llm 模块 |
| `kernel/` | 6 | ❌ 不再使用 | 旧 kernel，被 v2 替代 |
| `core/` (其余) | 27 | ❌ 不再使用 | 旧核心，被 v2 替代 |
| `skills/` | 11 | ❌ 不再使用 | 旧 skill 系统 |
| `domain/` | 2 | ❌ 不再使用 | 旧领域模型 |
| `acp/` | 9 | ❌ 不再使用 | 旧 ACP 协议 |
| `services/` | 3 | ❌ 不再使用 | RAG 服务 |
| `apps/` | 2 | ❌ 不再使用 | 旧应用入口 |
| `tui/` | 18 | ❌ 不再使用 | 旧 TUI，v2 有新的 |
| `storage/` | 2 | ❌ 不再使用 | SQLite 存储 |

---

## 建议清理方案

### 1. 保留（仍在使用）

```
src/nimbus/
├── v2/              # 新架构核心
├── server/          # HTTP 服务
├── cli/             # 命令行
└── tools/           # 基础工具（被 v2 复用）
    ├── read.py
    ├── edit.py
    ├── grep.py
    ├── sandbox.py
    └── base.py      # ToolRegistry 可能需要
```

### 2. 移到 legacy/（不再使用）

```
src/nimbus/legacy/
├── kernel/          # 旧 kernel
├── core/            # 旧核心（除 logging.py）
│   ├── planner/
│   ├── runtime/
│   ├── task/
│   ├── agent.py
│   ├── memory.py
│   └── ...
├── skills/          # 旧 skill 系统
├── domain/          # 旧领域模型
├── acp/             # 旧 ACP
├── services/        # RAG 服务
├── apps/            # 旧应用入口
├── tui/             # 旧 TUI
├── storage/         # SQLite 存储
└── llm/             # 旧 LLM 客户端
```

### 3. 需要处理的依赖

在移动前需要修复以下引用：

1. **`core/logging.py`** → 保留或移到 `utils/logging.py`
2. **`tools/sandbox.py`** → 保留，被 v2 使用
3. **`tools/read.py`, `edit.py`, `grep.py`** → 保留

---

## 执行步骤

### Phase 1: 提取共享模块

```bash
# 把 logging 移到 utils
mkdir -p src/nimbus/utils
mv src/nimbus/core/logging.py src/nimbus/utils/
# 更新所有 import
```

### Phase 2: 移动不用的模块

```bash
mkdir -p src/nimbus/legacy
mv src/nimbus/kernel src/nimbus/legacy/
mv src/nimbus/skills src/nimbus/legacy/
mv src/nimbus/domain src/nimbus/legacy/
mv src/nimbus/acp src/nimbus/legacy/
mv src/nimbus/services src/nimbus/legacy/
mv src/nimbus/apps src/nimbus/legacy/
mv src/nimbus/tui src/nimbus/legacy/
mv src/nimbus/storage src/nimbus/legacy/
mv src/nimbus/llm src/nimbus/legacy/

# 移动 core 中不用的部分
mv src/nimbus/core/planner src/nimbus/legacy/core/
mv src/nimbus/core/runtime src/nimbus/legacy/core/
mv src/nimbus/core/task src/nimbus/legacy/core/
mv src/nimbus/core/agent.py src/nimbus/legacy/core/
mv src/nimbus/core/memory.py src/nimbus/legacy/core/
# ... 其他
```

### Phase 3: 清理工具模块

```bash
# 保留必要的工具
# src/nimbus/tools/ 只保留:
#   - base.py
#   - read.py
#   - edit.py
#   - grep.py
#   - sandbox.py
#   - __init__.py

# 移动其他
mv src/nimbus/tools/subagent.py src/nimbus/legacy/tools/
mv src/nimbus/tools/websearch.py src/nimbus/legacy/tools/
# ...
```

### Phase 4: 更新 import

更新所有 `from nimbus.core.logging` 为 `from nimbus.utils.logging`

### Phase 5: 测试

```bash
./nimbus start
pytest tests/test_v2_*.py -v
python tests/e2e_tool_call.py
```

---

## 清理后结构

```
src/nimbus/
├── __init__.py
├── v2/                    # 核心架构 (41 files)
│   ├── agentos.py
│   ├── core/
│   ├── adapters/
│   ├── bridge/
│   ├── tools/
│   ├── llm/
│   ├── tui/
│   └── server/
├── server/                # HTTP 服务 (12 files)
├── cli/                   # 命令行 (6 files)
├── tools/                 # 共享工具 (5 files)
│   ├── base.py
│   ├── read.py
│   ├── edit.py
│   ├── grep.py
│   └── sandbox.py
├── utils/                 # 工具函数 (1 file)
│   └── logging.py
└── legacy/                # 已废弃 (~80 files)
    ├── kernel/
    ├── core/
    ├── skills/
    ├── ...
```

文件数量：~65 个活跃文件，~80 个 legacy 文件
