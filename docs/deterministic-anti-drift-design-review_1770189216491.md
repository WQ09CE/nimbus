# AI Council Report: 评审一下 docs/deterministic-anti-drift-design.md， 重点关注如何用更确定，不依赖大语言模型的方式来完成这个长上下文的任务抗漂移的改进。 要求尽可能通过代码机制来管理上下文，脱离智能总结。

2026/2/4 15:13:36

### Opinion from Claude Opus 4.5 (Deep Thinker)

作为 AI 顾问，我详细评审了 `docs/deterministic-anti-drift-design.md`。

**总体评价**：
该设计文档方向非常正确。它敏锐地指出了当前基于 LLM 总结（Summarization）机制的核心弱点——**主观性**和**随机性**。通过将“记忆”锚定在“工具调用（Tool Call）”这一客观事实之上，确实能极大提升系统的抗漂移能力。

然而，为了彻底实现“不依赖大语言模型”且“更确定”的上下文管理，目前的方案（仅生成文本 Milestone）还可以更进一步。我们应该从**“日志记录（Logging）”**思维升级到**“状态机（State Machine）”**思维。

以下是我的深度评审及改进建议，重点在于通过**代码机制（Code Mechanisms）**而非文本堆叠来管理上下文：

### 1. 核心改进：从“文本里程碑”升级为“结构化状态对象”

文档中提到的 `add_tool_result` 生成 `[x] Edited file...` 依然是生成自然语言文本。虽然这比 LLM 总结可靠，但依然难以被程序本身利用，且容易随着上下文变长而消耗 Token。

**建议方案**：
引入一个结构化的 **`TaskState` (任务状态)** 对象，由代码逻辑直接维护，而不是仅仅追加字符串。

```python
# 概念代码结构
@dataclass
class TaskState:
    created_files: Set[str] = field(default_factory=set)
    modified_files: Set[str] = field(default_factory=set)
    read_files: Set[str] = field(default_factory=set) # 记录由于"Read"而被关注的文件
    
    # 关键：执行状态追踪，而非简单的命令记录
    last_test_result: Optional[str] = None # "PASS" | "FAIL" | None
    last_error: Optional[str] = None
    
    def update(self, tool_name: str, tool_args: dict, tool_output: str):
        # 纯代码逻辑，无 LLM 参与
        if tool_name == "Write":
            self.created_files.add(tool_args['path'])
        elif tool_name == "Edit":
            self.modified_files.add(tool_args['path'])
        elif tool_name == "Bash" and "pytest" in tool_args['command']:
             # 解析 exit code 或 stderr 来更新状态
             self.last_test_result = "FAIL" if "failed" in tool_output else "PASS"
```

**收益**：
*   **确定性去重**：`Set` 数据结构天然去重，不需要“智能去重”逻辑。
*   **状态快照**：你可以随时将这个对象序列化为极简的 JSON 或 Markdown 表格注入 Prompt，比线性日志更省 Token。

### 2. 对 `Read` 操作的重新思考：区分“探索”与“上下文”

文档中提到对 `Read` 进行去重是一个很好的点，但更深层次的问题是：**Read 操作通常代表“短期记忆”或“注意力”，而非“里程碑”。**

*   **问题**：如果任务进行了 50 轮，读取了 20 个文件，即使去重了，这 20 个文件名作为 Milestone 放在 Prompt 里也可能由噪音构成。用户看过的文件不代表任务完成了。
*   **改进策略**：
    *   **Working Set (工作集) 概念**：利用 `Read` 操作来维护一个“当前活跃文件列表” (Active Context)。
    *   **上下文构建策略**：在构建 Prompt 时，不要把 `Read` 记录作为“完成的成就”展示，而是作为“背景参考”。
    *   **LRU 机制**：如果列表过长，通过代码逻辑（Least Recently Used）自动丢弃很久没再读取的文件引用，完全不需要 LLM 来决定什么重要。

