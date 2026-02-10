# 多模型并行评审架构方案 (AI Review Committee)

> 日期：2026-02-09
> 状态：Draft / 待讨论
> 场景：多个模型（Claude/GPT/Gemini）并行评审同一份代码/架构，由汇总模型出最终意见

---

## 1. 目标场景

```
用户: "帮我评审一下这个架构方案"
         │
         ▼
    ┌─────────┐
    │  Core   │ (Claude) — 协调者/汇总者
    │  Agent  │
    └────┬────┘
         │ 并行 fan-out
    ┌────┴────┬──────────┐
    ▼         ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐
│Reviewer│ │Reviewer│ │Reviewer│
│Claude  │ │GPT-5.2 │ │Gemini3 │
│  Pro   │ │        │ │  Pro   │
└───┬────┘ └───┬────┘ └───┬────┘
    │          │          │
    └────┬─────┴──────────┘
         │ fan-in 汇总
    ┌────▼────┐
    │  Core   │ → 最终评审意见
    │  Agent  │
    └─────────┘
```

**关键特征：**
- Reviewer 进程**不需要工具**，只需要纯 LLM 推理
- Reviewer 进程**没有 tool_call 消息**，所以不存在格式兼容问题
- Reviewer 进程是**一次性的**：传入内容，返回评审意见，结束
- 三个 Reviewer **并行执行**，总耗时 ≈ 最慢的那个

---

## 2. 现状分析

### 2.1 已有能力

| 能力 | 状态 | 位置 |
|------|------|------|
| AgentOS.spawn() 创建子进程 | ✅ 有 | `agentos.py` |
| AgentOS.wait() 等待子进程完成 | ✅ 有 | `agentos.py` |
| Scheduler DAG 并行执行 | ✅ 有 | `core/scheduler.py` |
| PiLLMAdapter 指定模型 | ✅ 有 | `adapters/pi_adapter.py` |
| pi-ai bridge 多 provider 支持 | ✅ 有 | `bridge/pi_ai_http.py` |

### 2.2 缺什么

| 缺失 | 说明 |
|------|------|
| `spawn()` 不接受 `llm_client` 参数 | Executor 只能用 Core 的模型 |
| 没有"纯 LLM 推理"进程模式 | 当前 spawn 默认带工具，评审不需要工具 |
| 没有多进程并行等待 | 只有 `wait(pid)` 单个等待，没有 `wait_all([pid1, pid2, pid3])` |
| 没有 Review Tool | Core Agent 没有"发起评审"的工具 |

---

## 3. 方案设计

### 3.1 改动一：spawn() 支持指定 LLM

**文件**: `src/nimbus/agentos.py`

```python
def spawn(
    self,
    goal: str,
    role: str = "",
    system_rules: Optional[str] = None,
    max_iterations: Optional[int] = None,
    llm_client: Optional[Any] = None,  # 新增
    tools: Optional[List] = None,       # 新增：None=继承, []=无工具
) -> str:
    # ...

    # 工具列表：支持无工具模式（纯推理）
    if tools is not None:
        tools_list = tools  # 显式指定（空列表 = 纯推理）
    else:
        tools_list = self._tools.get_definitions(format="openai", role=role)
        # ... 加 Memo

    vcpu = VCPU(
        alu=llm_client or self._llm,  # 有指定就用指定的
        tools=tools_list,
        # ...
    )
```

### 3.2 改动二：新增 wait_all() 并行等待

**文件**: `src/nimbus/agentos.py`

```python
async def wait_all(
    self,
    pids: List[str],
    timeout: Optional[float] = None,
) -> Dict[str, ToolResult]:
    """并行等待多个进程完成"""
    tasks = {
        pid: asyncio.create_task(self.wait(pid, timeout=timeout))
        for pid in pids
    }
    results = {}
    for pid, task in tasks.items():
        try:
            results[pid] = await task
        except Exception as e:
            results[pid] = ToolResult(
                status="ERROR",
                fault=Fault(domain="KERNEL", code="WAIT_FAIL", message=str(e))
            )
    return results
```

### 3.3 改动三：LLM 工厂方法

**文件**: `src/nimbus/agentos.py` 或新建 `src/nimbus/adapters/llm_factory.py`

```python
async def create_llm_client(model: str) -> PiLLMAdapter:
    """
    根据 model 字符串创建 LLM client。

    Args:
        model: "anthropic/claude-sonnet-4" 或 "openai/gpt-5.2" 或 "google/gemini-3-pro"

    Returns:
        已启动的 PiLLMAdapter
    """
    provider, model_id = model.split("/", 1)
    config = PiLLMConfig(provider=provider, model_id=model_id)
    adapter = PiLLMAdapter(config)
    await adapter.__aenter__()
    return adapter
```

