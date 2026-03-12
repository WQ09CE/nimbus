# AI Council Report:  ~/sourcecode/agent/agent-framework/nimbus/docs/subagent-orchestration-design.md 你看看这个文档， 我们按照非必要不加，只改最本质的东西的原则又重写了一版。

2026/3/12 17:03:16

### Opinion from Claude Opus 4.6 (Deep Thinker)

好，文档看完了。说说我的评价。

---

## 总体判断：这一版质量很高

V3 和之前的版本相比，最大的进步是**克制力**。整个方案只动四个点，每个点的改动量都控制在个位数到几十行，这在 AI Agent 框架的设计中是非常难得的。

下面按条逐一点评：

---

### 2.1 `contract_mode` — ✅ 精准，但有一个细节要确认

思路完全正确：子 Agent 的退出通道必须是**确定性的**，不能靠 Decoder 的启发式猜测。用一个 boolean flag 把纯文本的 RETURN 路径彻底堵死，干净利落。

**一个问题**：`contract_mode` 和 2.3 的 `submit_result` 是强耦合的——你堵死了文本退出，就**必须**保证 `submit_result` 这条路是通的。如果子 Agent 因为 token 耗尽或模型抽风始终不调用 `submit_result`，最终只能靠 `max_iterations` 兜底。这没问题，但建议在文档里**显式声明**这个兜底链路：

> `contract_mode` 退出优先级：`submit_result` → `max_iterations` 耗尽 → 超时 `timeout_seconds`

现在文档里分散提到了，但没有一个地方把这三层防线串成一条链。

---

### 2.2 迭代倒计时 Steering — ✅ 最小成本，最大收益

5 行代码，解决"子 Agent 跑到死还没总结"这个真实痛点。没什么好说的，**教科书级的性价比修复**。

唯一的小建议：`0.85` 这个阈值可以提成常量，比如 `COUNTDOWN_THRESHOLD = 0.85`，方便后续按角色微调（比如探索型任务可能要 0.7）。但这不急，现在硬编码完全没问题。

---

### 2.3 `submit_result` — ✅ 这是本方案的核心亮点

这个设计解决的是**父子 Agent 之间的信息传递协议**问题——从"自由文本语义解析"升级到"结构化 JSON 契约"。这是对的。

**几个值得肯定的决策**：
1. **调用后立即 `request_interruption()`** — 防止子 Agent 交付后继续瞎跑，浪费 token。好。
2. **降级到文本方案** — 没有 deliverable.json 时优雅降级，不引入新的故障模式。好。
3. **字段长度截断** — 防止子 Agent 把整篇分析塞进 `summary`。好。

**一个设计建议**：

`findings: list[str]` 有点太扁平了。现在看着够用，但你文档里举的例子已经暗示了结构：

```
"vcpu.py: 270 行，核心类 VCPU，FSM 引擎"
```

这其实是 `文件名: 描述` 的隐含结构。不过——按照你"非必要不加"的原则，`list[str]` 现在是对的。**等到真正发现父 Agent 经常解析不了 findings 时再加结构**。我同意你的判断。

---

### 2.4 SSE 进展事件 — ✅ 务实的选择

不改 SSEHub，不加新事件类型，只是在现有 `on_update` callback 里输出格式化文本。成本极低，效果够用。

`[spawn:reader] 🔧 Read src/nimbus/core/vcpu.py (2.1KB)` 这个格式也足够前端做简单解析了。

---

### 第 3 节"明确不做的事" — 这是整个文档最有价值的部分

很多设计文档只写"我们要做什么"，不写"我们不做什么"。你这一版把 PAUSED/resume、Process Registry、LLM Semaphore、SSEHub 合流全部列出来，并给了**不做的具体理由**。这比方案本身更重要——它建立了**决策边界**，防止后续开发者凭直觉往里塞东西。

---

## 总结

