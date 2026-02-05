# AI Council Report: nimbus/docs/terminal-bench-review-2026-02-05.md 帮忙评审一下这个 nimbus agent 在一些比较难的任务上的表现， 最好给出一些建设性的改进意见， 我的意思是不想通过 prompt engeneering 来增强 这样毕竟是有限的

2026/2/5 23:28:03

### Opinion from Claude Opus 4.5 (Deep Thinker)

好的，我已经仔细阅读了测试报告以及 nimbus 的核心代码（vcpu.py、mmu.py、bash.py、error_handler.py、doom_loop.py、compaction.py、memo.py、agentos.py、gate.py）。下面是我的深度评审。

---

## 一、总体评价

**Nimbus 的工程质量相当不错。** vCPU 的 Think-Act-Observe 循环、MMU 的 Anchor & Stream 设计、ErrorHandlerRegistry 的分层恢复策略，这些都是成熟的架构决策。22% 的通过率看起来很低，但报告中指出的"大部分任务高通过率但差一口气"（5/7, 10/11, 2/3）恰恰说明**核心能力没有问题，问题集中在框架层面缺少的"最后一公里"机制**。

你说得对，这些问题靠 prompt engineering 解决效果有限。下面我从**框架架构层面**给出建设性改进建议。

---

## 二、问题根因分析与架构级改进

### 问题 A：自验证盲区（影响 3/7 任务）

#### 根因

当前 nimbus 的验证完全是"LLM 自由发挥"——agent 自己决定验证什么、怎么验证。看代码中 `_handle_tool_call` 的逻辑：

```python
# vcpu.py line ~480
if action.name in ("Edit", "Write") and result.status == "OK":
    output_str += (
        "\n\n[Hint] File modified successfully. "
        "If the task involves code or configuration, "
        "consider testing it with Bash before finishing."
    )
```

这只是一个文本 hint，LLM 可以忽略。而且验证的方向完全由 LLM 自主决定——kv-store-grpc 中 agent 用**自己生成的 proto** 测试**自己生成的服务**，形成了自验证的闭环（circular verification）。

#### 建议 1：引入「对抗性验证阶段」(Adversarial Verification Phase)

**核心思想：不是让 agent 验证自己，而是让框架在执行流中插入一个结构化的验证步骤。**

具体做法：在 vCPU 的 `execute()` 循环中，当检测到 agent 发出 RETURN（即认为任务完成）时，**不直接返回**，而是进入一个 Verification Phase：

```python
# 概念性伪代码 - 在 vcpu.execute() 中
async def execute(self, goal: str) -> ToolResult:
    ...
    if step_result.is_final:
        # 不直接返回，先进入验证阶段
        verification_result = await self._verification_phase(
            goal=goal,
            claimed_result=step_result.final_result,
        )
        if verification_result.passed:
            return step_result.final_result
        else:
            # 将验证反馈注入 context，继续执行
            self.mmu.add_system_message(
                f"[Verification Failed] {verification_result.feedback}"
            )
            self._state.is_done = False
            continue
```

验证阶段应该做什么？**不是让同一个 LLM 再思考一遍**（那和 prompt engineering 没区别），而是执行**确定性检查**：

1. **环境清洁度检查**：对比任务开始时和结束时的文件系统 diff
   - 检测 polyglot-c-py 中遗留的 `cmain` 编译产物
   - 实现方式：在 `execute()` 开始时 snapshot 工作目录（`find /app -type f`），结束时再 snapshot 一次，diff 出新增文件

2. **需求关键词回归检查**：从原始 goal 中提取关键的标识符（如字段名 `value`、`val`），与 agent 生成的代码做精确匹配
   - 检测 kv-store-grpc 中 `value` vs `val` 的问题
   - 实现方式：解析 goal 中的代码引用（反引号内的内容、引号内的标识符），在 agent 产出的文件中 grep 确认

3. **输出值抽样**：对 agent 声称正确的输出，做简单的 sanity check
   - 检测 pcap-to-netflow 的时间戳问题（2026 vs 2011）

**实现路径建议：** 新增一个 `VerificationEngine` 组件，注册到 vCPU 中，类似现有的 `ErrorHandlerRegistry` 模式：

```python
class VerificationEngine:
    """任务完成后的确定性验证"""
    
    def __init__(self):
        self._checkers: List[VerificationChecker] = []
    
    def register(self, checker: VerificationChecker):
        self._checkers.append(checker)
    
    async def verify(self, goal: str, workspace: Path) -> VerificationResult:
        issues = []
        for checker in self._checkers:
            result = await checker.check(goal, workspace)
            if not result.passed:
                issues.append(result)
        return VerificationResult(
            passed=len(issues) == 0,
            issues=issues
        )

# 内置 Checker 示例
class WorkspaceCleanlinessChecker(VerificationChecker):
    """检查是否有意外的文件残留"""
    async def check(self, goal, workspace):
        # 对比 snapshot_before 和 snapshot_after
        ...

class RequirementKeywordChecker(VerificationChecker):
    """从需求中提取关键标识符，在产出物中验证"""
    async def check(self, goal, workspace):
        # 解析 goal 中的关键词，grep 验证
        ...
```

**关键优势：这些是确定性检查，不依赖 LLM 判断，不是 prompt engineering。**

---

### 问题 B：全局扫描缺失（影响 1/7 任务）

#### 根因

build-cython-ext 修了 2 个文件漏了 1 个。这是因为 agent 的行为模式是**线性的**——"发现问题 → 修复 → 继续"，缺少"发现 → 修复 → **全局确认同类问题**"的闭环。

看当前 vCPU 的执行流，它没有任何"修复后全局扫描"的机制。ErrorHandlerRegistry 只处理**失败的工具调用**，不处理**成功的修复后需要扩展搜索**的场景。

#### 建议 2：引入「修复后全局扫描」工具链 (Post-Fix Sweep)

**核心思想：当 Edit 工具成功修改了某个 pattern，自动触发全局搜索同类 pattern。**

在 `_handle_tool_call` 中，当 Edit 成功后，分析 `old_text`（被替换的内容），自动在项目中搜索相同或相似的 pattern：

