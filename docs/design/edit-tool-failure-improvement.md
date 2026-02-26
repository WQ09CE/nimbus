# Edit 工具失败根因分析与改进方案

> 背景：2026-02-26 处理 TSX/React 文件时 Edit 工具在单次会话内出现 66 次 `TOOL_FAILURE`，三级恢复机制未能有效阻止。本文档分析根因并设计改进方案。

---

## 一、根因分析

### 根因一：LLM 幻觉 old_text（最主要，占 ~60% 失败）

**现象**：LLM 未读文件直接生成 old_text，或读后因上下文压缩导致记忆失真。

**代码溯源**：
- `edit.py` 的 fuzzy match 只做空白/引号/破折号归一化，无法处理内容层面的幻觉
- `EditStringNotFoundHandler.attempt == 2` 确实触发了 `auto_read`，但读到的内容以 `[Auto-Recovery Output]` 追加在错误消息后，**仍属于 ERROR 状态消息**，LLM 下一轮生成时把错误消息当背景噪音而非新的事实基准

**核心矛盾**：auto_read 把文件内容 embed 在 ERROR 消息里，LLM 必须"在错误消息内提取有效事实"，认知负担高，极易再次生成幻觉。

---

### 根因二：TSX className 模板字符串匹配极难

**现象**：`` className={`w-1 self-stretch ${stripColor}`} `` vs `` className={`w-0.5 rounded-full ${stripColor}`} `` 细微差异。

**代码溯源**（`utils.py`）：
```python
def normalize_for_fuzzy_match(text: str) -> str:
    # 只处理：trailing whitespace、smart quotes、Unicode dashes、special spaces
    # ❌ 未处理：模板字符串内部换行折叠、多余空格
```
fuzzy match 对行内空格差异无能为力，精确匹配必然失败。

---

### 根因三：多次 Edit 同一文件时 old_text 过时

**现象**：第一次 Edit 成功修改文件后，LLM 第二次 Edit 还在用修改前的 old_text。

**代码溯源**：
- 成功的 Edit 只返回 diff 预览（最多 20 行），LLM 不一定感知到后续 old_text 已失效
- 没有机制警告"此文件已被修改，请重新 Read"

---

### 根因四：错误消息里的 fuzzy diff 被 LLM 忽略

**现象**：`Could not find exact text... Closest match found (similarity diff): --- your old_string +++ actual content` 已提供差异，但 LLM 仍重复相同错误。

**代码溯源**（`edit.py` 第 105-120 行）：
```python
diff_text = "".join(diff_lines[:30])  # 截断 30 行
raise ValueError(
    f"Could not find the exact text to replace in {file_path}.\n"
    f"Closest match found (similarity diff):\n{diff_text}\n"
    f"Check for whitespace, indentation, or content differences."
)
```
问题：
1. 错误消息结构扁平，diff 淹没在文字中，没有突出视觉分隔
2. 错误行动指导模糊（"Check for whitespace"），LLM 不知道该做什么
3. diff 前 30 行可能只展示上下文，关键差异行被截掉

---

## 二、改进方案

### 改进一：Error 消息重构——让 diff 更突出、行动指导更明确

**文件**：`src/nimbus/tools/edit.py`

**当前**（问题）：
```
Could not find the exact text to replace in {file_path}.
Closest match found (similarity diff):
--- your old_string
+++ actual content
...diff...
Check for whitespace, indentation, or content differences.
```

**改进后**：
```
╔══ EDIT FAILED: Text not found in {file_path} ══╗

Your old_text does NOT match the file. Here is what the file ACTUALLY contains
at the closest matching location:

━━━━━━━━━━━━━━━━━━ DIFF (your old_text → file content) ━━━━━━━━━━━━━━━━━━
--- old_text (what you sent)
+++ file content (ground truth)
{diff_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚡ REQUIRED ACTION: You MUST do ONE of the following:
  Option A: Read("{file_path}") → inspect current content → retry Edit with EXACTLY the content shown above
  Option B: If you cannot find the right text, use Write("{file_path}", ...) to replace the whole file
  
  ❌ DO NOT retry Edit with the same old_text — it will fail again.
```

**实现要点**：
- 优先展示 diff 的 `-` 行（删除行 = 你的 old_text）和 `+` 行（新增行 = 实际内容），跳过上下文行来节省 token
- 结构化提示（Option A / Option B）消除歧义

---

### 改进二：工具 description 预防性声明

**文件**：`src/nimbus/tools/__init__.py`，`EDIT_TOOL["description"]`

**当前**：
```
Edit a file by replacing exact text. The oldText must match exactly
(including whitespace). Use this for precise, surgical edits.
Falls back to fuzzy matching if exact match fails.
```

