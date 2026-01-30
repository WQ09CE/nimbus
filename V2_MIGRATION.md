# V2 Migration - Web UI 现在使用 AgentOS

## 🎯 已完成

今天完成了从 v1 CodeAgent 到 v2 AgentOS 的迁移。

### 改动内容

1. **创建 SessionManagerV2** (`server/session_v2.py`)
   - 使用 `AgentOS` 代替 `CodeAgent`
   - 每个 session 对应一个 AgentOS 实例
   - 支持 SSE 流式事件推送

2. **修改 app.py**
   - 启动时使用 `SessionManagerV2` 代替 `SessionManager`
   - CORS 配置支持 `:3000` 端口

3. **修改 api.py chat 端点**
   - 使用 `session_manager.stream_chat()` 方法
   - 通过 SSEHub queue 实时转发事件

4. **Web UI 端口改为 3000**
   - `package.json` 端口改为 3000
   - 与 Pi TUI 端口对齐

---

## 📊 架构对比

### V1 (旧版)
```
Web UI → FastAPI → SessionManager → CodeAgent
                                    ├── Planner (Rule/Context/LLM)
                                    ├── Runtime (DAG)
                                    └── Memory (Tiered)
```

### V2 (新版)
```
Web UI → FastAPI → SessionManagerV2 → AgentOS
                                       ├── VCPU (Think-Act-Observe)
                                       ├── MMU (Memory Management)
                                       ├── Gate (Tool Execution)
                                       └── Scheduler (DAG)
```

---

## 🔑 核心差异

| 特性 | V1 | V2 |
|------|----|----|
| **核心** | CodeAgent | AgentOS |
| **内存** | TieredMemory | MMU (Memory Management Unit) |
| **规划** | Pipeline (Rule→Context→LLM) | VCPU + Scheduler |
| **工具** | 直接调用 | Gate (统一入口 + 权限) |
| **事件** | 直接 yield | Event + EventStream |
| **会话** | 手动管理 | SessionManager (内置) |
| **压缩** | 无 | CompactionEngine (自动) |

---

## 🚀 启动方式

### 方式 1: 自动启动脚本

```bash
cd ~/sourcecode/agent/agent-framework/nimbus
./run-web.sh
```

### 方式 2: 手动启动

**终端 1 - Nimbus Server (v2):**
```bash
cd ~/sourcecode/agent/agent-framework/nimbus
nimbus serve --port 4096
```

**终端 2 - Web UI:**
```bash
cd ~/sourcecode/agent/agent-framework/nimbus/web-ui
npm run dev
```

然后访问: **http://localhost:3000**

---

## 🧪 测试 SSE 兼容性

### 创建 Session

```bash
curl -X POST http://localhost:4096/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"name": "test-v2"}'
```

### 发送消息 (SSE 流)

```bash
curl -X POST http://localhost:4096/api/v1/sessions/sess_xxx/chat \
  -H "Content-Type: application/json" \
  -d '{"content": "你好"}' \
  -N
```

应该看到流式事件：
```
event: connected
data: {"session_id": "sess_xxx", ...}

event: message_start
data: {"role": "assistant"}

event: message
data: {"content": "你"}

event: message
data: {"content": "好"}

event: dag_complete
data: {"status": "OK"}
```

---

## 📦 文件清单

### 新增文件
- `src/nimbus/server/session_v2.py` - SessionManagerV2 (AgentOS wrapper)

### 修改文件
- `src/nimbus/server/app.py` - 使用 SessionManagerV2
- `src/nimbus/server/api.py` - chat 端点改用 SSE queue
- `web-ui/package.json` - 端口改为 3000
- `web-ui/.env.local` - 标注 v2

---

## ✅ 验证清单

- [x] Session 创建成功
- [x] Web UI 端口改为 3000
- [x] CORS 配置正确
- [x] Server 启动成功
- [ ] SSE 流式输出正常 (待测试)
- [ ] Tool call 可视化 (待测试)
- [ ] 多轮对话记忆 (待测试)

---

## 🐛 已知问题

1. **事件映射**
   - v2 的事件类型与 v1 不完全匹配
   - 目前在 `_emit_v2_event()` 做了简单映射
   - 可能需要更精细的事件转换

2. **流式粒度**
   - v2 AgentOS 的 `chat()` 方法是同步的
   - 目前通过 SSEHub queue 模拟流式
   - 可能需要改进 AgentOS 支持真正的异步流

3. **工具注册**
   - v2 需要显式注册工具 (`register_default_tools`)
   - 目前已在 SessionManagerV2 中自动注册

---

## 🔮 下一步

1. **测试完整流程**
   - 在浏览器中测试多轮对话
   - 验证 tool call 是否正常显示
   - 检查内存是否正确保持

2. **优化流式体验**
   - 考虑在 VCPU 层面添加流式支持
   - 实现更细粒度的事件推送

3. **性能对比**
   - v1 vs v2 响应时间
   - 内存使用情况
   - 并发性能

---

## 📝 备注

**Pi Extension** (`pi-extension/nimbus_server.py`) 已经在用 v2 架构，所以 v2 本身是经过验证的。

现在 Web UI 和 Pi TUI 都在使用 v2 AgentOS 作为核心了！🎉