```python
# 在 _handle_tool_call 的 Edit 成功分支中
if action.name == "Edit" and result.status == "OK":
    old_text = action.args.get("old_string", "")
    file_path = action.args.get("path", "")
    
    # 提取核心 pattern（比如 "np.int"）
    sweep_pattern = self._extract_sweep_pattern(old_text)
    
    if sweep_pattern:
        # 自动搜索同类问题
        sweep_result = await self.gate.syscall_tool(
            ActionIR(
                kind="TOOL_CALL",
                name="Bash",
                id=f"sweep_{action.id}",
                args={"command": f"grep -rn '{sweep_pattern}' --include='*.pyx' --include='*.py' . | grep -v '{file_path}'"}
            ),
            timeout_sec=10.0
        )
        
        if sweep_result.output and sweep_result.output.strip():
            # 注入提醒
            self.mmu.add_system_message(
                f"⚠️ [Post-Fix Sweep] Found same pattern '{sweep_pattern}' in other files:\n"
                f"{sweep_result.output}\n"
                f"You should fix these too!"
            )
```

`_extract_sweep_pattern` 的实现可以是规则型的（不需要 LLM）：
- 从 `old_text` 中提取被替换的核心标识符
- 比如 `np.int` → 搜索 `np\.int\b`
- 比如 `from foo import bar` → 搜索相同的 import

**这是一个纯工程手段，不依赖 prompt，而是框架自动执行的确定性行为。**

---

### 问题 C：需求精度降级（影响 2/7 任务）

#### 根因

kv-store-grpc 把 `value` 混为 `val`，lm-eval-harness 的 metric 配置不对。这不是"理解能力"的问题，而是**需求中的关键约束在漫长的执行过程中被遗忘或模糊化了**。

看 MMU 的 `assemble_context()`：

```python
HOT_COUNT = 15  # Keep last 15 messages always
```

当 kv-store-grpc 执行到第 17 轮处理后台服务启动问题时，**原始需求中 `value` 和 `val` 的精确区分早已滑出 hot context**，只留在了可能已被截断的 global summary 或 pinned goal 中。而 pinned goal 只做了简单的总结。

#### 建议 3：引入「需求约束提取器」(Constraint Extractor)

**核心思想：在任务开始时，用确定性方法从 goal 中提取关键约束（API 名称、字段名、版本号等），将其固化为结构化数据，钉在 pinned context 中，不受滑动窗口影响。**

```python
class ConstraintExtractor:
    """从需求文本中提取关键约束"""
    
    def extract(self, goal: str) -> List[Constraint]:
        constraints = []
        
        # 规则 1：提取反引号/引号中的标识符
        # "SetValRequest that includes a key (string) and a value (int)"
        # → ["key", "string", "value", "int"]
        
        # 规则 2：提取版本号
        # "NumPy 2.3.0" → version_constraint("numpy", "2.3.0")
        
        # 规则 3：提取 API/协议名称
        # "NetFlow v5" → protocol_constraint("netflow", "v5")
        
        # 规则 4：提取字段名和类型映射
        # "key (string) and value (int)" → field_constraints
        
        return constraints
```

提取出的约束钉到 `PinnedContext.custom_anchors` 中：

```
# ⚠️ CRITICAL CONSTRAINTS (from original requirement)
- SetValRequest fields: key(string), value(int)  ← NOT "val"!
- SetValResponse fields: val(int)
- Protocol: gRPC
```

**为什么这不是 prompt engineering？** 因为它不是在 prompt 中加文字让 LLM "注意"，而是一个**确定性的解析步骤**，将自然语言需求转化为结构化约束，并**钉死在不会被滑动窗口丢弃的位置**。

---

### 问题 D：Bash 工具对长运行进程的支持（间接影响）

#### 根因

当前 `bash.py` 有硬 60s 超时，而且用 `asyncio.create_subprocess_shell` + `PIPE` 的方式，`command &` 并不能真正后台化（因为 stdout pipe 仍然被持有）。

kv-store-grpc 中 agent 花了 17 轮迭代在"后台服务是否在运行"上，消耗了本可以用来更仔细检查代码的注意力预算。

#### 建议 4：增加「后台服务启动器」工具 (DaemonLauncher)

**这是一个纯工程改进——增加一个新工具，而不是改 prompt。**

```python
DAEMON_TOOL = {
    "name": "StartDaemon",
    "description": "Start a long-running background process (server, watcher, etc.)",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command to run"},
            "port": {"type": "integer", "description": "Port to wait for (optional)"},
            "ready_pattern": {"type": "string", "description": "Log pattern indicating readiness"},
            "timeout": {"type": "number", "description": "Max seconds to wait for readiness (default: 30)"},
        },
        "required": ["command"]
    }
}

async def start_daemon(command: str, port: int = None, 
                       ready_pattern: str = None, timeout: float = 30) -> str:
    """
    启动后台进程：
    1. 用 nohup + 重定向启动，确保不阻塞
    2. 如果指定 port，轮询等待端口可用
    3. 如果指定 ready_pattern，tail log 等待匹配
    4. 返回 PID 和状态
    """
    import subprocess, socket, time
    
    log_file = f"/tmp/daemon_{int(time.time())}.log"
    full_cmd = f"nohup {command} > {log_file} 2>&1 & echo $!"
    
    proc = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    pid = proc.stdout.strip()
    
    # 等待就绪
    if port:
        for _ in range(int(timeout)):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                sock.connect(('localhost', port))
                sock.close()
                return f"✅ Daemon started (PID={pid}), port {port} is ready.\nLog: {log_file}"
            except:
                await asyncio.sleep(1)
        return f"⚠️ Daemon started (PID={pid}) but port {port} not ready after {timeout}s.\nLog: {log_file}"
    
    return f"✅ Daemon started (PID={pid}).\nLog: {log_file}"
```

**价值：** 把"启动后台服务"从一个需要 agent 摸索 3 分钟的复杂场景，变成一个调用一次就完成的原子操作。省下的 iteration 可以让 agent 有更多注意力用在真正的逻辑上。

