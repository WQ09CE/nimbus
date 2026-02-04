# Nimbus "Deterministic Anti-Drift" (确定性抗漂移) 设计提案

## 背景
目前的抗漂移机制（Milestone 提取）强依赖 LLM 的自我总结。
**风险**：如果 LLM 在 Summary 阶段“走神”或忽略了某个关键步骤，该 Milestone 就会永久丢失。
**需求**：需要一种**不依赖 LLM 解释就能确定的事实记录机制**，通过代码手段增强系统的“硬记忆”。

## 核心理念：Tool Call Audit Trail (工具调用审计轨迹)
利用 `ToolResult` 的客观事实，自动生成不可辩驳的 Milestone，绕过 LLM 的主观总结环节。

## 详细设计

### 1. 关键工具白名单与策略

| 工具类型 | 判定逻辑 | 记录策略 | Milestone 格式示例 |
| :--- | :--- | :--- | :--- |
| **Write** | 文件创建/覆盖 | **必须记录** | `[x] Created file: config.json` |
| **Edit** | 文件内容修改 | **必须记录** | `[x] Edited file: src/nimbus/vcpu.py` |
| **Bash** | 关键命令 (git/pip/test) | **正则匹配记录** | `[x] Executed: git commit ...`<br>`[x] Executed: pip install ...`<br>`[x] Executed: python test.py` |
| **Read** | 文件读取 | **智能去重记录** | `[x] Read file: README.md` (同一文件多次读取只记一次) |

### 2. Read 工具的特殊处理 (智能去重)
为了防止分析任务中 `Read` 操作产生的 Milestone 爆炸：
*   **Filename Deduplication**: 以文件路径为 Key。
*   **状态检查**: 在添加 Milestone 前，检查 Pinned Context 中是否已存在相同路径的 `Read` 记录。
*   **效果**: 无论分片读取多少次 `vcpu.py`，Milestone 列表中只会出现一行 `[x] Read file: src/nimbus/vcpu.py`。

### 3. 实现逻辑 (`src/nimbus/core/memory/mmu.py`)

在 `add_tool_result` 方法中注入自动审计钩子：

```python
def add_tool_result(self, tool_call_id: str, name: str, content: str, tool_args: dict = None) -> None:
    # ... 原有逻辑 ...
    
    # 确定性抗漂移：自动提取关键操作作为里程碑
    # 仅记录成功的操作（非 Error）
    if not content.startswith("[Error]") and tool_args:
        auto_milestone = self._derive_milestone_from_action(name, tool_args)
        if auto_milestone:
            self.add_milestones([auto_milestone])

def _derive_milestone_from_action(self, name: str, args: dict) -> Optional[str]:
    """根据工具行为生成确定性 Milestone"""
    if name == "Write":
        return f"Created/Wrote file: {args.get('file_path')}"
    elif name == "Edit":
        return f"Edited file: {args.get('file_path')}"
    elif name == "Read":
        # Read 操作：文件名去重逻辑在 add_milestones 中处理
        return f"Read file: {args.get('file_path')}"
    elif name == "Bash":
        cmd = args.get("command", "")
        # 仅记录关键指令，忽略 ls/pwd/cd 等探索性指令
        if any(x in cmd for x in ["git ", "pip ", "python ", "npm ", "pytest"]):
            return f"Executed: {cmd[:50]}" # 截断过长指令
    return None
```

### 4. 预期收益
1.  **零遗漏**: 关键操作（改代码、跑测试）绝对会被记录。
2.  **零漂移**: Milestone 基于客观事实，不随 LLM 的注意力变化而改变。
3.  **上下文增强**: 即使 Summary 被压缩得很短，Milestone 列表也能提供完整的“行动轨迹”，帮助 AI 在长任务后期快速找回状态。
