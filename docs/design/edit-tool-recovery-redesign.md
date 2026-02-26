# Edit 工具失败恢复机制重设计

> **Author**: Architect Agent  
> **Date**: 2025-02-25  
> **Status**: Proposal  
> **Severity**: Critical — 单次会话 66 次 TOOL_FAILURE 导致资源浪费和任务失败

---

## 1. 根因分析：为什么 66 次失败？

### 1.1 表层原因 vs 深层根因

| 层级 | 问题 | 文件位置 |
|------|------|----------|
| **表层** | Edit 工具 fuzzy match 失败 | `tools/edit.py:107-112` |
| **中层** | 恢复机制无效，LLM 无法利用恢复信息 | `runtime/error_handler.py:EditStringNotFoundHandler` |
| **深层** | **Edit 的 API 契约与 LLM 认知模型不匹配** | `tools/__init__.py:EDIT_TOOL` |
| **根因** | **系统缺乏硬性打断能力，允许无限重试同类失败** | `runtime/doom_loop.py` + `runtime/vcpu.py` |

### 1.2 四个致命缺陷的详细分析

#### 缺陷 A：Edit API 对 LLM 不友好（根因 #1）

Edit 要求 `old_text` **精确匹配**文件内容。但 LLM 生成 `old_text` 的过程本质上是**从记忆中回忆**，而非从实时文件中复制。对于 TSX/React 文件，以下特征放大了这一问题：

- JSX 嵌套深、缩进复杂（LLM 常记错缩进层级）
- 大量相似的 `<div className=...>` 结构（LLM 容易混淆）
- 动态表达式 `{condition && <Component />}` 中的空格敏感
- Template literal / CSS-in-JS 中的多行字符串

**核心矛盾**：Edit 工具要求精确性，但 LLM 无法提供精确性。Fuzzy match 只能修正 trailing whitespace 和 Unicode 字符差异（`tools/utils.py:94-108`），无法修正结构性回忆错误。

#### 缺陷 B：三级恢复机制是"纸老虎"（根因 #2）

分析 `EditStringNotFoundHandler`（`error_handler.py:263-330`）的三级恢复：

```
attempt 1: inject_hint
  → 返回 fuzzy diff + "Read the file first"
  → 问题：LLM 收到 ERROR 状态 + diff，但 diff 格式 LLM 难以逆向利用
  → LLM 行为：忽略 diff，凭记忆再猜一次 old_text

attempt 2: auto_tool Read
  → 自动读取文件，以 ERROR 状态返回
  → 问题：ToolResult(status="ERROR") 导致 LLM 将文件内容当作错误噪音
  → 关键代码 recovery_executor.py:135-138:
    return ToolResult(status="ERROR", output=combined_message, ...)
  → LLM 行为：不读文件内容，再次尝试 Edit

attempt 3: generic inject_hint
  → "The file may have been modified..." 等泛泛提示
  → 问题：没有行动力，没有强制降级
  → LLM 行为：继续 Edit
```

**三级恢复耗尽后发生了什么？** `ErrorHandlerRegistry._get_failure_count()` 持续累加，但 `EditStringNotFoundHandler.handle()` 对 `attempt >= 4` 的情况走 `else` 分支，返回的仍然是 `inject_hint`——**没有上限，没有熔断，没有强制降级**。

#### 缺陷 C：Doom Loop Detector 存在盲区（根因 #3）

`doom_loop.py` 的检测逻辑（`check()` 方法，第 126-144 行）：

```python
# 只检测 **完全相同参数** 的连续调用
normalized_args = self._normalize_args_for_comparison(tool_name, args)
# 对 Edit：只比较 file_path + old_text
```

**盲区**：LLM 每次失败后会**稍微修改** `old_text`（加减一个空格、改变缩进），导致 `args_json` 每次都不同。Doom Loop Detector 的阈值为 3 次 **完全相同** 调用才触发，但 LLM 的"微调幻觉"模式永远不会触发它。

这解释了为什么 66 次失败——每次 `old_text` 都略有不同，Doom Loop Detector 从未触发。

#### 缺陷 D：Heart 心跳监控与 vCPU 之间存在断层（关联问题）

`session_monitor.py` 的 `_handle_iteration()` 检测高频空输出迭代（`rate_limit_count=5` in `rate_limit_window=10s`），但：