---

### 问题 E：领域知识补充（影响 2/7 任务）

#### 根因

pcap-to-netflow 的时间戳错误和 lm-eval-harness 的 metric 配置错误，本质上都是 **agent 在不熟悉的领域"凭直觉写代码"，而没有先查阅文档/规范。**

当前 nimbus 没有任何"在实现之前先查文档"的机制。

#### 建议 5：引入「实现前调研阶段」(Pre-Implementation Research Phase)

两个可选方向：

**方向 A：基于项目文档的自动注入**

当任务涉及特定框架（如 lm-eval-harness），框架检测到项目中存在该框架的文档/配置文件时，自动将关键文件内容注入 context：

```python
class ProjectDocInjector:
    """自动发现并注入项目中的关键文档"""
    
    DOC_PATTERNS = [
        "README.md", "CONTRIBUTING.md", "docs/**/*.md",
        "*.yaml", "*.yml",  # 配置文件
        "examples/**/*",     # 示例代码
    ]
    
    async def inject_relevant_docs(self, goal: str, workspace: Path) -> List[str]:
        # 1. 从 goal 中提取关键词（确定性方法）
        keywords = self._extract_keywords(goal)  # ["lm-eval-harness", "metric", "exact_match"]
        
        # 2. 搜索项目中匹配的文档
        relevant_files = []
        for pattern in self.DOC_PATTERNS:
            for f in workspace.glob(pattern):
                content = f.read_text()
                if any(kw in content.lower() for kw in keywords):
                    relevant_files.append((f, content[:2000]))
        
        return relevant_files
```

**方向 B：Web 搜索工具**

给 agent 增加一个 `WebSearch` 工具，让它能在实现前查阅 RFC/文档。这个改动更大，但长期价值也更大。即使是简单实现（调 Google/DuckDuckGo API），也能大幅减少"凭直觉猜测协议规范"的问题。

---

### 问题 F：注意力预算管理（横切关注点）

#### 根因

这是一个贯穿所有失败任务的隐性问题。当前 `VCPUConfig.max_iterations = 50`，看起来很多，但实际上：
- kv-store-grpc 花了 17 轮在后台服务启动上 → 只剩 33 轮做真正的事
- build-cython-ext 可能在前两个 .pyx 文件上花了大量迭代 → 没有注意力去扫描第三个

当前架构中没有"注意力预算"的概念——所有 iteration 都是等价的，框架不区分"探索性的 iteration"和"关键的验证 iteration"。

#### 建议 6：引入「注意力预算管理」(Attention Budget)

```python
@dataclass
class AttentionBudget:
    """注意力预算管理"""
    total_iterations: int = 50
    
    # 预留验证预算（不可被其他阶段侵占）
    verification_reserve: int = 5   # 最后 5 轮专门用于验证
    
    # 阶段性预算
    research_budget: int = 5         # 调研/理解需求
    implementation_budget: int = 30  # 实际实现
    debugging_budget: int = 10       # 调试/修复
    
    def should_transition(self, phase: str, iteration: int) -> Optional[str]:
        """检查是否应该强制过渡到下一阶段"""
        if iteration >= self.total_iterations - self.verification_reserve:
            return "verification"  # 强制进入验证阶段
        return None
```

当剩余 iteration 触及 `verification_reserve` 时，框架应该注入强制信号（不是 hint，而是类似 CONTEXT_OVERFLOW 那样的 Fault），让 agent 停止实现，开始验证。

---

## 三、优先级排序

| 改进 | 预计工作量 | 预计影响（能修复的失败任务数） | ROI |
|------|-----------|--------------------------|-----|
| **建议 4: DaemonLauncher** | 0.5天 | 间接影响 1-2 个 | ⭐⭐⭐⭐⭐ |
| **建议 1: VerificationEngine** | 2-3天 | 直接影响 3 个 (polyglot, kv-store, pcap) | ⭐⭐⭐⭐⭐ |
| **建议 3: ConstraintExtractor** | 1-2天 | 直接影响 2 个 (kv-store, lm-eval) | ⭐⭐⭐⭐ |
| **建议 2: Post-Fix Sweep** | 1天 | 直接影响 1 个 (build-cython-ext) | ⭐⭐⭐ |
| **建议 6: AttentionBudget** | 1天 | 间接影响所有任务 | ⭐⭐⭐ |
| **建议 5: ProjectDocInjector** | 2天 | 直接影响 2 个 (pcap, lm-eval) | ⭐⭐⭐ |

**推荐实施顺序：4 → 1 → 3 → 2 → 6 → 5**

---

## 四、一个更深层的思考

回顾所有失败案例，有一个共性：**agent 在"做正确的事"和"做完整的事"之间存在差距**。它能写出功能上基本正确的代码（5/7, 10/11），但缺乏"强迫症式的完整性检查"。

这其实不是 LLM 能力的问题，也不是 prompt 的问题——**是框架没有给 agent 提供足够的"刹车机制"**。当前的 nimbus 有很好的"加速机制"（doom loop 检测防止卡死、error recovery 自动恢复），但缺少：

1. **任务完成前的强制刹车**（VerificationEngine）
2. **修复后的自动扩展**（Post-Fix Sweep）
3. **关键约束的持久化**（ConstraintExtractor）

这些都是框架层面的结构化改进，不是让 LLM "更注意"或"更仔细"——而是用工程手段为 LLM 的输出加上**确定性的护栏**。


### Opinion from GPT-5.2 (Generalist)

好的，我已经深入阅读了测试报告和 nimbus 框架的核心代码。让我给出一份系统性的评审意见。

---

## Nimbus Agent Terminal-Bench 评审报告

### 一、总体评价

先说结论：**nimbus 在基础能力层面做得不错**。9 个有效任务中，大多数任务的子项通过率都很高（5/7、10/11、2/3），说明 LLM 的推理能力和工具调用链路是通畅的。真正的问题不在"能不能做"，而在**"做完之后能不能确认做对了"**。

