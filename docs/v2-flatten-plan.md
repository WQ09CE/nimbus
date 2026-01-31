# V2 目录展开计划

> 目标：移除 `v2/` 中间层，将代码展开到一级目录

## 当前结构

```
src/nimbus/
├── v2/                    # 41 个文件 - 要展开
│   ├── __init__.py
│   ├── agentos.py         # 主入口
│   ├── adapters/          # LLM 适配器
│   │   ├── __init__.py
│   │   └── pi_adapter.py
│   ├── bridge/            # pi-ai 桥接
│   │   ├── __init__.py
│   │   ├── pi_ai_http.py
│   │   └── pi_client.py
│   ├── core/              # 核心模块
│   │   ├── __init__.py
│   │   ├── compaction.py
│   │   ├── protocol.py
│   │   ├── scheduler.py
│   │   ├── session.py
│   │   ├── memory/
│   │   │   ├── __init__.py
│   │   │   ├── context.py
│   │   │   └── mmu.py
│   │   └── runtime/
│   │       ├── __init__.py
│   │       ├── decoder.py
│   │       └── vcpu.py
│   ├── llm/               # LLM 客户端
│   │   ├── __init__.py
│   │   ├── anthropic.py
│   │   ├── gemini.py
│   │   ├── openrouter.py
│   │   └── retry.py
│   ├── os/                # 权限门控
│   │   ├── __init__.py
│   │   └── gate.py
│   ├── server/            # API 服务
│   │   ├── __init__.py
│   │   ├── api_ai_sdk.py
│   │   ├── api_openai.py
│   │   └── app.py
│   ├── tools/             # 工具实现
│   │   ├── __init__.py
│   │   ├── bash.py
│   │   ├── edit.py
│   │   ├── glob.py
│   │   ├── grep.py
│   │   ├── read.py
│   │   └── write.py
│   └── tui/               # TUI 界面
│       ├── __init__.py
│       ├── app.py
│       └── widgets/
│           ├── __init__.py
│           ├── chatbox.py
│           └── prompt_input.py
├── core/                  # 5 个文件 - 保留
├── tools/                 # 9 个文件 - 合并
├── server/                # 16 个文件 - 保留
├── storage/               # 2 个文件 - 保留
├── cli/                   # 7 个文件 - 保留
└── legacy/                # 90 个文件 - 已废弃
```

## 目标结构

```
src/nimbus/
├── __init__.py            # 更新导出
├── agentos.py             # 从 v2/agentos.py
├── adapters/              # 从 v2/adapters/
│   ├── __init__.py
│   └── pi_adapter.py
├── bridge/                # 从 v2/bridge/
│   ├── __init__.py
│   ├── pi_ai_http.py
│   └── pi_client.py
├── core/                  # 合并 v2/core/ 到现有 core/
│   ├── __init__.py        # 更新导出
│   ├── logging.py         # 保留
│   ├── types.py           # 保留
│   ├── config.py          # 保留
│   ├── memory.py          # 保留
│   ├── compaction.py      # 从 v2/core/
│   ├── protocol.py        # 从 v2/core/
│   ├── scheduler.py       # 从 v2/core/
│   ├── session.py         # 从 v2/core/
│   ├── memory/            # 从 v2/core/memory/
│   │   ├── __init__.py
│   │   ├── context.py
│   │   └── mmu.py
│   └── runtime/           # 从 v2/core/runtime/
│       ├── __init__.py
│       ├── decoder.py
│       └── vcpu.py
├── llm/                   # 从 v2/llm/ (替换 legacy/llm)
│   ├── __init__.py
│   ├── anthropic.py
│   ├── gemini.py
│   ├── openrouter.py
│   └── retry.py
├── os/                    # 从 v2/os/
│   ├── __init__.py
│   └── gate.py
├── tools/                 # 合并 v2/tools/ 到现有 tools/
│   ├── __init__.py        # 更新导出
│   ├── base.py            # 保留
│   ├── sandbox.py         # 保留
│   ├── read.py            # 用 v2 版本替换
│   ├── write.py           # 用 v2 版本替换
│   ├── edit.py            # 用 v2 版本替换
│   ├── glob.py            # 用 v2 版本替换
│   ├── grep.py            # 用 v2 版本替换
│   └── bash.py            # 用 v2 版本替换
├── tui/                   # 从 v2/tui/ (替换 legacy/tui)
│   ├── __init__.py
│   ├── app.py
│   └── widgets/
│       ├── __init__.py
│       ├── chatbox.py
│       └── prompt_input.py
├── server/                # 保留 + 添加 v2/server/ 的 API
│   ├── __init__.py
│   ├── app.py             # 保留
│   ├── api.py             # 保留
│   ├── api_ai_sdk.py      # 从 v2/server/
│   ├── api_openai.py      # 从 v2/server/
│   ├── ...                # 其他保留
├── storage/               # 保留
├── cli/                   # 保留
└── legacy/                # 保留（可后续删除）
```