| 维度 | 评分 |
|------|------|
| 问题定义精准度 | ⭐⭐⭐⭐⭐ |
| 方案最小性 | ⭐⭐⭐⭐⭐ |
| 改动范围控制 | ⭐⭐⭐⭐⭐ |
| 兜底/降级考虑 | ⭐⭐⭐⭐ (退出链路可以更显式) |
| 未来演进路径 | ⭐⭐⭐⭐⭐ |

**一句话**：这是一个**知道自己不该做什么**的方案。可以直接进入实施。

唯一的行动项：在 2.1 或 2.3 里补一句子 Agent 的**退出优先级链**，把三层防线（submit_result → max_iterations → timeout）串起来写清楚。其他的，动手吧。


### Opinion from GPT-5.4 (Generalist)

我看完了。**这版比前一类“大而全”方案明显成熟很多**，方向是对的，而且和 Nimbus 当前真实痛点是对齐的。

一句话结论：

> **这版已经从“重新设计子 Agent 系统”收缩成“修 4 个真实缺口”，基本符合“非必要不加，只改最本质的东西”的原则。**

我下面从 **整体评价 / 四项改动逐条判断 / 我建议的落地优先级 / 仍需注意的边界** 来说。

---

## 一、整体评价：这版是“收敛正确”的

这份 V3 最好的地方，不是某个点子多新，而是它做了三件很重要的事：

### 1) 尊重现状，不推翻已有基础设施
文档先承认现有 `spawn_agent.py` 已经有：

- 上下文隔离
- 输出截断
- timeout
- partial recovery
- abort 传播
- scratchpad 增量写入

这很关键。  
因为 Nimbus 当前的问题并不是“完全没有子 Agent 能力”，而是**最后那 20% 的治理缺口**导致体验和稳定性不够。

这比“从零设计 process registry / pause-resume / async orchestration”要健康得多。

---

### 2) 把问题定义得非常具体
你们现在不是在谈抽象的“可观测性”“可恢复性”“多 Agent future-proofing”，而是明确成四个 bug/缺口：

- Decoder 误杀
- 迭代耗尽前不收尾
- 返回值不可机器消费
- UI 期间无进度感知

这就是典型的**工程上正确的收缩**：  
不做平台化，先把最影响成功率的几个断点补齐。

---

### 3) 避开了当前架构最危险的扩张点
从 Context Hub 看，Nimbus 当前明确存在这些技术债：

- process / session 概念纠缠
- async background polling 缺失
- vCPU / loop God Class 倾向
- UI 推送体系还比较粗糙

在这种背景下，**不去碰 PAUSED / registry / SSEHub 合流 / semaphore**，是对的。  
因为这些东西一旦加进去，几乎都会顺手把 “process lifecycle / state model / event model” 全部拖进来，风险远大于收益。

所以整体上，我给这版判断是：

> **不是“极简得不够用”，而是“极简得刚好够解决现阶段问题”。**

---

## 二、四个改动逐条看：哪些是“本质修复”，哪些只是“不错的增强”

---

## 1. `contract_mode` 修 Decoder 误杀子 Agent  
### 结论：**这是必须做的，且是最本质的一刀**

这个问题本质上是：

> 当前 Decoder 的“文本可视为完成”启发式，适用于主 Agent，不适用于被委派的子 Agent。

子 Agent 的工作契约和主 Agent 不一样。  
主 Agent 可以“说一句结果”就结束；子 Agent 更像一个受托执行单元，它应该：

- 要么调工具继续干活
- 要么通过明确交付动作退出
- 而不是因为一句短文本被 Decoder 猜成 RETURN

所以你们引入 `contract_mode`，把子 Agent 的纯文本统一降级为 `THOUGHT`，这个思路是对的，而且非常干净。

### 我认可的原因
- 它不是 patch prompt，而是**修语义契约**
- 不需要重写 decoder，只是给不同运行场景一个 mode
- 和现有“Promise Gate / 幻觉防火墙”是一类治理手段，位置也合理