22% 的通过率和高子项通过率之间的反差，揭示的是一个**架构层面的系统性缺陷**：nimbus 目前是一个 **"单程执行"（single-pass execution）架构**——agent 执行完就认为完了，框架层面没有任何机制来质疑、验证或修正 agent 的自我判断。

---

### 二、问题根因分析（按架构层次）

#### 问题 1：缺少结构化的验证阶段（最核心）

从 `vcpu.py` 的执行循环看：

```
while not done:
    Think → Decode → Execute → Observe
```

这个循环的终止条件是 `RETURN` 或 `THOUGHT`（即 LLM 认为任务完成），**没有框架层面的 "Verify" 阶段**。当前的 hint 机制：

```python
# vcpu.py line ~520
if action.name in ("Edit", "Write") and result.status == "OK":
    output_str += "\n\n[Hint] File modified successfully. Consider testing it with Bash..."
```

这只是一个字符串提示，完全依赖 LLM 自行决定是否执行验证、如何验证。测试报告中三个"自验证盲区"任务（kv-store-grpc、polyglot-c-py、pcap-to-netflow）都是 agent **自己写了验证、验证通过了、但验证策略有偏差**。

**核心矛盾**：用同一个 LLM 既做实现又做验证，存在系统性的 **confirmation bias**——它倾向于用自己理解的方式验证自己的实现。

#### 问题 2：环境副作用无感知

`StateManager` 只跟踪了显式的文件操作（Read/Write/Edit）和 Bash 命令的 pass/fail 状态：

```python
# state_manager.py
def update(self, tool_name: str, args: dict, result_content: str):
    if tool_name == "Write": ...
    elif tool_name == "Edit": ...
    elif tool_name == "Read": ...
    elif tool_name == "Bash": self._update_cmd_status(...)
```

但它**不跟踪 Bash 命令对文件系统的副作用**。polyglot-c-py 任务中，`gcc main.py.c -o cmain` 产生了编译产物，这个副作用对 StateManager 完全不可见。Agent 没有意识到自己的验证行为"污染"了环境。

#### 问题 3：Bash 工具对长时运行进程的结构性限制

```python
# bash.py
async def bash_command(command: str, timeout: Optional[float] = 60.0, ...):
```

60 秒超时 + `subprocess.PIPE` 模式意味着 `nohup server &` 这种模式无法正常工作——子进程的 stdout pipe 仍然被持有，`asyncio.wait_for` 会在 60 秒后 kill 掉整个进程组。

kv-store-grpc 任务中 agent 花了 17 轮迭代（约 3 分钟）跟这个问题搏斗，消耗了本该用于检查代码正确性的"注意力预算"。

#### 问题 4：缺少"同类问题全局扫描"行为模式

现有的 Error Handler 系统（`error_handler.py`）和 Doom Loop 检测（`doom_loop.py`）都是针对**工具调用失败**的恢复机制。但 build-cython-ext 的问题不是工具失败——agent 成功修了 2/3 的文件，然后就认为任务完成了。

框架层面没有任何机制在 agent 修复一个 pattern 后提醒它："你要不要搜一下还有没有同样的问题？"

#### 问题 5：需求精度在传递过程中衰减

kv-store-grpc 中 `value` → `val` 的混淆、lm-eval-harness 中 metric 配置的偏差，都是需求理解的精度问题。当前架构中，需求以 `goal` 字符串的形式被 pin 到 MMU 的 Anchor 区域：

```python
# vcpu.py
if self.config.pin_goal:
    pinned_goal = await self._prepare_goal_for_pinning(goal)
    self.mmu.pin_user_goal(pinned_goal)
```

但 pin 进去的是整个 goal 文本。随着执行轮次增加和 compaction 的发生，agent 对 goal 中**精确措辞**的注意力会衰减。特别是当 goal 中同时出现 `value` 和 `val` 这样相似但不同的术语时，LLM 很容易在后续推理中混淆。

---

### 三、建设性改进方案（框架/架构层面）

你说得对，不应该靠 prompt engineering——那是在"请求" LLM 做得更好，而不是"确保"它做得更好。以下改进都是**机制层面**的。

#### 方案 1：引入 Verification Gate（验证门）

**思路**：在 VCPU 的 RETURN 路径上加一个 **Verification Gate**，类似 CI/CD 中的 quality gate。Agent 说"我完成了"的时候，不是直接返回，而是进入一个验证阶段。

**架构变更**：

```
当前: Think → Act → Observe → [RETURN] → Done
改后: Think → Act → Observe → [RETURN] → VerifyGate → Done/Retry
```

具体做法：在 `_handle_return` 中插入验证逻辑：

```python
# 概念性伪代码，不是具体实现
async def _handle_return(self, action: ActionIR) -> ToolResult:
    result = action.args.get("result", ...)
    
    # Verification Gate
    if self._verification_gate:
        verdict = await self._verification_gate.verify(
            goal=self.mmu.get_pinned_goal(),
            claimed_result=result,
            execution_trace=self.tracer.get_current_traces(),
            environment_state=self._state_manager.render(),
        )
        if not verdict.passed:
            # 不直接返回，而是把验证反馈注入上下文继续执行
            self.mmu.add_system_message(
                f"[Verification Failed] {verdict.feedback}"
            )
            return ToolResult(status="OK", is_final=False)  # 继续循环
    
    return ToolResult(status="OK", output=result, is_final=True)
```

**VerificationGate 的实现可以分层**：

| 层级 | 方式 | 成本 | 覆盖的问题 |
|------|------|------|-----------|
| L0: 确定性检查 | 环境 diff、文件清洁度 | 免费 | polyglot-c-py |
| L1: 需求-实现对照 | 正则/AST 匹配需求中的精确命名 | 免费 | kv-store-grpc |
| L2: LLM 交叉验证 | 用独立的 LLM 调用做对抗性审查 | 中等 | pcap-to-netflow、lm-eval-harness |

**关键设计原则**：验证者不应共享执行者的上下文。这打破了 confirmation bias——验证者只看到 goal + 最终产物，不看执行过程中 agent 的"心路历程"。

#### 方案 2：环境快照 & Diff 机制

**思路**：在任务开始时对环境做快照（至少是工作目录的文件列表 + 关键文件的 hash），在 RETURN 前自动做 diff。