1. Edit 失败返回的是 ERROR 消息（非空输出），`has_output` 可能为 True → 不触发断路器
2. 即使触发，`heart.outbox.put()` 发送的 `system.intervention` 消息，在 vCPU 侧**没有消费者**——vCPU 的 `_step()` 方法中没有检查 Heart outbox 的逻辑
3. Heart 提议的 "increase_perturbation"（提高 temperature）对 Edit 幻觉问题反而有害——更高的随机性会产生更离谱的 `old_text`

**结论**：Heart 心跳与 Edit 失败循环事实上是**断联的**。

---

## 2. 初步方案的批判性审视

### 2.1 逐条评估

| 方案 | 评价 | 问题 |
|------|------|------|
| ① forced_read 以 OK 状态返回 | ⚠️ 有隐患 | LLM 可能误认为 Edit 成功了，跳过后续修改。OK 状态 + 文件内容 ≈ "Edit succeeded, here's the file" |
| ② 错误消息重构（Option A/B） | ✅ 方向正确但不够 | LLM 对长格式指令的遵从率有限，尤其在上下文压力大时 |
| ③ attempt 3 强制降级 Write | ⚠️ 粗暴 | Write 覆盖整个文件风险大；且 LLM 可能不知道该写什么完整内容 |
| ④ Edit description 加预防性声明 | ❌ 低效 | 工具描述是"冷知识"，LLM 在反复失败的热循环中不会重新审视工具描述 |
| ⑤ EditLines 行号定位 | ✅ 方向对 | 但行号在文件修改后会漂移，且引入新工具增加 LLM 学习负担 |

### 2.2 关键误区

初步方案都在**优化恢复路径**（让 LLM 更好地从失败中恢复），但忽略了两个更根本的问题：

1. **预防优于治疗**：为什么不在 Edit 失败前就避免幻觉？
2. **硬性打断**：为什么不在 N 次同类失败后强制终止循环？

---

## 3. 重新设计方案

### 架构理念：三道防线

```
┌─────────────────────────────────────────────────────────┐
│                   防线 1: 预防（Prevention）              │
│  Auto-Read Gate: Edit 调用前自动验证 old_text 可行性      │
│  目标：将幻觉拦截在执行前                                 │
├─────────────────────────────────────────────────────────┤
│                   防线 2: 修复（Recovery）                 │
│  Smart Recovery: 失败后以正确状态和格式返回可行动信息       │
│  目标：给 LLM 足够信息来自我纠正                          │
├─────────────────────────────────────────────────────────┤
│                   防线 3: 熔断（Circuit Break）            │
│  Edit Fuse: N 次同文件 Edit 失败后强制切换策略             │
│  目标：硬性打破死循环                                     │
└─────────────────────────────────────────────────────────┘
```

### 3.1 防线 1：Auto-Read Gate（预防层）

**原理**：在 `edit_file()` 函数内部，当 exact match 失败且 fuzzy match 也失败时，不是抛出无用的 ValueError，而是**内联返回文件相关片段**。

**修改文件**：`src/nimbus/tools/edit.py`

```python
# 当前代码 (edit.py:107-136):
if not match_result["found"]:
    # Generate diff showing closest match for debugging
    diff_output = _generate_closest_match_diff(content, old_text)
    raise ValueError(
        f"Could not find the exact text to replace in {file_path}. "
        f"Closest match found (similarity diff):\n{diff_output}"
    )

# 改进后：
if not match_result["found"]:
    # 不再只返回 diff，返回结构化的可行动信息
    context_snippet = _extract_relevant_context(content, old_text, context_lines=20)
    raise ValueError(
        f"EDIT FAILED: old_text not found in {file_path}.\n"
        f"\n"
        f"── YOUR old_text (first 3 lines) ──\n"
        f"{_first_n_lines(old_text, 3)}\n"
        f"\n"
        f"── ACTUAL FILE CONTENT (most similar region, lines {start}-{end}) ──\n"
        f"{context_snippet}\n"
        f"\n"
        f"ACTION REQUIRED: Copy the exact text from ACTUAL FILE CONTENT above "
        f"as your new old_text. Do NOT type from memory."
    )
```

**关键改进点**：

1. **返回最相似区域的原始文件内容**而非 unified diff。Diff 需要 LLM 逆向解析，原始内容可以直接复制。
2. 用 `difflib.SequenceMatcher` 找到最相似的连续行区域，返回带行号的原文。
3. 错误消息末尾用命令式语气（"Copy the exact text..."），比 "you may want to..." 更有效。