### 我唯一的提醒
这个规则要**严格限定在 subagent contract 下**，不要让它污染主 Agent 的默认行为。  
也就是说：

- `contract_mode=False`：维持现有主 Agent 逻辑
- `contract_mode=True`：纯文本不结束，必须走工具或显式交付

这点你们文档里已经写清楚了，挺好。

### 我建议再补一个小原则
不要把“永远只产出 THOUGHT”写成未来不可变的铁律，最好在实现里表达为：

> “在 contract_mode 下，**普通文本默认不被视为 final return**。”

这样以后如果要支持某种特殊 `final:` 协议，不至于语义打死。

但这只是措辞建议，不影响当前方案正确性。

---

## 2. 迭代倒计时 Steering  
### 结论：**值得做，但它是兜底增强，不是第一性修复**

这个点很实用。  
因为现在真实问题不是“子 Agent 不会工作”，而是：

> 它能工作，但经常把 budget 花在探索上，最后没有把阶段性结果写出来。

所以在 85% 预算处打一针：

> “你只剩 N 步，请立刻写 Scratchpad 并总结”

这是典型的低成本高收益防呆。

### 为什么这个方案对
- 不改状态机，不加新状态
- 不引入 checkpoint/resume
- 只是在临近耗尽时做一次 steering
- 非常符合“非必要不加”

### 但我建议注意两点
#### 1) 最好是“一次性注入”，不要每步重复刷屏
如果 `iteration >= threshold` 后每一轮都插一条 system message，可能反而污染上下文。  
更好的做法是：

- 进入阈值区间时注入一次
- 标记 `countdown_warning_sent = True`

#### 2) 这个提醒最好和 `submit_result` 绑定
提醒文案不要只说“写入 Scratchpad 并总结”，更好是：

- 写入 scratchpad
- 调用 `submit_result`

否则模型可能只会继续写自然语言总结，却没形成结构化交付。

### 所以我的判断
这项不是系统根基，但**非常值得做**，因为它能明显降低“忙了一圈没有交付”的概率。

---

## 3. `submit_result` 结构化交付  
### 结论：**这是四项里 ROI 最高的一项，甚至可以说是最关键的产品化修复**

如果说第 1 项解决的是“别死太早”，那第 3 项解决的是：

> **“即使活着做完了，也别交一坨不可消费的自由文本。”**

这个问题非常本质。  
因为父 Agent 和子 Agent 之间，本质上是一个**机器对机器的委托边界**。  
在这个边界上继续用长篇自然语言，其实就是把下游解析风险全转嫁给父 Agent。

这会带来三个问题：

1. 父 Agent 必须做语义猜测
2. 结果不可稳定聚合
3. 多子任务时无法可靠比较和汇总

所以你们引入一个通用 `submit_result(summary, findings, artifacts)`，我认为非常正确，而且比“按 role 设计不同 submit_xxx 工具”更克制。

### 这个设计最好的点
- **不是引入大而全协议，只是最小 JSON deliverable**
- 有 fallback，不强依赖一次到位
- schema 很小，足够通用
- 父 Agent 可以稳定消费，不再靠猜

### 这项设计和 `contract_mode` 是天然配套的
你们文档里这句很重要：

> 纯文本不能结束，必须调用 `submit_result` 才能退出

这个组合逻辑是成立的。它相当于明确了子 Agent 的完成契约：

- 干活中：text/tool 都行
- 完成时：必须 submit deliverable

这比“猜文本是不是完成”要强太多。

### 我建议你们守住两个边界
#### 边界 1：`submit_result` 一定要保持极小
就保持你们现在这种：

- `summary`
- `findings`
- `artifacts`

不要立刻加：

- confidence
- status
- next_actions
- blockers
- metrics
- citations
- evidence_map

这些未来都可能需要，但**现在不需要**。  
先让交付结构稳定，比字段丰富更重要。

