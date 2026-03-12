# AI Council Report: 我们来热身一下， 帮忙各抒己见， 现在从极简，高效的角度出发， 多 agent 框架可以简单到什么程度

2026/3/12 13:12:25

### Opinion from Claude Opus 4.5 (Deep Thinker)

# 多 Agent 框架的极简之道

从 Nimbus 项目的设计哲学出发，我来分享几个层次的"极简"观点：

---

## 🎯 极简本质：Agent = LLM + Tool + Loop

最简形式只需要三个要素：

```python
# 极简 Agent（约 30 行）
while not done:
    response = llm.chat(messages)      # Think
    tool_call = parse(response)        # Decide
    result = execute(tool_call)        # Act
    messages.append(result)            # Observe
```

**多 Agent？只是多个循环而已。**

---

## 📐 三种极简架构模式

| 模式 | 复杂度 | 核心思想 |
|------|--------|----------|
| **1. Tool-as-Agent** | ⭐ | Agent 调用另一个 Agent，就像调用工具一样 |
| **2. Fork 模式** | ⭐⭐ | 子进程 spawn，Unix 哲学，stdout/stdin 通信 |
| **3. 消息队列** | ⭐⭐⭐ | 发布/订阅，解耦但引入复杂性 |

Nimbus 选择了 **Tool-as-Agent** 模式：

```python
spawn_agent(role="Tester", task="写单元测试", mode="sync")
```

这就是最简单的多 Agent —— **一个工具调用**。

---

## 🔥 我的极简主张

### 可以砍掉的：
- ❌ 复杂的 Agent 通信协议（直接函数调用）
- ❌ 中央调度器（让 LLM 自己决定何时 spawn）
- ❌ 共享状态管理（文件系统就是最好的共享存储）
- ❌ Agent 发现/注册机制（硬编码角色列表）

### 必须保留的：
- ✅ **上下文隔离**（子 Agent 独立 MMU，防止 token 爆炸）
- ✅ **输出截断**（大返回走磁盘 scratchpad）
- ✅ **超时控制**（防止子 Agent 无限循环）

---

## 💡 理论极限：单文件实现

```
┌─────────────────────────────────────┐
│  main_agent.py (~200 行)            │
│  ├── llm_call()                     │
│  ├── tool_execute()                 │
│  ├── spawn_sub_agent()  ← 递归调用  │
│  └── main_loop()                    │
└─────────────────────────────────────┘
```

**本质洞见**：多 Agent 不需要"框架"，只需要：
1. 一个能调用自己的函数
2. 隔离的上下文
3. 结果传递机制（返回值/文件）

---

## 🤔 反思：为什么大家把它做复杂了？

| 过度设计 | 极简替代 |
|----------|----------|
| Agent 编排 DSL | 让 LLM 自己编排 |
| 复杂状态机 | 简单 while 循环 |
| 微服务架构 | 单进程 + subprocess |
| 向量数据库记忆 | 文件 + grep |

---

**一句话总结**：

> 多 Agent 框架可以简单到 **"一个递归的工具调用"**，所有复杂性都是为了解决规模化和容错问题。如果你的场景不需要，就不要引入。

你对哪个方向最感兴趣？可以深入聊聊具体实现。


### Opinion from GPT-5.2 (Generalist)

(Error: Exit Code undefined)
file:///opt/homebrew/lib/node_modules/@mariozechner/pi-coding-agent/dist/core/agent-session.js:638
            throw new Error(`No API key found for ${this.model.provider}.\n\n` +
                  ^

Error: No API key found for azure-openai-responses.

Use /login or set an API key environment variable. See /opt/homebrew/lib/node_modules/@mariozechner/pi-coding-agent/docs/providers.md
    at AgentSession.prompt (file:///opt/homebrew/lib/node_modules/@mariozechner/pi-coding-agent/dist/core/agent-session.js:638:19)
    at process.processTicksAndRejections (node:internal/process/task_queues:104:5)
    at async runPrintMode (file:///opt/homebrew/lib/node_modules/@mariozechner/pi-coding-agent/dist/modes/print-mode.js:69:9)
    at async main (file:///opt/homebrew/lib/node_modules/@mariozechner/pi-coding-agent/dist/main.js:678:9)

Node.js v25.7.0


### Opinion from Gemini 3 Pro High (Reasoning)

(Error: Exit Code undefined)
Model "gemini-3-pro-high" not found. Use --list-models to see available models.