**改进后**：
```
Edit a file by replacing exact text. The old_text must match the file EXACTLY
(including all whitespace, quotes, and template string characters like backticks).

⚠ CRITICAL RULES to avoid failure:
1. Always Read the file first before writing old_text — do NOT guess content from memory.
2. After each successful Edit, treat the file as CHANGED. Re-read before the next Edit on the same file.
3. For TSX/JSX className with template strings (backtick + ${...}), copy text character-for-character from Read output.
4. If Edit fails twice, switch to Write to replace the entire file instead of retrying.
5. old_text must be UNIQUE in the file. If it appears multiple times, add more surrounding lines for context.
```

---

### 改进三：恢复策略重构——auto_read 真正有效化

**问题根因**：当前 attempt 2 的 `auto_read` 把文件内容 embed 在 **ERROR 状态消息**里，LLM 把它当错误日志而非权威事实。

**改进方案**：将 attempt 2 从 `auto_tool` 改为 `inject_hint + 强制阅读指令`，并引入新的 `FORCED_READ` action type。

#### 3.1 新增 `forced_read` RecoveryAction

**文件**：`src/nimbus/core/runtime/error_handler.py`

```python
@classmethod
def forced_read(cls, file_path: str, hint: str) -> "RecoveryAction":
    """
    强制读取文件，将结果作为 OK 消息注入，而非 ERROR 追加。
    这让 LLM 把文件内容当作"权威事实"而非错误消息的一部分。
    """
    return cls(
        action_type="forced_read",
        auto_tool="Read",
        auto_args={"file_path": file_path},
        hint=hint,
    )
```

#### 3.2 RecoveryExecutor 处理 forced_read

**文件**：`src/nimbus/core/runtime/recovery_executor.py`

```python
async def _handle_forced_read(
    self, recovery: RecoveryAction, ctx: RecoveryContext
) -> Optional[ToolResult]:
    """
    forced_read: 执行 Read 并将结果以 OK 状态返回。
    关键：status="OK" 让 LLM 把内容当权威事实，而非错误消息。
    """
    recovery_action = ActionIR(
        kind="TOOL_CALL",
        name="Read",
        id=f"recovery_read_{ctx.original_action.id}",
        args=recovery.auto_args,
        meta={"recovery_for": ctx.original_action.name},
    )
    read_result = await self._execute_tool(recovery_action, ctx.default_timeout)
    
    if read_result.status != "OK":
        return None  # 读文件都失败，走兜底
    
    # 关键：以 OK 状态返回，hint 作为引导语
    hint_header = recovery.hint or f"[Auto-Read] Current file content:"
    return ToolResult(
        status="OK",
        output=(
            f"{hint_header}\n\n"
            f"{read_result.output}\n\n"
            f"⚡ Now retry Edit with old_text copied EXACTLY from the content above."
        ),
    )
```

#### 3.3 EditStringNotFoundHandler 调整策略

**文件**：`src/nimbus/core/runtime/error_handler.py`，`EditStringNotFoundHandler.handle()`

| Attempt | 当前策略 | 改进策略 |
|---------|---------|---------|
| 1 | inject hint + fuzzy diff | **保留**，但强化错误消息格式（见改进一） |
| 2 | auto_tool Read（ERROR 状态） | **改为 forced_read（OK 状态）**，LLM 重新定向到事实 |
| 3 | generic 提示 | **Write 降级提示**（见改进四） |

```python
elif attempt == 2:
    file_path = args.get("file_path", "")
    return RecoveryAction.forced_read(
        file_path=file_path,
        hint=(
            f"⚠ Edit failed again. Automatically reading current file content.\n"
            f"Use EXACTLY the text below as old_text in your next Edit call."
        ),
    )
```

---

### 改进四：Write 全量替换降级策略

**触发条件**：attempt >= 3（连续 3 次 Edit 失败）

**文件**：`src/nimbus/core/runtime/error_handler.py`

```python
else:  # attempt >= 3
    file_path = args.get("file_path", "")
    return RecoveryAction.inject(
        f"🚨 EDIT REPEATEDLY FAILED ({attempt} attempts) on '{file_path}'.\n\n"
        f"STOP using Edit. Switch to Write strategy:\n"
        f"  1. Read('{file_path}')  ← get complete current content\n"
        f"  2. Make your changes mentally on the COMPLETE content\n" 
        f"  3. Write('{file_path}', content='...complete new content...')\n\n"
        f"Write replaces the entire file and never fails due to text matching.\n"
        f"DO NOT call Edit again for this file."
    )
```

---