### 3. Bash 指令的确定性捕获：结果优于过程

文档建议正则匹配 `git/pip/test`。这很好，但还不够。
对于抗漂移来说，**结果（Outcome）** 比 **动作（Action）** 更重要。

*   **场景**：AI 执行了 `pytest`（这是 Milestone），但测试失败了。
*   **当前设计**：记录 `[x] Executed: pytest`。
*   **风险**：模型看到这个 Milestone 可能会产生幻觉，认为“哦，我跑过测试了，应该没问题”，从而忽略了之前的失败。
*   **改进建议**：
    代码层拦截 Bash 的 `exit_code` 或 `stderr`。
    *   如果 exit_code != 0 -> 状态记录为 **RED** (Failed)。
    *   如果 exit_code == 0 -> 状态记录为 **GREEN** (Passed)。
    在 Prompt 中显式展示：`Test Status: 🔴 FAILED (Fix required)`。这种强信号能强制模型关注并未完成的任务，防止“以为自己做完了”的漂移。

### 4. 彻底脱离智能总结的 Context 组装范式

目前的上下文管理通常是：`Raw Context -> LLM Summarizer -> Summarized Context`。
您要求的“脱离智能总结”可以通过以下确定性算法（Deterministic Algorithm）实现 Context 组装：

```text
[System Prompt]
...

[Structural Task State] (由代码生成，绝对客观)
- Files Created: 2 (main.py, utils.py)
- Files Modified: 1 (config.json)
- Current Test Status: 🔴 FAILED (Last run: pytest tests/)

[Milestone Log] (仅保留 Write/Edit 和 状态改变的 Bash)
1. Created file main.py
2. Edited config.json
3. Executed pytest (Failed)

[Recent History] (Sliding Window, e.g., Last 10 messages)
User: Fix the bug...
Assistant: I will check...
...
```

**这种设计的优势**：
1.  **Zero Information Loss (Key Actions)**：关键动作通过 State 和 Log 双重锁定。
2.  **No Hallucination**：状态是由代码逻辑判断的，LLM 无法“编造”测试通过。
3.  **Self-Correction**：显式的 `FAILED` 状态会强迫 LLM 在下一轮对话中优先修复错误。

### 总结

`docs/deterministic-anti-drift-design.md` 是一个很好的起点。要达到您要求的高度，建议做以下修改：

1.  **引入 `TaskState` 类**：用代码维护项目状态，而不是只记流水账。
2.  **增强 Bash 审计**：必须解析执行结果（成功/失败），并将结果作为状态的一部分。
3.  **降级 Read 权重**：`Read` 用于维护“活跃上下文窗口”，不作为永久“里程碑”。
4.  **确定性 Prompt 模板**：用“状态快照 + 滑动窗口”替代“LLM 总结”。

通过这些手段，您可以构建一个完全不依赖 LLM 记忆力、基于规则引擎的“硬核”抗漂移系统。


### Opinion from GPT-5.2 (Generalist)

作为 AI 顾问，我仔细审阅了 `docs/deterministic-anti-drift-design.md`。这份文档的方向非常正确：**将记忆管理从“依赖概率（LLM）”转向“依赖规则（Code）”**。

目前的提案主要集中在“记录流水线（Audit Trail）”，这是一个很好的起点，但对于长上下文抗漂移来说，**仅仅“记录”是不够的，关键在于“压缩”和“索引”**。如果任务进行很久，确定性的 Log 也会无限膨胀，最终挤占上下文窗口。

以下是我的评审意见和改进方案，重点在于如何通过**代码机制（Code-Mechanism）**构建一个更健壮的、不依赖 LLM 总结的上下文状态机。

### 1. 核心问题诊断：从“流水账”到“状态机”

