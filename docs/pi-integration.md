# Nimbus + Pi 集成方案

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Nimbus Core (Python)                          │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  AgentOS                                                     ││
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ ││
│  │  │    vCPU     │  │     MMU     │  │      Session        │ ││
│  │  │ Agent Loop  │  │Context Stack│  │    Persistence      │ ││
│  │  │ Doom Detect │  │  GC/Filter  │  │    JSONL Tree       │ ││
│  │  └──────┬──────┘  └─────────────┘  └─────────────────────┘ ││
│  │         │                                                    ││
│  │         │ LLMClient interface                                ││
│  │         ▼                                                    ││
│  │  ┌─────────────────────────────────────────────────────────┐││
│  │  │              PiLLMAdapter / PiIOAdapter                  │││
│  │  │          nimbus/v2/adapters/pi_adapter.py               │││
│  │  └──────────────────────────┬──────────────────────────────┘││
│  └─────────────────────────────┼────────────────────────────────┘│
└────────────────────────────────┼────────────────────────────────┘
                                 │
                                 │ JSON-RPC over stdin/stdout
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Pi Bridge (Node.js)                           │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    nimbus/bridge/pi-bridge.ts                ││
│  │  ┌─────────────────────────┐  ┌───────────────────────────┐ ││
│  │  │         pi-ai           │  │          pi-tui           │ ││
│  │  │  • stream()             │  │  • render_markdown()      │ ││
│  │  │  • complete()           │  │  • get_input()            │ ││
│  │  │  • getModels()          │  │  • select() / confirm()   │ ││
│  │  │  • 多 Provider 支持      │  │  • notify()               │ ││
│  │  └─────────────────────────┘  └───────────────────────────┘ ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## 文件结构

```
nimbus/
├── bridge/                          # Node.js 桥接层
│   ├── package.json
│   ├── pi-bridge.ts                 # JSON-RPC 服务
│   └── dist/                        # 编译输出
│
├── src/nimbus/v2/
│   ├── bridge/                      # Python RPC 客户端
│   │   ├── __init__.py
│   │   └── pi_client.py             # PiClient, PiAI, PiTUI
│   │
│   ├── adapters/                    # 适配器
│   │   ├── __init__.py
│   │   └── pi_adapter.py            # PiLLMAdapter, PiIOAdapter
│   │
│   └── core/                        # Nimbus 核心（不变）
│       ├── memory/mmu.py
│       ├── runtime/vcpu.py
│       ├── session.py
│       └── compaction.py
│
└── examples/
    └── nimbus_with_pi.py            # 使用示例
```

## 快速开始

### 1. 安装 Bridge 依赖

```bash
cd nimbus/bridge
npm install
npm run build
```

### 2. 使用示例

```python
from nimbus.v2.adapters import PiLLMAdapter, PiLLMConfig, PiIOAdapter
from nimbus.v2.core.memory.mmu import MMU, MMUConfig

async def main():
    # 配置 LLM
    config = PiLLMConfig(
        provider="anthropic",
        model_id="claude-sonnet-4-20250514",
    )
    
    # 初始化 MMU（你的 Context Stack）
    mmu = MMU(config=MMUConfig(
        auto_detect_failures=True,
        auto_extract_on_pop=True,
    ))
    
    # 启动 Pi 适配器
    async with PiLLMAdapter(config) as llm:
        io = PiIOAdapter(llm._client)
        
        # Agent 循环
        while True:
            user_input = await io.input("> ")
            if user_input == "exit":
                break
            
            # 添加到 Context Stack
            mmu.add_user_message(user_input)
            
            # 过滤失败的 tool calls
            context = mmu.assemble_context(filter_discardable=True)
            
            # 调用 LLM
            async for event in llm.stream(context):
                if event.type == "text":
                    await io.print_streaming(event.text)
```

## JSON-RPC 协议

### AI 方法

| Method | Params | Returns |
|--------|--------|---------|
| `ai.stream` | `{provider, modelId, messages, options}` | 通过 notification 返回事件 |
| `ai.complete` | `{provider, modelId, messages, options}` | `{content, usage}` |
| `ai.getModels` | - | `[{provider, id, name}]` |

### TUI 方法

| Method | Params | Returns |
|--------|--------|---------|
| `tui.render` | `{type, content}` | - |
| `tui.getInput` | - | `string` |
| `tui.notify` | `{message, type}` | - |
| `tui.select` | `{title, options}` | `string \| null` |
| `tui.confirm` | `{title, message}` | `boolean` |

### Streaming 事件

通过 JSON-RPC notification 推送：

```json
{"jsonrpc": "2.0", "method": "ai.streamEvent", "params": {"type": "text", "text": "Hello"}}
{"jsonrpc": "2.0", "method": "ai.streamEvent", "params": {"type": "stop", "reason": "end"}}
```

## 优势

1. **Nimbus 保持 Python** - 你的核心逻辑不用改
2. **复用 Pi 生态** - pi-ai 的多 provider 支持，pi-tui 的渲染能力
3. **解耦清晰** - JSON-RPC 是标准协议，易于调试
4. **可扩展** - 未来可以添加更多 Pi 功能（Extension、Session 等）

## 未来扩展

- [ ] 复用 Pi 的 Extension 系统
- [ ] 复用 Pi 的 Tool 定义
- [ ] 共享 Session 格式
- [ ] 双向事件通知