### 改进五（可选）：Patch 模式——行号定位编辑

针对 TSX 这类模板字符串密集的文件，引入基于行号的 `EditLines` 工具作为 Edit 的补充。

#### 5.1 工具签名设计

```python
async def edit_lines(
    file_path: str,
    start_line: int,       # 1-indexed，包含
    end_line: int,         # 1-indexed，包含
    new_content: str,      # 替换后的内容（不含行号）
    workspace: Optional[Path] = None,
) -> str:
    """
    Replace lines [start_line, end_line] with new_content.
    Line numbers from Read tool output can be used directly.
    """
```

#### 5.2 工具 description

```
EditLines replaces a range of lines in a file using line numbers from Read output.
Use this instead of Edit when:
- The text contains template strings, backticks, or complex JSX/TSX
- Edit has already failed due to whitespace/quote matching issues

Workflow:
  1. Read("file.tsx")  ← note the line numbers in output
  2. EditLines("file.tsx", start_line=42, end_line=45, new_content="...")

start_line and end_line are 1-indexed and inclusive.
new_content replaces the entire range (can be more or fewer lines than original).
```

#### 5.3 实现关键点

```python
# edit_lines.py 核心逻辑
lines = content.splitlines(keepends=True)
total = len(lines)

if start_line < 1 or end_line > total or start_line > end_line:
    raise ValueError(f"Invalid line range [{start_line}, {end_line}], file has {total} lines")

new_lines = (
    lines[:start_line - 1]           # before range
    + [new_content if new_content.endswith('\n') else new_content + '\n']
    + lines[end_line:]                # after range  
)
```

**Read 工具适配**：确认 `read.py` 输出行号（当前 `utils.py` 的 diff 函数已带行号，Read 工具需确认输出格式带行号标注）。

---

## 三、改进优先级与实施路线图

| 优先级 | 改进项 | 工作量 | 预期收益 |
|--------|--------|--------|---------|
| P0 | **改进三**：forced_read（OK 状态）替换 auto_read（ERROR 状态） | 小（~30行） | 直接解决 attempt 2 无效问题 |
| P0 | **改进一**：错误消息重构，突出 diff + 明确 Option A/B | 小（~20行） | 减少 LLM 忽略错误细节 |
| P1 | **改进二**：EDIT_TOOL description 预防性声明 | 极小（5行文本） | 从源头减少幻觉 old_text |
| P1 | **改进四**：attempt 3 强制降级到 Write | 极小（~10行） | 防止 66 次失败循环 |
| P2 | **改进五**：EditLines 工具 | 中（~100行） | 根治 TSX 匹配问题 |

---

## 四、改进前后对比

### 当前失败链路（66 次失败的原因）

```
Edit(幻觉 old_text) 
  → FAIL
  → attempt 1: inject hint + diff  [LLM 看到 diff 但不知道怎么用]
  → Edit(又一次幻觉 old_text)
  → FAIL  
  → attempt 2: auto_read → ERROR 状态消息  [LLM 把文件内容当错误日志噪音]
  → Edit(第三次幻觉)
  → FAIL
  → attempt 3: generic 提示  [完全没有行动指导]
  → Edit(第四次幻觉)  ← 三级恢复耗尽，之后无限失败
  → FAIL × 60
```

### 改进后链路

```
Edit(幻觉 old_text)
  → FAIL
  → attempt 1: 结构化错误消息（视觉突出 diff + "Option A/B"）
  → Edit(LLM 参考 diff 修正)  ← 大部分幻觉在这里被修正
  → 若仍 FAIL:
  → attempt 2: forced_read → OK 状态 + 文件完整内容 + 明确指令
  → Edit(基于实际内容)  ← 几乎不可能再失败
  → 若仍 FAIL:
  → attempt 3: 强制降级 Write  ← 彻底绕过文本匹配
  → Write(完整替换)  ← 必然成功
```

---

## 五、附：TSX 匹配失败的特殊处理

针对 TSX/JSX 中 `className` 模板字符串的极端情况，除了 EditLines 工具外，还可以在 `normalize_for_fuzzy_match` 里增加对模板字符串的特殊处理：

```python
# utils.py 追加到 normalize_for_fuzzy_match()

# 折叠模板字符串内的多余空白（保留单个空格）
# 例：`w-1  self-stretch  ${x}` → `w-1 self-stretch ${x}`
result = re.sub(r'(`[^`]*`)', lambda m: re.sub(r'\s+', ' ', m.group(0)), result)
```

但这是微优化，治标不治本。**根治方案是 EditLines + forced_read**。

---

*文档版本：2026-02-26 | 作者：Architect Agent*