当前的 `Tool Call Audit Trail` 设计本质上是一个**线性日志**。
*   **风险**：在长任务（如 50+ 步骤）中，即使去重了 Read 操作，Edit 和 Bash 的记录也会变得冗长。例如 `Edited file A` 出现了 10 次，上下文里就会有 10 条记录，这会分散模型的注意力。
*   **改进建议**：抗漂移的核心不是“记住所有发生过的事”，而是“**记住当前项目的确切状态**”。我们需要从 **Log-based (日志式)** 转向 **State-based (状态式)** 管理。

### 2. 改进方案：构建确定性项目状态注册表 (Deterministic Project State Registry)

建议引入一个新的数据结构 `ProjectState`，由代码自动维护，作为 Prompt 的一部分注入系统提示词（System Prompt）中，而不是仅仅作为对话历史。

#### 2.1 机制设计：文件指纹与热度图
不要只记录“编辑了文件”，要记录“文件的最终状态”。

**代码逻辑概念：**
建立一个内存中的 `FileRegistry`，在每次 `ToolResult` 返回时更新：

*   **File Map**: 维护一个当前任务涉及的文件字典。
    *   `status`: `created` | `modified` | `read_only`
    *   `access_count`: 访问次数（用于判断重要性）
    *   `last_modified_step`: 最后一次修改的步骤 ID
    *   `diff_stats`: (可选) 变更行数 `+10/-5` (通过 `difflib` 计算，不需 LLM)

**Prompt 呈现方式（替代流水账）：**

```text
[Deterministic Project Context]
Active Files (Modified/Created):
- src/nimbus/vcpu.py (Modified 3 times, last at step #15)
- tests/test_vcpu.py (Created at step #16)

Reference Files (Read Only):
- README.md
- config.json
```

**抗漂移原理**：无论任务多长，这个列表永远只展示**结果**。Agent 随时知道哪些文件是它“弄脏”的，哪些是“参考”的。

#### 2.2 机制设计：确定性测试结果解析
目前的 Bash 记录仅是 `Executed: pytest`。这无法告诉 Agent 它的行动是否成功。

**代码逻辑概念：**
编写一个简单的正则解析器（Regex Parser），拦截 `Bash` 工具的输出：
*   如果命令包含 `pytest` 或 `npm test`：
    *   解析 stdout 寻找 `(\d+) passed`, `(\d+) failed`。
    *   **强制注入状态**：如果 `failed > 0`，在上下文中设置一个全局 `Status: FAILED` 标记。

**Prompt 呈现方式：**

```text
[Execution State]
Last Command: pytest tests/test_vcpu.py
Outcome: 🔴 FAILED (2 passed, 1 failed)
Error Context: AssertionError in test_memory_allocation (See tool output for details)
```

**抗漂移原理**：Agent 不需要去翻阅几页前的日志来回忆“我上次测试过了吗？结果是啥？”。状态栏直接告诉它：**现在的代码是坏的**。

### 3. 代码实现建议

在 `src/nimbus/core/memory/mmu.py` 或新建 `state_manager.py` 中实现：