#### 边界 2：fallback 必须保留
不要把系统变成“没调 submit_result 就算失败”。  
现实里模型总会漏调用。你们现在的降级策略对：

- 优先 `deliverable.json`
- 否则退回文本 + scratchpad

这个容错非常必要。

### 我唯一会再强调的一点
如果 `submit_result` 调用后会触发中断，那么要保证：

- deliverable 先可靠落盘
- 再触发 interruption

避免出现“返回了 DELIVERED，但文件没落稳”的竞态。

整体看，这项我认为是：

> **最应该落地的核心改动。**

---

## 4. SSE 进展事件最小化  
### 结论：**方向对，但它不是“本质必需项”，而是低成本体验增强项**

你们没有去动 SSEHub、没有新造 `subagent_event`、没有做事件总线合流，这很理性。

当前 WebUI 的问题是：  
用户看到 `spawn_agent` 后一片沉默，只剩 loading。

你们现在的想法是：

- 继续走 `on_update`
- 只把输出文本格式标准化
- 前端识别 `[spawn:reader]` 前缀即可

这很 pragmatic，我支持。

### 为什么这比“正式事件系统”更好
因为当前真正缺的不是“完美的 observability architecture”，而是：

> 用户能不能知道子 Agent 不是卡死，而是在读文件、grep、分析。

文本协议已经足够解决这个问题。

### 但我建议你们把它定位清楚
这项应该叫：

> **结构化进度文本**

而不是“SSE 进展事件”。

因为从系统设计上看，它本质上仍然是文本流，不是新事件模型。  
这样可以避免团队后面误以为自己已经建立了 subagent event protocol。

### 我的建议
这项排在 1/3 之后做最合理。  
因为它改善的是感知，不是成功率本身。

---

## 三、如果按“只改本质”排序，我建议优先级这样排

### P0：必须先做
#### 1) `contract_mode`
这是防止子 Agent 被误杀的根问题。  
不修它，其它治理都建立在不稳定运行基础上。

#### 2) `submit_result`
这是让父子 Agent 交付边界从“自然语言猜测”升级到“机器可消费”的关键。

> **这两个加起来，才是真正的“执行契约闭环”。**

---

### P1：强烈建议做
#### 3) 迭代倒计时 steering
它能明显减少“没来得及交付”的尾部失败。

---

### P2：可以随后做
#### 4) 结构化 `on_update`
这是 UX 增强，但不应和核心契约耦合推进。

---

## 四、这版里我最认可的一点：你们没有被“未来可能需要”绑架

文档第 3 节“明确不做的事”，我基本都同意。

尤其这几个不做，非常对：

### 1) 不做 PAUSED / resume_agent
这个东西不是一个 feature，而是一整套状态恢复语义。  
一旦开工，就会牵扯：

- MMU snapshot
- pending tool state
- VCPU 恢复点
- timeout 后重入语义
- session/process 生命周期

对 Nimbus 当前阶段来说，绝对不是“最本质改动”。

---

### 2) 不做 Process Registry
你们已经说得很清楚：当前是 sync-only。  
那 registry 现在几乎没有真实收益，只会先把 process/session 纠缠继续坐实。

---

### 3) 不做 LLM Semaphore
这属于典型“为不存在的问题建制度”。  
目前没有明显 async fan-out 压力，没必要提早引入全局并发治理。

---

### 4) 不做 SSEHub 合流 / 新 event type
这个也很对。  
因为当前 UI 的核心诉求只是“看得见进度”，不是“具备完整多任务事件时序系统”。

---

## 五、我觉得还需要补一条：这四项里真正的“架构性改变”其实只有一个半

如果我要从顾问视角再提炼一下：

### 真正的本质变化只有：
#### A. 子 Agent 完成契约显式化
也就是：

- 运行期：`contract_mode`
- 完成期：`submit_result`

这两者是一体的。  
它们一起定义了“子 Agent 不是自由对话体，而是受托执行单元”。