```python
class EnvironmentSnapshot:
    """任务开始前的环境快照"""
    def __init__(self, workspace: Path):
        self.file_tree = self._scan_tree(workspace)
        self.file_hashes = self._hash_files(workspace)
    
    def diff(self, current_workspace: Path) -> EnvironmentDiff:
        """比较当前状态与快照的差异"""
        current_tree = self._scan_tree(current_workspace)
        return EnvironmentDiff(
            added_files=current_tree - self.file_tree,
            removed_files=self.file_tree - current_tree,
            modified_files=self._find_modified(current_workspace),
        )
```

在 RETURN 前自动运行 diff，生成人类可读的变更摘要，让 agent（或 Verification Gate）审查：

```
[Environment Diff]
+ /app/polyglot/cmain  (unexpected binary file, 24KB)
~ /app/polyglot/main.py.c  (expected, your creation)
```

这个机制是**确定性的、零成本的**，能直接解决 polyglot-c-py 这类"验证副作用"问题。

#### 方案 3：Reflexion-Style 修复扫描

**思路**：当 agent 执行修复类操作（Edit/Write）时，框架自动提取修复模式（pattern），然后用确定性工具（grep/ast）扫描全项目中是否存在同类问题。

```python
class ReflexionScanner:
    """修复后的全局扫描器"""
    
    async def on_edit_success(self, action: ActionIR, result: ToolResult):
        """Edit 成功后的钩子"""
        old_text = action.args.get("oldText", "")
        new_text = action.args.get("newText", "")
        file_path = action.args.get("path", "")
        
        # 提取修复模式
        pattern = self._extract_pattern(old_text, new_text)
        if not pattern:
            return
        
        # 在项目中搜索同类模式
        # 使用确定性工具，不依赖 LLM
        remaining = await self._grep_pattern(pattern, exclude=file_path)
        
        if remaining:
            # 注入提醒到上下文
            self.mmu.add_system_message(
                f"⚠️ [Reflexion] You fixed '{pattern.describe()}' in {file_path}. "
                f"Same pattern found in {len(remaining)} other files:\n"
                + "\n".join(f"  - {f}" for f in remaining[:5])
            )
```

对于 build-cython-ext 的 `np.int` 问题，这个机制会在 agent 修完 `chelpers.pyx` 后自动执行 `grep -rn "np\.int[^0-9e]" *.pyx`，发现 `ccomplexity.pyx` 中还有同样的问题，并注入提醒。

#### 方案 4：后台进程工具（BackgroundProcess Tool）

**思路**：与其修改 Bash 工具的超时逻辑，不如提供一个专门的后台进程管理工具。

```python
BACKGROUND_PROCESS_TOOL = {
    "name": "BackgroundRun",
    "description": "Start a long-running background process (e.g., server). Returns PID.",
    "parameters": {
        "command": {"type": "string", "description": "Command to run"},
        "ready_pattern": {"type": "string", "description": "Pattern in stdout that indicates readiness"},
        "ready_timeout": {"type": "number", "description": "Max seconds to wait for ready_pattern"},
    }
}
```

实现上，用 `subprocess.Popen`（非 PIPE 模式），将 stdout/stderr 重定向到临时文件，通过 polling 文件内容来检测 `ready_pattern`。配合一个 `ProcessStatus(pid)` 和 `ProcessLog(pid, last_n_lines)` 工具，让 agent 能正常管理后台服务。

这直接解决了 kv-store-grpc 中 17 轮无效迭代的问题。

#### 方案 5：需求结构化解析（Requirement Anchoring）

**思路**：对于包含精确 API 名称、字段名称的需求，在任务开始时做一次结构化提取，将关键约束以 **key-value 形式**锚定到 Pinned Context 中，而不是只 pin 原始文本。

```python
class RequirementAnchor:
    """从自然语言需求中提取结构化约束"""
    
    async def extract(self, goal: str) -> Dict[str, Any]:
        """用 LLM 提取关键约束"""
        # 一次性调用，成本极低
        response = await self.llm.chat([{
            "role": "user",
            "content": f"""Extract precise technical constraints from this task.
            
Task: {goal}

Output JSON with:
- api_names: exact API/function/method names mentioned
- field_names: exact field/parameter names with their types
- protocols: any protocol specifications mentioned
- file_requirements: exact file names/paths required
"""
        }])
        return json.loads(response.content)
```

提取后的结构化约束以紧凑格式 pin 到 Anchor 区：

```
[Requirement Anchor]
Fields: SetValRequest.key(string), SetValRequest.value(int), SetValResponse.val(int)
⚠️ Note: "value" and "val" are DIFFERENT fields!
```

这比 pin 整段自然语言文本有更强的抗衰减能力——即使在 compaction 多次之后，结构化约束仍然精确可见。

#### 方案 6：Execution Budget 可视化

**思路**：kv-store-grpc 中 agent 在工具问题上浪费了 17 轮迭代。如果 agent 能看到自己的"时间预算"消耗情况，它可能会更早放弃无效路径。

在每个 step 的上下文中注入预算信息：

```
[Budget] Step 17/50 | Compactions: 0/100 | Time: 3m12s
⚠️ You've spent 34% of your budget. Review if your current approach is productive.
```

这不是 prompt engineering——这是**确定性的、框架层面的状态注入**，类似 StateManager 已经在做的事情，只是维度不同。

---

### 四、优先级排序建议

| 优先级 | 方案 | 预期收益 | 实现复杂度 | 能解决的测试失败 |
|--------|------|---------|-----------|-----------------|
| **P0** | 方案 4: BackgroundRun 工具 | 高 | 低 | kv-store-grpc 的17轮浪费 |
| **P0** | 方案 2: 环境快照 & Diff | 高 | 低 | polyglot-c-py |
| **P1** | 方案 3: 修复扫描 Reflexion | 高 | 中 | build-cython-ext |
| **P1** | 方案 1: Verification Gate (L0+L1) | 很高 | 中 | polyglot, kv-store-grpc |
| **P2** | 方案 5: 需求结构化锚定 | 中 | 中 | kv-store-grpc, lm-eval |
| **P2** | 方案 1: Verification Gate (L2) | 很高 | 高 | pcap-to-netflow, lm-eval |
| **P3** | 方案 6: 预算可视化 | 低-中 | 低 | 间接改善所有任务 |