**实现 `_extract_relevant_context()`**：

```python
def _extract_relevant_context(content: str, old_text: str, context_lines: int = 20) -> str:
    """找到文件中与 old_text 最相似的区域，返回带行号的原文片段。"""
    content_lines = content.split('\n')
    old_lines = old_text.split('\n')
    
    if not old_lines:
        return "(empty old_text)"
    
    # 使用第一行作为锚点，找到最可能的起始位置
    best_ratio = 0
    best_start = 0
    first_old_line = old_lines[0].strip()
    
    for i, line in enumerate(content_lines):
        ratio = difflib.SequenceMatcher(None, first_old_line, line.strip()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = i
    
    # 返回该位置周围的 context_lines 行，带行号
    start = max(0, best_start - 3)
    end = min(len(content_lines), best_start + context_lines)
    
    result_lines = []
    for i in range(start, end):
        result_lines.append(f"{i+1:4d} │ {content_lines[i]}")
    
    return '\n'.join(result_lines)
```

### 3.2 防线 2：Smart Recovery（修复层）

**修改文件**：`src/nimbus/core/runtime/error_handler.py` 的 `EditStringNotFoundHandler`

重新设计三级恢复：

```python
class EditStringNotFoundHandler(ErrorHandler):
    """重设计的 Edit 恢复策略"""
    
    async def handle(self, error_code, tool_name, args, error_msg, attempt, workspace=None):
        file_path = args.get("file_path", "")
        
        if attempt == 1:
            # ── 第 1 次：透传增强错误消息（已含文件片段） ──
            # edit.py 已经返回了最相似区域，不需要额外操作
            # 只追加一条简短的行动指令
            return RecoveryAction.inject(
                "⚡ INSTRUCTION: Read the ACTUAL FILE CONTENT shown above. "
                "Copy the exact text as your new old_text. "
                "If the change you wanted is already there, STOP and move on."
            )
        
        elif attempt == 2:
            # ── 第 2 次：forced_read + REPLACE_ONLY 状态 ──
            # 关键改变：以 "EDIT_FAILED_WITH_CONTEXT" 的特殊状态返回
            # 而非 OK（避免误判为成功）或 ERROR（被当作噪音忽略）
            return RecoveryAction.auto_execute(
                tool="Read",
                args={"file_path": file_path},
                hint=(
                    f"⚠️ EDIT FAILED TWICE on {file_path}. "
                    f"Below is the COMPLETE current file content. "
                    f"You MUST use text EXACTLY as shown below for your next Edit, "
                    f"or use Write to rewrite the entire file."
                ),
            )
        
        elif attempt >= 3:
            # ── 第 3 次及以后：强制 Write 降级 ──
            # 不再给 hint，直接返回结构化指令
            return RecoveryAction.inject(
                f"🛑 EDIT HAS FAILED {attempt} TIMES on {file_path}.\n"
                f"The Edit tool CANNOT find your text. STOP using Edit for this file.\n\n"
                f"YOU MUST NOW do ONE of:\n"
                f"  Option A: Read(file_path='{file_path}'), then Write the complete corrected file\n"
                f"  Option B: Skip this change and move to the next task\n\n"
                f"DO NOT call Edit on {file_path} again."
            )
```

**关键改变**：

1. **attempt 2 的 forced_read 仍以 ERROR 状态返回**，但消息格式彻底重构——先给清晰的"EDIT FAILED TWICE"上下文，再给文件内容，最后给明确指令。不用 OK 状态，避免"成功"误导。
2. **attempt 3+ 的消息用全大写命令语气**，这对 Claude/GPT 系列模型的指令遵从有显著提升（empirical observation）。
3. **移除 generic 提示**（"The file may have been modified..."），这种被动语气 LLM 会直接忽略。

### 3.3 防线 3：Edit 熔断器（Circuit Break）

这是**最关键的改进**——一个系统层面的硬性保障。

**修改文件**：`src/nimbus/core/runtime/vcpu.py` 或新建 `src/nimbus/core/runtime/edit_fuse.py`