## 测试覆盖率报告

### v2 模块覆盖率（测试前）

| 模块 | 覆盖率 | 状态 | 说明 |
|------|--------|------|------|
| **core/protocol.py** | 100% | ✅ | 协议定义 |
| **core/__init__.py** | 100% | ✅ | |
| **core/memory/__init__.py** | 100% | ✅ | |
| **core/runtime/__init__.py** | 100% | ✅ | |
| **llm/__init__.py** | 100% | ✅ | |
| **os/__init__.py** | 100% | ✅ | |
| **tui/__init__.py** | 100% | ✅ | |
| **tui/widgets/__init__.py** | 100% | ✅ | |
| **core/memory/context.py** | 92% | ✅ | 上下文管理 |
| **tools/__init__.py** | 86% | ✅ | 工具注册 |
| **core/runtime/decoder.py** | 86% | ✅ | 指令解码 |
| **os/gate.py** | 84% | ✅ | 权限门控 |
| **core/scheduler.py** | 82% | ✅ | DAG 调度 |
| **core/memory/mmu.py** | 82% | ✅ | 内存管理 |
| **core/runtime/vcpu.py** | 74% | ⚠️ | vCPU 执行 |
| **tools/read.py** | 74% | ⚠️ | |
| **core/compaction.py** | 73% | ⚠️ | 上下文压缩 |
| **tools/write.py** | 72% | ⚠️ | |
| **tools/edit.py** | 68% | ⚠️ | |
| **tools/glob.py** | 67% | ⚠️ | |
| **agentos.py** | 66% | ⚠️ | 主入口 |
| **tools/grep.py** | 66% | ⚠️ | |
| **core/session.py** | 61% | ⚠️ | 会话管理 |
| **tui/widgets/chatbox.py** | 57% | ⚠️ | |
| **llm/gemini.py** | 53% | ⚠️ | |
| **tui/widgets/prompt_input.py** | 49% | ❌ | |
| **tools/bash.py** | 44% | ❌ | |
| **llm/openrouter.py** | 39% | ❌ | |
| **tui/app.py** | 32% | ❌ | |
| **llm/anthropic.py** | 28% | ❌ | |
| **adapters/__init__.py** | 0% | ❌ | |
| **adapters/pi_adapter.py** | 0% | ❌ | 需要 pi-ai 服务 |
| **bridge/__init__.py** | 0% | ❌ | |
| **bridge/pi_ai_http.py** | 0% | ❌ | 需要 pi-ai 服务 |
| **bridge/pi_client.py** | 0% | ❌ | 已废弃 |
| **server/__init__.py** | 0% | ❌ | |
| **server/api_ai_sdk.py** | 0% | ❌ | 需要服务器 |
| **server/api_openai.py** | 0% | ❌ | 需要服务器 |
| **server/app.py** | 0% | ❌ | 需要服务器 |
| **llm/retry.py** | 0% | ❌ | |

### 总结