```python
import re
from typing import Dict, Set

class DeterministicStateManager:
    def __init__(self):
        # 文件状态追踪
        self.active_files: Dict[str, dict] = {} 
        # 关键命令结果追踪
        self.last_test_outcome: str = "UNKNOWN"
        self.visited_dirs: Set[str] = set()

    def update(self, tool_name: str, tool_args: dict, tool_output: str):
        """
        每次工具调用后触发，纯规则驱动，无 LLM 介入
        """
        file_path = tool_args.get('file_path')
        
        # 1. 追踪文件状态
        if tool_name == 'Write':
            self.active_files[file_path] = {'status': 'created', 'ops': 1}
        elif tool_name == 'Edit':
            if file_path not in self.active_files:
                self.active_files[file_path] = {'status': 'modified', 'ops': 0}
            self.active_files[file_path]['ops'] += 1
            self.active_files[file_path]['status'] = 'modified'
        elif tool_name == 'Read':
            # 只记录路径，不改变状态
            pass

        # 2. 追踪目录关注点 (用于 Heuristic Context Pruning)
        if file_path:
             # 简单提取目录: src/core/utils.py -> src/core
            directory = "/".join(file_path.split("/")[:-1])
            self.visited_dirs.add(directory)

        # 3. 追踪测试结果 (规则引擎)
        if tool_name == 'Bash':
            cmd = tool_args.get('command', '')
            if 'pytest' in cmd or 'npm test' in cmd:
                if 'failed' in tool_output.lower() or 'error' in tool_output.lower():
                     # 这里可以用更复杂的正则提取具体 failed 数量
                    self.last_test_outcome = "FAILED" 
                else:
                    self.last_test_outcome = "PASSED"

    def get_context_block(self) -> str:
        """
        生成注入 System Prompt 的确定性上下文块
        """
        # 按目录聚合文件，减少 Token
        files_display = []
        for f, meta in self.active_files.items():
            files_display.append(f"{f} ({meta['status']})")
            
        return f"""
## Project State (Auto-Generated)
- **Active Context**: {len(self.active_files)} files modified.
- **Last Test Status**: {self.last_test_outcome}
- **Focus Areas**: {', '.join(list(self.visited_dirs)[:5])}
- **Modified Files**:
  {'\n  '.join(files_display)}
"""
```

### 4. 总结与下一步建议

原文档的 `Milestone` 设计是很好的**历史记录 (History)**，但我建议在此基础上增加**状态管理 (State Management)**。

**修正建议：**
1.  **保留 Milestone 列表**，但对其进行**折叠**。例如，连续针对同一文件的 3 个 Edit 操作，在 UI/Context 中合并显示为 `Edited file X (3 times)`。
2.  **新增 `Project State` 区块**（如上代码所示），作为 High Priority Context 始终保留在 Prompt 尾部或 System Prompt 中。
3.  **确定性修剪**：当 `active_files` 超过一定数量（如 20 个）时，根据 `ops` (操作次数) 自动剔除操作最少的文件，将其降级为“已归档”，不再显示在 Active List 中。

这种“代码管理上下文”的方式，比依赖 LLM 写 Summary 要可靠得多，因为它基于**文件系统的客观事实**。


### Opinion from Gemini 3 Pro High (Reasoning)

你好！作为 AI 顾问，我已深入评审了 `docs/deterministic-anti-drift-design.md`。这份文档提出的“工具调用审计轨迹 (Tool Call Audit Trail)”方向非常正确，切中了当前长上下文任务中 LLM 容易“遗忘”或“幻觉”的痛点。

目前的提案处于 **“日志化 (Logging)”** 阶段，即忠实记录发生了什么。为了达到更强的抗漂移效果，建议将其升级为 **“状态化 (State Tracking)”** 阶段。

以下是我的详细评审意见与改进建议，专注于**完全脱离 LLM 智能总结、基于纯代码逻辑的上下文管理机制**：

### 1. 核心评审结论

*   **优点**：
    *   **方向正确**：将 `ToolResult` 作为客观事实来源，消除了 LLM 总结的主观偏差。
    *   **去重策略有效**：针对 `Read` 操作的去重设计（Filename Deduplication）能有效防止 Token 爆炸。
    *   **零遗漏保证**：Write/Edit 操作的强制记录保证了“物理修改”的可追溯性。

*   **不足/风险**：
    *   **线性堆积风险**：如果任务很长，线性的 `Milestone` 列表本身也会变长，最终挤占上下文。
    *   **缺乏“结果”感知**：仅记录“执行了测试”是不够的，上下文漂移往往发生在“以为测试过了但其实没过”的时候。
    *   **缺乏“状态”视图**：流水账式的记录（Log）不如当前系统的快照（State）直观。

---

### 2. 改进方案：从“流水账”到“结构化状态”

建议在现有设计基础上，增加以下三个**确定性代码机制**，以构建更稳固的抗漂移防线：