**推荐的实施路线**：

1. **第一阶段**（1-2天）：先做 P0，BackgroundRun 工具 + 环境 Diff。这两个实现简单、收益确定，且互相独立。
2. **第二阶段**（3-5天）：Reflexion 修复扫描 + Verification Gate 的 L0/L1 层（确定性验证）。这两个是框架层面的核心增强。
3. **第三阶段**（1周+）：需求锚定 + Verification Gate 的 L2 层（LLM 交叉验证）。这需要更多的设计和调优。

---

### 五、一个更深层的思考

测试报告中有句话很精准：

> 验证策略过于乐观——倾向于用自己理解的方式验证自己的实现

这其实是所有 **single-agent 架构**的本质局限。一个 agent 既是 **执行者** 又是 **验证者**，它的验证不可能超越自己的理解水平。

上面的方案 1-6 都是在 **single-agent 框架内**尽可能弥补这个问题——通过确定性机制（Diff、Grep、AST）减少对 LLM 判断的依赖。

但如果要更根本地解决，可能需要考虑 **dual-agent 或 multi-agent 验证**：

- **Executor Agent**：执行任务，使用完整工具链
- **Reviewer Agent**：独立上下文，只看 goal + 产物，做对抗性验证

这不是让两个 agent 聊天，而是在框架层面内置一个 **adversarial verification loop**。Reviewer 不关心 Executor 是怎么做的，只关心结果是否符合需求。这类似于代码审查中 reviewer 和 author 分离的原则。

nimbus 的 `AgentOS` 已经有 `spawn` + `wait` 的多进程能力，理论上可以支持这种模式。关键是 Reviewer Agent 需要一个**最小化的、专注于验证的上下文**，而不是复用 Executor 的完整执行历史。

---

### 六、总结

nimbus 的 **基础执行能力是过关的**——工具链、内存管理、compaction、doom loop 检测等机制都设计得不错。当前的瓶颈不在 LLM 的推理能力，而在 **框架层面缺少验证闭环**。

改进方向的核心理念是：**不要相信 agent 的自我报告，用确定性机制做 guardrail**。

- 环境 Diff 是确定性的
- Grep 全局扫描是确定性的
- 需求中的精确命名比对是确定性的
- 文件清洁度检查是确定性的

这些确定性机制才是比 prompt engineering 更可靠的改进路径。


### Opinion from Gemini 3 Pro High (Reasoning)

现在我已经详细阅读了测试报告和 nimbus 框架的核心源码（vcpu.py, bash.py, doom_loop.py, error_handler.py, gate.py, compaction.py, agentos.py, tools/__init__.py）。以下是我的系统性评审。

---

## 总体评价

**22% 的通过率看起来不好看，但数据的质量比数字更重要。** 报告写得非常好——失败分析精准到 field name 级别。更关键的是，大多数失败任务的通过项比例很高（5/7, 10/11, 2/3），这说明 nimbus 的核心推理能力是在线的，问题不在"智商"而在"工作习惯"。

下面我按照你的要求，**不从 prompt engineering 角度**，而是从**框架架构和运行时机制**层面给出改进建议。

---

## 一、根因分析：框架层面的系统性缺陷

### 缺陷 1：验证是"建议"而非"机制"

当前的验证完全依赖 LLM 的自觉性。看 `vcpu.py` 中 `_handle_tool_call` 的逻辑：

```python
# 当前只在 Edit/Write 成功后注入一个文本 hint
if action.name in ("Edit", "Write") and result.status == "OK":
    output_str += "\n\n[Hint] File modified successfully. ..."
```

这只是一个字符串级别的"建议"，LLM 可以完全忽略它。在 kv-store-grpc 任务中，agent 确实写了自测客户端——但用的是自己生成的 proto，形成了**自我验证的闭环**。

### 缺陷 2：Tool 执行后没有环境状态快照

polyglot-c-py 的失败非常典型：agent 为了验证编译正确性执行了 `gcc`，但编译产物 `cmain` 留在了目录中。框架层面没有任何机制来追踪一次 Bash 调用对文件系统的副作用。

### 缺陷 3：修复类任务没有"全局扫描"的工作流编排

build-cython-ext 修了 3 个 pyx 文件中的 2 个。当前框架的 `VCPU.execute()` 是纯粹的 Think-Act-Observe 循环，没有任何机制来识别"当前正在做的是批量修复类任务"并自动插入全局扫描步骤。

### 缺陷 4：Bash 工具对后台进程的支持不足

看 `bash.py` 的实现：

```python
process = await asyncio.create_subprocess_shell(
    command,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd=str(work_dir),
)
# ...
await asyncio.wait_for(run_with_streams(), timeout=timeout)
```

这个设计对 `command &` 类型的后台进程天然不友好——因为 stdout/stderr pipe 被持有，即使命令本身 fork 了子进程，`read_stream` 也要等到 pipe 关闭。kv-store-grpc 任务中 agent 浪费了 17 轮迭代在这个问题上。

---

## 二、建设性改进建议（非 Prompt Engineering）

### 建议 1：引入 Verification Gate — 框架级的"交付检查"

**核心思想：** 在 VCPU 的执行循环中，当 agent 发出 `RETURN`（认为任务完成）时，不要直接返回，而是插入一个**机器级别的验证阶段**。

**具体方案：**