- **总覆盖率**: 50%
- **测试结果**: 194 passed, 6 failed, 5 skipped
- **高覆盖 (>70%)**: 12 个模块
- **中等覆盖 (40-70%)**: 12 个模块
- **低覆盖 (<40%)**: 17 个模块

### 需要补充测试的模块

1. **adapters/pi_adapter.py** - 需要 mock pi-ai HTTP 服务
2. **bridge/pi_ai_http.py** - 需要 mock HTTP 客户端
3. **server/*.py** - 需要集成测试
4. **llm/anthropic.py** - 需要 mock API
5. **llm/retry.py** - 需要单元测试

## 迁移步骤

### Phase 1: 移动独立模块（无冲突）

```bash
# 1. 移动 adapters/
mv src/nimbus/v2/adapters src/nimbus/

# 2. 移动 bridge/
mv src/nimbus/v2/bridge src/nimbus/

# 3. 移动 llm/
mv src/nimbus/v2/llm src/nimbus/

# 4. 移动 os/
mv src/nimbus/v2/os src/nimbus/

# 5. 移动 agentos.py
mv src/nimbus/v2/agentos.py src/nimbus/
```

### Phase 2: 合并到现有目录

```bash
# 1. 合并 core/ (需要处理冲突)
mv src/nimbus/v2/core/compaction.py src/nimbus/core/
mv src/nimbus/v2/core/protocol.py src/nimbus/core/
mv src/nimbus/v2/core/scheduler.py src/nimbus/core/
mv src/nimbus/v2/core/session.py src/nimbus/core/
mv src/nimbus/v2/core/memory src/nimbus/core/
mv src/nimbus/v2/core/runtime src/nimbus/core/

# 2. 合并 tools/ (用 v2 版本替换)
mv src/nimbus/v2/tools/bash.py src/nimbus/tools/
mv src/nimbus/v2/tools/edit.py src/nimbus/tools/
mv src/nimbus/v2/tools/glob.py src/nimbus/tools/
mv src/nimbus/v2/tools/grep.py src/nimbus/tools/
mv src/nimbus/v2/tools/read.py src/nimbus/tools/
mv src/nimbus/v2/tools/write.py src/nimbus/tools/

# 3. 移动 tui/
rm -rf src/nimbus/tui  # 删除旧的（如果存在）
mv src/nimbus/v2/tui src/nimbus/

# 4. 合并 server/
mv src/nimbus/v2/server/api_ai_sdk.py src/nimbus/server/
mv src/nimbus/v2/server/api_openai.py src/nimbus/server/
# v2/server/app.py 需要手动合并
```

### Phase 3: 更新导入路径

需要更新所有 `from nimbus.v2.xxx` 为 `from nimbus.xxx`:

```bash
# 查找所有需要更新的文件
grep -rn "from nimbus.v2" src/nimbus/ tests/
grep -rn "import nimbus.v2" src/nimbus/ tests/
```

### Phase 4: 更新 __init__.py

1. `src/nimbus/__init__.py` - 更新顶层导出
2. `src/nimbus/core/__init__.py` - 添加新模块导出
3. `src/nimbus/tools/__init__.py` - 更新工具导出

### Phase 5: 清理

```bash
# 删除空的 v2 目录
rm -rf src/nimbus/v2

# 运行测试验证
pytest tests/test_v2*.py -v
```

## 风险点

1. **导入路径变更** - 需要更新所有引用 `nimbus.v2` 的代码
2. **循环导入** - 合并 core/ 时可能产生循环导入
3. **命名冲突** - tools/ 中的 v2 版本和旧版本可能有冲突
4. **测试更新** - 测试文件中的导入路径也需要更新

## 验证清单

- [ ] `python -c "from nimbus import AgentOS; print('OK')"`
- [ ] `./nimbus start --no-ui` 服务正常启动
- [ ] `curl http://localhost:4096/health` 返回 healthy
- [ ] `pytest tests/test_v2*.py -v` 通过
- [ ] E2E 测试 `python tests/e2e_tool_call.py` 通过