#### 机制一：维护“工作集 (Working Set)”而非单纯的日志

目前的 Milestone 是线性的（时间维度）。建议在上下文中维护一个**“活跃文件状态表” (Spatial Dimension)**。

*   **逻辑**：
    *   在内存中维护一个字典 `active_files: Dict[str, FileStatus]`。
    *   当 `Edit/Write` 发生时，更新该文件的状态为 `Modified` 并记录修改次数。
    *   当 `Read` 发生时，更新状态为 `Referenced`。
*   **Prompt 呈现**：
    在 Context 的开头或结尾，通过代码自动注入一个动态更新的 Block，而不是仅仅依赖历史记录列表。

    ```text
    [System: Deterministic Context - Working Set]
    - src/nimbus/vcpu.py (Modified x3, Last: Step 15)
    - config.json (Created, Step 2)
    - README.md (Read only)
    ```
*   **抗漂移价值**：无论对话进行多长，模型永远一眼能看到自己“改了哪些文件”，这是防止漂移的最强锚点。

#### 机制二：结果感知的审计 (Outcome-Aware Auditing)

文档中提到记录 `Executed: pytest`。这还不够。代码应该尝试**解析结果**，因为“失败”的上下文权重远高于“成功”。

*   **改进逻辑**：
    *   针对 `Bash` 工具，增加简单的**确定性后处理 (Deterministic Post-processing)**。
    *   检查 `Exit Code`（如果可用）或通过简单的关键词匹配（如 `failed`, `error`, `traceback`）来标记 Milestone 的性质。
*   **代码示例**：
    ```python
    def _derive_milestone_from_bash(cmd: str, output: str, exit_code: int) -> str:
        base_log = f"Executed: {cmd[:50]}"
        
        # 确定性结果判断
        if exit_code != 0 or "failed" in output.lower() or "error" in output.lower():
            return f"[!] FAILED: {base_log} (Check output details)"
        
        return f"[✓] PASSED: {base_log}"
    ```
*   **抗漂移价值**：模型在回溯时，会优先注意到 `[!]` 标记，避免在错误的基础上继续构建（这是最常见的漂移形式）。

#### 机制三：确定性的上下文剪枝 (Deterministic Pruning)

当 Milestone 列表过长时，LLM 总结会丢失细节。建议采用**基于规则的层级压缩**。

*   **逻辑**：
    *   **最近保留 (Recency)**：保留最近 10 条原始 Milestone。
    *   **关键保留 (Pinned Events)**：`File Create` 和 `Git Commit` 永远不压缩。
    *   **同类折叠 (Folding)**：连续的同类操作在旧历史中自动折叠。
*   **示例**：
    *   Step 1-5 的 5 次 `Edited vcpu.py` -> 折叠为 `[History] Heavily modified vcpu.py (5 edits)`。
    *   这种折叠由代码逻辑完成（检测连续相同 Action），完全不需要 LLM 参与。

---

### 3. 落地建议 (Actionable Advice)

针对 `src/nimbus/core/memory/mmu.py` 的具体修改建议：

1.  **引入 `StructuredState` 类**：除了 `milestones` 列表，新增一个 `file_state_tracker`。
2.  **增强 `_derive_milestone_from_action`**：
    *   传入 `tool_result` (output) 而不仅仅是 `tool_args`，以便判断执行结果（成功/失败）。
3.  **Prompt 模板更新**：
    *   在 System Prompt 中预留 `{{WORKING_SET_SNAPSHOT}}` 槽位，由代码实时填充。

### 总结

这份设计文档已经具备了很好的基础。要做到极致的“抗漂移”，关键在于：**不要让 LLM 去回忆它做了什么，而是由系统直接把“案发现场（修改过的文件、失败的测试）”贴在它脑门上。**

如果您同意这个方向，我可以为您草拟 `FileStateTracker` 或增强版 `MMU` 的代码实现。