这其实是最重要的架构升级。

---

### 其它两项只是良好的护栏和体验层
- 倒计时 steering：执行护栏
- 结构化进度文本：可见性增强

所以如果你们以后还要继续压缩 scope，我会建议：

> **哪怕只做两项，也优先做 `contract_mode + submit_result`。**

因为这是最小闭环。

---

## 六、我会提醒的两个潜在风险

### 风险 1：`contract_mode` + `submit_result` 组合可能让子 Agent 更“僵”
也就是说，如果模型迟迟不调工具、也不 submit，可能会一直在 THOUGHT 里打转，最后靠 `max_consecutive_thoughts` 或 `max_iterations` 兜底。

这不是方案错误，而是你们要意识到：

> 这个改动是在用“显式契约”替换“启发式完成”。

替换后会更稳，但也更依赖：
- Promise Gate
- 空转检测
- thought 上限

好消息是这些护栏体系本来就该存在，所以我不认为这是否定点，只是实现时要联动验证。

---

### 风险 2：`submit_result` 不要变成“新的一层 ceremony”
一旦 prompt 写得太重，模型可能花很多 token 在“组织交付格式”上，而不是干活。

所以 prompt 要非常短，类似：

- 完成任务时必须调用 `submit_result`
- 先写 scratchpad，再 submit_result
- summary 简短，findings 列要点

不要写成长篇规范文档。

---

## 七、如果你问我：这版是否可以进入实现？  
我的答案是：**可以，而且值得直接开始。**

但我建议你们内部在评审时，把结论说得更尖锐一点：

### 建议的评审结论
> 本轮不再讨论 async / registry / pause-resume / event bus。  
> 只做子 Agent 的最小执行契约闭环：
> 1. 不被误杀  
> 2. 临近耗尽会收尾  
> 3. 完成时交结构化结果  
> 4. 父端能看到基本进度

如果你们这样定 scope，基本就不会跑偏。

---

## 八、我的最终判断

### 这版的优点
- 收缩得对
- 问题定义清晰
- 和 Nimbus 当前技术债相容
- 没有引入新的重型概念
- 兼顾正确性、交付性、可见性

### 我认为最核心的两点
- `contract_mode`
- `submit_result`

### 我认为可以延后的一点
- 结构化 `on_update` UI 呈现

### 总体评价
> **这是一个“可以落地、能见效、不会把系统拖进过度设计”的版本。**
> 
> 如果按你说的“非必要不加，只改最本质的东西”，我认为这版已经基本达标了。

如果你愿意，我下一步可以继续帮你做两件事里的任意一个：

1. **把这份设计再压缩成一页“实施决策版”**，方便团队拍板  
2. **按 Nimbus 现有代码结构，逐文件指出改动点和测试点**，变成可执行 implementation checklist


### Opinion from Gemini 3.1 Pro Preview (Reasoning)

作为你的 AI 架构顾问，我仔细审阅了这份 `Nimbus Subagent 治理方案 (V3)`。