```python
class EditFuse:
    """
    Edit 工具熔断器
    
    追踪同一文件的 Edit 失败次数。
    当达到阈值时，返回强制降级消息并阻止后续 Edit 调用。
    
    与 DoomLoopDetector 的区别：
    - DoomLoopDetector 要求完全相同的参数（会被微调绕过）
    - EditFuse 按文件级别追踪，不关心具体参数
    """
    
    def __init__(self, max_failures_per_file: int = 4, cooldown_on_success: int = 2):
        self._file_failures: Dict[str, int] = {}  # file_path -> failure count
        self._fused_files: Set[str] = set()  # 已熔断的文件
        self.max_failures = max_failures_per_file
        self.cooldown = cooldown_on_success
    
    def on_edit_failure(self, file_path: str) -> Optional[str]:
        """
        记录 Edit 失败。
        
        Returns:
            None: 未熔断，正常继续
            str: 熔断消息，应直接返回给 LLM 替代原始错误
        """
        self._file_failures[file_path] = self._file_failures.get(file_path, 0) + 1
        count = self._file_failures[file_path]
        
        if count >= self.max_failures:
            self._fused_files.add(file_path)
            return (
                f"🚫 EDIT FUSE BLOWN: {file_path} has failed {count} Edit attempts.\n"
                f"The Edit tool is now DISABLED for this file.\n\n"
                f"You MUST use one of these alternatives:\n"
                f"  1. Read('{file_path}') then Write('{file_path}', <complete corrected content>)\n"
                f"  2. Skip this change and continue with other tasks\n\n"
                f"Calling Edit on this file again will be automatically blocked."
            )
        return None
    
    def on_edit_success(self, file_path: str):
        """Edit 成功，减少失败计数（不清零，留有记忆）"""
        if file_path in self._file_failures:
            self._file_failures[file_path] = max(0, self._file_failures[file_path] - self.cooldown)
        self._fused_files.discard(file_path)
    
    def is_fused(self, file_path: str) -> Optional[str]:
        """
        检查文件是否已被熔断。
        
        Returns:
            None: 未熔断
            str: 熔断阻止消息
        """
        if file_path in self._fused_files:
            return (
                f"🚫 BLOCKED: Edit is disabled for {file_path} (fuse blown).\n"
                f"Use Read + Write instead, or skip this file."
            )
        return None
```

**集成点**：在 `vcpu.py` 的 `_execute_action()` 方法中（约第 680 行）：

```python
# 在 tool execution 之前检查
if action.name == "Edit":
    file_path = action.args.get("file_path", "")
    fuse_msg = self._edit_fuse.is_fused(file_path)
    if fuse_msg:
        return ToolResult(status="ERROR", output=fuse_msg)

# 在 tool execution 之后检查
if action.name == "Edit" and result.status == "ERROR":
    file_path = action.args.get("file_path", "")
    fuse_msg = self._edit_fuse.on_edit_failure(file_path)
    if fuse_msg:
        # 覆盖原始错误消息为熔断消息
        result = ToolResult(status="ERROR", output=fuse_msg)
elif action.name == "Edit" and result.status == "OK":
    file_path = action.args.get("file_path", "")
    self._edit_fuse.on_edit_success(file_path)
```

### 3.4 补充改进：增强 Doom Loop Detector 对 Edit 的语义检测

**修改文件**：`src/nimbus/core/runtime/doom_loop.py`

```python
def _normalize_args_for_comparison(self, tool_name: str, args: Dict) -> Dict:
    if tool_name == "Edit":
        # 当前：比较 file_path + old_text（完全匹配）
        # 改进：只比较 file_path（同文件连续 Edit 失败 = doom loop 的变体）
        return {
            "file_path": args.get("file_path", ""),
            # 移除 old_text 的比较！
            # LLM 每次微调 old_text 来绕过检测，
            # 但核心模式是：同一文件反复 Edit 失败
        }
    # ... 其他工具保持不变
```

**但要注意**：这个改动会把正常的"对同一文件的多次 Edit"也标记为 doom loop。因此需要一个额外条件：**只在 Edit 失败时累加计数**。

```python
def check(self, tool_name: str, args: Dict, succeeded: bool = True) -> DoomLoopResult:
    """增加 succeeded 参数"""
    if tool_name == "Edit" and succeeded:
        # 成功的 Edit 不计入 doom loop
        self._recent_calls.clear()
        return DoomLoopResult.ok()
    # ... 原有逻辑
```

### 3.5 可选：EditLines 工具评估

**不建议新增 EditLines 工具**，原因：