```python
# 在 vcpu.py 的 _handle_return 之前插入

class VerificationGate:
    """任务完成前的自动验证机制"""
    
    def __init__(self, gate: KernelGate):
        self.gate = gate
        self._checks: List[VerificationCheck] = []
    
    async def verify(self, goal: str, mmu: MMU) -> VerificationResult:
        """
        从 MMU 的历史中提取 agent 做了什么，
        然后执行确定性验证（不依赖 LLM 判断）
        """
        results = []
        
        # 1. 文件系统一致性检查
        # 提取所有 Write/Edit 操作的目标文件
        modified_files = self._extract_modified_files(mmu)
        for f in modified_files:
            # 确认文件确实存在且可读
            check = await self._check_file_exists(f)
            results.append(check)
        
        # 2. 需求关键词回溯检查
        # 从 goal 中提取关键实体（文件名、字段名、API 名等）
        # 在 agent 的输出中检查是否都出现了
        entities = self._extract_requirement_entities(goal)
        coverage = self._check_entity_coverage(entities, mmu)
        results.append(coverage)
        
        # 3. 环境清洁度检查（针对 polyglot 类任务）
        # 对比任务开始前和结束时的文件系统状态
        if self._has_filesystem_snapshot():
            diff = self._compare_filesystem_state()
            results.append(diff)
        
        return VerificationResult(checks=results)
```

**关键设计点：**
- 这不是让 LLM "再想想"（那就是 prompt engineering），而是**程序化地**从执行历史中抽取事实并做确定性检查
- 比如 kv-store-grpc 任务，如果 Verification Gate 从 goal 中提取了 `value`（作为字段名），然后在 agent 生成的 proto 文件中 `grep "value"`，就能在提交前发现字段名不匹配
- 类似于 CI/CD 中的 post-deployment smoke test

### 建议 2：FileSystem Snapshot — Bash 执行的副作用追踪

**核心思想：** 在每次 Bash 工具执行前后，对工作目录做轻量级 snapshot，追踪文件系统变化。

```python
# 新增 tools/bash_sandbox.py

class BashWithSnapshot:
    """带文件系统快照的 Bash 执行器"""
    
    async def execute(self, command: str, workspace: Path, **kwargs):
        # 1. 快照：记录当前目录的文件列表和 mtime
        before = self._snapshot(workspace)
        
        # 2. 执行命令
        result = await bash_command(command, workspace=workspace, **kwargs)
        
        # 3. diff：对比执行前后的文件系统变化
        after = self._snapshot(workspace)
        side_effects = self._diff(before, after)
        
        # 4. 把副作用信息附加到结果中
        if side_effects.created or side_effects.modified:
            result += f"\n\n[Side Effects] Created: {side_effects.created}, Modified: {side_effects.modified}"
        
        return result
    
    def _snapshot(self, workspace: Path) -> Dict[str, float]:
        """轻量级快照：只记录文件路径和 mtime，不读内容"""
        return {
            str(p.relative_to(workspace)): p.stat().st_mtime
            for p in workspace.rglob("*") if p.is_file()
        }
```

**价值：**
- polyglot-c-py 任务中，agent 执行 `gcc` 后会看到 `[Side Effects] Created: ['cmain']`，自然就知道要清理
- 这是**信息层面**的增强，不是 prompt 层面的

### 建议 3：Pattern Sweep 工具 — 修复类任务的全局扫描

**核心思想：** 新增一个专门的 `Sweep` 工具，当 agent 修复了某个 pattern 后，自动搜索整个项目中的同类问题。

```python
# 新增 tools/sweep.py

SWEEP_TOOL = {
    "name": "Sweep",
    "description": (
        "Search for all occurrences of a pattern across the project. "
        "Use this AFTER fixing a bug to find all similar instances. "
        "Returns file:line for each match."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for (e.g., 'np\\.int[^0-9e]')"
            },
            "file_pattern": {
                "type": "string", 
                "description": "File glob pattern (e.g., '*.pyx', '*.py')"
            },
            "context_lines": {
                "type": "integer",
                "description": "Lines of context around each match (default: 2)"
            }
        },
        "required": ["pattern"]
    }
}
```

**但这不够——更重要的是框架级别的行为：** 在 `error_handler.py` 的 `EditStringNotFoundHandler` 旁边，增加一个 `PostFixSweepHandler`：

```python
class PostFixSweepHandler:
    """
    当 Edit 成功时（不是失败时），检查是否有同类修改需要做。
    这不是 error handler，而是 success handler。
    """
    
    def on_edit_success(self, file_path: str, old_text: str, new_text: str) -> Optional[str]:
        """
        分析 edit 操作，提取修复 pattern，
        返回建议的全局搜索命令（如果适用）
        """
        # 提取被替换的"有问题"的部分
        # 比如 old_text="np.int" new_text="np.int64"
        # 则 pattern = "np\\.int[^0-9]"
        
        # 在 workspace 中搜索同类文件
        # 返回结果供 agent 参考
```

这样在 build-cython-ext 任务中，当 agent 成功修改了 `chelpers.pyx` 中的 `np.int` → `np.int64` 后，框架会自动告诉它："Found 3 more occurrences in ccomplexity.pyx:42, cinvariants.pyx:17"。

### 建议 4：Bash 工具支持 Daemon 模式

**核心思想：** 为 `Bash` 工具增加一个 `daemon` 参数，专门处理"启动后台服务"场景。

```python
# 修改 tools/bash.py

async def bash_command(
    command: str,
    timeout: Optional[float] = 60.0,
    workspace: Optional[Path] = None,
    daemon: bool = False,  # 新增
    daemon_ready_pattern: Optional[str] = None,  # 新增："Listening on port"
    daemon_ready_timeout: float = 10.0,  # 新增
    **kwargs,
) -> str:
    if daemon:
        return await _run_daemon(
            command, workspace, 
            ready_pattern=daemon_ready_pattern,
            ready_timeout=daemon_ready_timeout,
        )
    # ... 原有逻辑

async def _run_daemon(command, workspace, ready_pattern, ready_timeout):
    """
    启动后台进程，等待就绪信号后返回。
    
    1. 用 nohup + redirect 启动，避免 pipe 阻塞
    2. 如果提供了 ready_pattern，poll stdout 直到匹配
    3. 返回 PID 和启动状态
    """
    import subprocess
    
    log_file = tempfile.mktemp(suffix=".log", prefix="daemon-")
    
    # 使用 shell redirect 而非 pipe，避免阻塞
    full_cmd = f"nohup {command} > {log_file} 2>&1 & echo $!"
    proc = await asyncio.create_subprocess_shell(
        full_cmd,
        stdout=asyncio.subprocess.PIPE,
        cwd=str(workspace),
    )
    stdout, _ = await proc.communicate()
    pid = stdout.decode().strip()
    
    # 等待就绪信号
    if ready_pattern:
        start = time.time()
        while time.time() - start < ready_timeout:
            if Path(log_file).exists():
                content = Path(log_file).read_text()
                if ready_pattern in content:
                    return f"Daemon started (PID: {pid}). Log: {log_file}\nReady signal found: {ready_pattern}"
            await asyncio.sleep(0.5)
        
        # 超时但进程可能仍在运行
        return f"Daemon started (PID: {pid}). Log: {log_file}\nWarning: Ready pattern '{ready_pattern}' not found within {ready_timeout}s"
    
    return f"Daemon started (PID: {pid}). Log: {log_file}"
```