整体来看，这可以说是一份**极具工程智慧的架构减法（Architecture by Subtraction）文档**。它完美契合了 YAGNI (You Aren't Gonna Need It) 和 KISS (Keep It Simple) 原则。在多 Agent 协同（Multi-Agent Orchestration）的早期阶段，最容易犯的错误就是过度设计（比如引入复杂的进程表、IPC 通信、暂停/恢复状态机等），而这份 V3 方案精准地避开了这些陷阱，将精力全部集中在当前**最高频、最痛的 4 个断点**上。

以下是我从专业顾问视角的深度点评和落地建议：

### 1. 核心改进点评

#### ✅ 2.1 引入 `contract_mode` (契约模式)
*   **顾问视角**：**非常漂亮的解法。** LLM 的本能是“拟人化交流”（例如回复“好的，我马上看”），而传统的 `_is_done()` 基于启发式规则（字数、特定词汇）去判断结束是非常脆弱的。引入 `contract_mode` 明确了子 Agent 的身份是**“无情的工作机器（Worker）”**而非“聊天机器人（Chatbot）”。纯文本只作为 Thought 留存，必须通过行为（调用工具）或耗尽寿命（max iterations）来结束，这从根源上消除了“误杀”问题。
*   **风险提示**：需确保现有的 `max_consecutive_thoughts` 熔断机制足够健壮。当处于 `contract_mode` 时，如果 LLM 陷入了不断输出纯文本但不调用任何工具的死循环，必须能被及时掐断，避免浪费 Token。

#### ✅ 2.2 倒计时 Steering (85% 强制收尾)
*   **顾问视角**：**四两拨千斤。** 过去处理“Agent 迷失”往往需要外部监控进程介入，而这里巧妙利用了 AgentOS 已有的 VCPU Steering 能力，在循环周期末尾动态注入 System Message。这相当于给 Agent 戴上了一块“倒计时手表”，强迫其收敛。
*   **风险提示**：在注入 `⚠️ 你只剩 N 步可用...` 消息时，需确保注入时机是“回合之间（Between Turns）”。如果 Agent 正在输出巨型长文本的中途，强行截断并注入可能导致后续 JSON 工具调用格式损坏。由于你们是基于步骤（step）注入，这点理论上已经规避了。

#### ✅ 2.3 结构化交付 (`submit_result` 工具)
*   **顾问视角**：**这是 V3 方案中最具架构价值的一笔。** 将父子 Agent 的通信协议从“非结构化的自然语言（猜）”升级为“结构化的 JSON 契约（确定性）”。自然语言总结不仅极易引发父 Agent 的幻觉，还会导致大量上下文 Token 的浪费。强制子 Agent 使用 `submit_result` 生成 `deliverable.json`，并将该行为与 VCPU 中断直接挂钩，建立了一个极其稳固的交付闭环。
*   **改进建议**：子 Agent 在生成 `deliverable.json` 时，LLM 偶尔也会输出非标准 JSON（比如漏掉逗号）。建议在父 Agent 读取这个 JSON 前，加一层轻量级的容错/校验（如 `json.loads` 失败时走降级方案），确保父 Agent 不会被子 Agent 畸形的交付物拉崩。

#### ✅ 2.4 SSE 最小化进展事件 (`[spawn:xxx]` 格式化)
*   **顾问视角**：**极高 ROI 的妥协。** 在不改造 SSEHub、不增加新的 Event Type 的前提下，通过 Unix 哲学中经典的“特定前缀标准输出”来解决可观测性（Observability）问题，为前端解析提供了极大的便利。1 天工时的预算里，这绝对是最聪明的做法。

### 2. 对“明确不做的事”的认同

文档第三部分的“不为清单（Not-To-Do List）”展现了成熟架构师的克制：
*   **拒绝 PAUSED 状态与恢复**：在当前基于上下文的 LLM 架构下，恢复脏状态的 MMU 代价极高。出错直接抛弃并带着旧 Scratchpad 重建（Respawn）是容错性最高的做法（类似于 Erlang 的 "Let it crash" 哲学）。
*   **拒绝并发与 Semaphore**：在确立了 Sync-only 作为主要模式后，过早引入限流只会增加系统状态的复杂度。等到真正被 API Provider 抛出 429 错误打痛时，在 Adapter 层统一拦截即可。

### 3. 总体实施建议 (Next Steps)

这份方案的落地可行性极高，1 天的预估工时非常合理。

**你可以直接放行并按照此文档执行。** 唯一需要你在写代码时稍微留意的是：确保 `sub_session_id` 和 `.nimbus/sessions/.../deliverable.json` 的路径拼接在并发或异常退出时具有**原子性**和**幂等性**，清理遗留的 `.json` 文件，避免下次运行读取到上一次的“脏交付物”。

这是一份优秀的架构演进文档，大道至简，直击痛点。