### 3.4 改动四：ReviewCommittee Skill

这是用户最终使用的入口。作为一个 **Skill** 实现，Core Agent 调用它发起评审。

**文件**: `skills/review-committee/SKILL.md`

```yaml
---
name: review-committee
version: 1.0.0
description: AI Review Committee - parallel multi-model code/architecture review
tools:
  - name: ReviewCommittee
    description: "Submit code or architecture for parallel review by multiple AI models. Returns individual reviews and a synthesized summary."
    entrypoint: scripts/review.py
    args:
      content:
        type: string
        description: "The code, architecture doc, or design to review"
      focus:
        type: string
        description: "Review focus area (e.g. 'security', 'performance', 'architecture', 'all')"
      models:
        type: string
        description: "Comma-separated model list (default: anthropic/claude-sonnet-4,openai/gpt-5.2,google/gemini-3-pro)"
---
```

**但这里有个关键的设计选择——**

Skill 脚本是外部进程（`python3 scripts/review.py`），它没法直接调用 `AgentOS.spawn()`。所以有两条路：

#### 路线 A：Skill 脚本调 pi-ai bridge HTTP（独立）

```
review.py
  → 直接调 pi-ai HTTP API（3 个并行请求，3 个不同模型）
  → 自己汇总
  → 返回结果
```

优点：完全独立，不依赖 AgentOS 内核
缺点：绕过了 AgentOS 的进程管理、事件系统

#### 路线 B：内置到 AgentOS 作为原生 Meta-Tool（推荐）

类似 `DispatchTool`，作为一个**原生注册的 Meta-Tool**：

```python
# orchestration/review_tool.py

class ReviewTool:
    """AI Review Committee — 并行多模型评审"""

    def __init__(self, agent_os: AgentOS):
        self._agent_os = agent_os

    async def review(self, content: str, focus: str = "all", models: str = "") -> str:
        """
        发起并行评审。

        1. 解析模型列表
        2. 为每个模型 spawn 一个纯推理进程
        3. 并行等待所有评审完成
        4. 返回汇总结果（由 Core Agent 自己做最终汇总）
        """
        # 默认评审委员会
        model_list = models.split(",") if models else [
            "anthropic/claude-sonnet-4-20250514",
            "openai/gpt-4o",
            "google/gemini-2.5-pro",
        ]

        # 为每个模型创建 LLM client 并 spawn 评审进程
        pids = []
        for model in model_list:
            model = model.strip()
            provider_name = model.split("/")[0]

            llm = await create_llm_client(model)

            review_prompt = f"""You are a code reviewer using {model}.

## Review Focus: {focus}

## Content to Review:
{content}

## Instructions:
1. Analyze the code/architecture thoroughly
2. List specific issues found (with severity: Critical/Major/Minor)
3. List strengths
4. Give an overall assessment (1-10 score)
5. Provide actionable suggestions

Be specific, cite line numbers or sections. Be honest about limitations."""

            pid = self._agent_os.spawn(
                goal=review_prompt,
                role="reviewer",
                llm_client=llm,
                max_iterations=1,  # 纯推理，只需要 1 轮
                tools=[],          # 无工具
            )
            pids.append((model, pid))

        # 并行等待
        results = await self._agent_os.wait_all(
            [pid for _, pid in pids],
            timeout=120.0,
        )

        # 格式化输出
        output = "## 🏛️ AI Review Committee Results\n\n"
        for model, pid in pids:
            result = results.get(pid)
            review_text = result.output if result and result.output else "(No response)"
            output += f"### 📋 Review by `{model}`\n\n{review_text}\n\n---\n\n"

        output += "## 📝 Awaiting Your Synthesis\n"
        output += "Above are the individual reviews from all committee members. "
        output += "Please synthesize them into a final assessment."

        return output
```

**注册方式（跟 DispatchTool 一样）：**

```python
# session_v2.py
review_tool = ReviewTool(agent_os=agent_os)
agent_os.register_tool(
    name="ReviewCommittee",
    func=review_tool.review,
    description="Submit code/architecture for parallel multi-model review",
    parameters={...},
    roles=["core", "chat"],
)
```

---

## 4. 交互流程