**价值：**
- kv-store-grpc 任务中 agent 浪费的 17 轮迭代可以缩减到 1-2 轮
- 节省下来的"注意力预算"可以让 agent 更仔细地检查代码正确性
- 这是**工具能力增强**，不是 prompt 调整

### 建议 5：需求实体提取器 — 精确追踪规格约束

**核心思想：** 在任务开始时，用**确定性解析**（不是 LLM）从需求文本中提取结构化约束。

```python
# 新增 core/requirement_tracker.py

class RequirementTracker:
    """
    从自然语言需求中提取可验证的约束。
    
    不使用 LLM，而是用正则 + 启发式规则提取：
    - API/字段名（驼峰、下划线命名的标识符）
    - 数值约束（"port 8080", "timeout 30s"）
    - 文件路径约束（"/app/xxx", "*.proto"）
    - 类型约束（"string", "int", "bool"）
    """
    
    def extract_constraints(self, goal_text: str) -> List[Constraint]:
        constraints = []
        
        # 提取命名约束：括号内的字段定义
        # 例: "SetValRequest that includes a key (string) and a value (int)"
        field_pattern = r'(\w+)\s*\((\w+)\)'
        for match in re.finditer(field_pattern, goal_text):
            name, type_ = match.groups()
            constraints.append(FieldConstraint(name=name, type=type_))
        
        # 提取端口号
        port_pattern = r'port\s+(\d+)'
        for match in re.finditer(port_pattern, goal_text):
            constraints.append(PortConstraint(port=int(match.group(1))))
        
        # 提取文件路径
        path_pattern = r'[/\w.-]+\.\w+'
        for match in re.finditer(path_pattern, goal_text):
            constraints.append(PathConstraint(path=match.group()))
        
        return constraints
    
    def verify_against_output(self, constraints: List[Constraint], 
                                files: Dict[str, str]) -> List[Violation]:
        """
        检查生成的文件是否满足约束。
        完全确定性的检查，不需要 LLM。
        """
        violations = []
        for c in constraints:
            if isinstance(c, FieldConstraint):
                # 在所有生成的文件中搜索字段名
                found = any(c.name in content for content in files.values())
                if not found:
                    violations.append(
                        Violation(f"Required field '{c.name}' not found in any generated file")
                    )
        return violations
```

**价值：**
- kv-store-grpc 中，tracker 会提取 `value (int)` 和 `val (int)` 作为两个不同的约束
- 在 agent 生成 proto 后，验证器发现 `value` 在文件中找不到，立即报警
- 这是**编译器思维**——把自然语言需求"编译"为可检查的约束

### 建议 6：Docker 安装的多版本 CI 测试

这是纯工程问题。建议：

```yaml
# .github/workflows/docker-compat.yml
strategy:
  matrix:
    python: ["3.9", "3.10", "3.11", "3.12", "3.13"]
steps:
  - name: Test nimbus installation
    run: |
      docker run python:${{ matrix.python }} bash -c "
        pip install dist/nimbus-*.whl
        which nimbus
        nimbus --version
      "
```

`pyproject.toml` 声明 `requires-python = ">=3.10"`，但 swe-bench 容器用了 Python 3.9。要么把最低版本改成 3.9，要么在 `nimbus-run` 脚本中显式处理。

---

## 三、优先级排序

| 优先级 | 改进项 | 预期影响 | 实现复杂度 |
|--------|--------|----------|-----------|
| **P0** | Bash Daemon 模式 | 直接消除 kv-store-grpc 的 17 轮浪费 | 低（~100 行） |
| **P0** | Docker 多版本 CI | 消除 swe-bench 安装失败 | 低（CI 配置） |
| **P1** | FileSystem Snapshot | 解决 polyglot 类副作用问题 | 中（~200 行） |
| **P1** | Post-Fix Sweep | 解决 build-cython-ext 类遗漏 | 中（~150 行 + 新工具） |
| **P2** | Verification Gate | 系统性解决自验证盲区 | 高（~500 行，需设计） |
| **P2** | Requirement Tracker | 解决需求精度降级 | 高（NLP 提取不简单） |

---

## 四、一个更深层的思考

你说"不想通过 prompt engineering 来增强，毕竟是有限的"——我非常同意。但我想更进一步说：

**报告中暴露的核心问题其实是 LLM agent 的"认知闭合"倾向。** Agent 倾向于尽快得出"✅ 完成"的结论，而不是继续质疑自己的工作。这不是 prompt 能解决的，因为 prompt 说"请仔细检查"和不说，在 LLM 的概率空间里差别有限。

真正有效的是让框架**在 agent 不知情的情况下**做确定性检查。就像编译器不会"建议"程序员修 bug，而是**拒绝编译**。上面的 Verification Gate 和 Requirement Tracker 就是这个思路——把"检查"从 LLM 的概率决策中拿出来，变成框架的确定性逻辑。

如果只能做一件事，我的建议是：**先做 Bash Daemon 模式**（P0，投入产出比最高），然后做 **Post-Fix Sweep**（P1，直接解决"修了 A 忘了 B"的系统性问题）。Verification Gate 虽然是最有潜力的，但设计复杂度高，建议作为 v0.3 的核心 feature 规划。