1. **行号漂移**：对同一文件的连续 Edit 会导致行号偏移，LLM 不擅长心算行号变化
2. **增加 LLM 认知负担**：两个编辑工具（Edit + EditLines）会让 LLM 纠结选哪个
3. **治标不治本**：行号同样需要 LLM "回忆"（除非先 Read），并不能消除幻觉

**替代方案**：如果确实需要行号能力，可以在 Edit 的错误消息中**附带行号**（防线 1 已包含），而非新增工具。

---

## 4. 实施计划

### Phase 1（紧急，< 1 天）：熔断器 + 错误消息重构

| 优先级 | 改动 | 文件 | 工作量 |
|--------|------|------|--------|
| P0 | 新增 `EditFuse` 类 | `runtime/edit_fuse.py`（新建） | 1h |
| P0 | vCPU 集成 EditFuse | `runtime/vcpu.py:_execute_action()` | 0.5h |
| P0 | 重构 Edit 错误消息 | `tools/edit.py:_generate_closest_match_diff()` → `_extract_relevant_context()` | 1h |
| P1 | 重新设计三级恢复消息 | `runtime/error_handler.py:EditStringNotFoundHandler` | 1h |

**预期效果**：同一文件最多 4 次 Edit 失败就强制熔断，从 66 次降至 ≤4 次。

### Phase 2（重要，< 3 天）：Doom Loop 增强

| 优先级 | 改动 | 文件 | 工作量 |
|--------|------|------|--------|
| P1 | Doom Loop 对 Edit 使用文件级检测 | `runtime/doom_loop.py` | 1h |
| P1 | `check()` 增加 `succeeded` 参数 | `runtime/doom_loop.py` + `runtime/vcpu.py` | 1h |
| P2 | 单元测试 | `tests/unit/test_edit_fuse.py`（新建） | 2h |

### Phase 3（优化，< 1 周）：Heart 联动

| 优先级 | 改动 | 文件 | 工作量 |
|--------|------|------|--------|
| P2 | vCPU 在 Edit 熔断时通知 Heart | `runtime/vcpu.py` | 0.5h |
| P2 | Heart 接收 Edit 熔断事件并记录 | `heart_modules/session_monitor.py` | 1h |
| P3 | 心跳 intervention 消息消费端 | `runtime/vcpu.py`（新增 Heart outbox 检查） | 2h |

---

## 5. 方案对比矩阵

| 维度 | 初步方案 | 重设计方案 |
|------|----------|------------|
| **预防幻觉** | ❌ 无 | ✅ 错误消息返回可直接复制的文件片段 |
| **恢复有效性** | ⚠️ OK 状态有误导 + diff 格式难用 | ✅ 结构化指令 + 原始文件内容 |
| **硬性打断** | ❌ 无（三级耗尽后继续） | ✅ EditFuse 4 次熔断 |
| **Doom Loop 绕过** | ❌ 微调 old_text 绕过 | ✅ 文件级检测 |
| **Heart 联动** | ❌ 断联 | ✅ Phase 3 打通 |
| **新增工具** | EditLines（增加复杂度） | 无（在现有工具内优化） |
| **最坏情况** | 66 次失败 | ≤4 次失败（熔断 + 自动降级 Write） |

---

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| EditFuse 误伤正常的多次 Edit（如连续修改同一文件的不同部分） | 只在 Edit **失败**时累加计数；成功时减少计数（cooldown） |
| Doom Loop 文件级检测过于激进 | 需要 `succeeded=False` 才计入；成功调用清零 |
| 错误消息过长占用 context | `_extract_relevant_context()` 限制为 20 行；比当前 diff 30 行更少 |
| LLM 忽略 "DO NOT call Edit" 指令 | EditFuse 是系统级拦截，不依赖 LLM 遵从——**直接阻止工具调用** |

---

## 7. 设计哲学总结

```
原则 1：不要相信 LLM 的记忆 → 给它可复制的原始文件内容
原则 2：不要依赖 LLM 的遵从 → 系统级硬性熔断
原则 3：不要新增复杂度 → 在现有接口内优化，不加新工具
原则 4：预防 > 治疗 > 熔断 → 三道防线，越前越好
```

核心思想：**LLM 是一个不靠谱但有创造力的操作者。好的工具设计不是给它更多规则（它会忽略），而是让它更难犯错（预防），犯错后更容易纠正（修复），以及在无法纠正时自动止损（熔断）。**