```
用户: "帮我评审 src/nimbus/agentos.py 的架构"

Core Agent (Claude):
  1. 读取文件内容 → Read("src/nimbus/agentos.py")
  2. 调用 ReviewCommittee(content=文件内容, focus="architecture")

ReviewTool 内部:
  3. spawn reviewer-1 (Claude Sonnet) — 纯推理，无工具
  4. spawn reviewer-2 (GPT-5.2)       — 纯推理，无工具
  5. spawn reviewer-3 (Gemini 3 Pro)   — 纯推理，无工具
  6. wait_all([r1, r2, r3])            — 并行等待 ~30s
  7. 返回三份评审报告

Core Agent (Claude):
  8. 收到三份报告
  9. 自己做最终汇总：共识、分歧、最终建议
  10. 输出给用户
```

**为什么让 Core Agent 自己做汇总而不是 ReviewTool 内部做？**
- Core Agent 有完整的对话上下文（知道用户关心什么）
- Core Agent 可以追问用户（"你更关心安全还是性能？"）
- 保持 ReviewTool 的职责单一：只负责并行评审，不负责决策

---

## 5. 改动文件清单

| 文件 | 改动 | 工作量 |
|------|------|--------|
| `src/nimbus/agentos.py` | spawn() 加 `llm_client` + `tools` 参数 | 10 行 |
| `src/nimbus/agentos.py` | 新增 `wait_all()` | 15 行 |
| `src/nimbus/adapters/llm_factory.py` | **新建**：LLM 工厂方法 | 20 行 |
| `src/nimbus/orchestration/review_tool.py` | **新建**：ReviewTool | 80 行 |
| `src/nimbus/server/session_v2.py` | 注册 ReviewCommittee 工具 | 15 行 |
| `src/nimbus/orchestration/prompts.py` | 评审 prompt 模板 | 20 行 |

**总计：~160 行新代码，改动 2 个现有文件。**

---

## 6. 为什么不存在 Tool Call 兼容问题

这个场景巧妙地**绕过了**跨 provider tool_call 格式不兼容的问题：

| 维度 | 评审场景 | 通用多模型场景 |
|------|---------|---------------|
| Reviewer 需要工具吗？ | ❌ 纯推理 | ✅ 需要 |
| 有 tool_call 消息吗？ | ❌ 没有 | ✅ 有 |
| 需要跨模型共享消息历史吗？ | ❌ 每个独立 | ✅ 需要 |
| 存在格式兼容问题吗？ | ❌ 不存在 | ✅ 存在 |

每个 Reviewer 是独立的纯推理进程：
- 输入：一条 user 消息（评审内容 + 指令）
- 输出：一条 assistant 消息（评审意见）
- 完毕

**这就是为什么"评审委员会"是多模型协作的最佳切入点——它天然不存在消息格式兼容问题。**

---

## 7. 扩展可能

实现了评审委员会后，同样的 `spawn(llm_client=) + wait_all()` 基础设施可以支持：

| 场景 | 模式 | 工具需求 |
|------|------|---------|
| 🏛️ 代码/架构评审 | fan-out → fan-in | 无工具（纯推理） |
| 🧪 多模型测试生成 | fan-out → fan-in | 无工具 |
| 🤔 多模型头脑风暴 | fan-out → fan-in | 无工具 |
| 📝 多模型翻译对比 | fan-out → fan-in | 无工具 |
| 🔍 多模型事实核查 | fan-out → fan-in | 可选 WebSearch |

一旦需要带工具的多模型协作（最后一行），才需要解决 tool_call 兼容问题——但那是 Phase 2 的事。

---

## 8. 讨论点

### Q1: 评审模型列表放哪里配置？
- **选项 A**: `~/.nimbus/config.json` 里加 `review_committee.default_models`
- **选项 B**: ReviewTool 的 `models` 参数由 Core Agent 动态决定
- **选项 C**: 两者都支持（config 提供默认值，参数可 override）
- 推荐 C

### Q2: Reviewer 的 max_iterations 设为多少？
- 纯推理只需要 **1 轮**（输入 → 输出）
- 但如果内容很长，模型可能需要 extended thinking
- 建议设为 **1**，如果需要可以改为 2

### Q3: 评审结果要不要持久化？
- 当前方案：评审结果只在当前对话中
- 可选：写入 `docs/reviews/` 或 `.nimbus/reviews/` 持久化
- 建议 Phase 1 不做，后续按需加

### Q4: 路线 A 还是路线 B？
- **路线 A（Skill 脚本直调 HTTP）**：快速实现，不改 AgentOS 内核
- **路线 B（原生 Meta-Tool）**：改 AgentOS，但基础设施可复用
- 推荐 **路线 B**——`spawn(llm_client=)` 和 `wait_all()` 是通用基础设施，值得投入
