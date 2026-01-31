# 智能 Error Handler 设计

## 问题分析

当前的 Doom Loop 检测太粗暴：
```
Glob("*test*.py") → No matches
Glob("*test*.py") → No matches  
Glob("*test*.py") → No matches → DOOM_LOOP ERROR ❌
```

Claude Code 的做法更智能：
```
Read("foo.py") → Not found
→ 自动执行 ls 查看当前目录
→ 发现文件在 src/foo.py
→ Read("src/foo.py") ✅
```

## 设计方案

### 1. 错误分类 (Error Categories)

```python
class ToolErrorCode(Enum):
    # 文件系统错误
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    DIRECTORY_NOT_FOUND = "DIRECTORY_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    IS_A_DIRECTORY = "IS_A_DIRECTORY"
    
    # 搜索/匹配错误
    PATTERN_NO_MATCH = "PATTERN_NO_MATCH"      # Glob/Grep 无匹配
    SEARCH_TOO_BROAD = "SEARCH_TOO_BROAD"      # 匹配太多结果
    
    # 编辑错误
    STRING_NOT_FOUND = "STRING_NOT_FOUND"      # Edit 找不到目标字符串
    MULTIPLE_MATCHES = "MULTIPLE_MATCHES"      # Edit 匹配多处
    
    # 执行错误
    COMMAND_FAILED = "COMMAND_FAILED"
    TIMEOUT = "TIMEOUT"
    
    # 通用
    REPEATED_FAILURE = "REPEATED_FAILURE"      # 重复失败（原 DOOM_LOOP）
```

### 2. Error Handler 接口

```python
@dataclass
class RecoveryAction:
    """恢复动作"""
    action_type: Literal["inject_hint", "auto_tool", "modify_args", "skip"]
    hint: Optional[str] = None           # 注入给 LLM 的提示
    auto_tool: Optional[str] = None      # 自动执行的工具
    auto_args: Optional[dict] = None     # 自动工具的参数
    modified_args: Optional[dict] = None # 修改后的参数重试

class ErrorHandler(Protocol):
    """错误处理器接口"""
    
    def can_handle(self, error_code: ToolErrorCode, tool_name: str) -> bool:
        """是否能处理此错误"""
        ...
    
    async def handle(
        self,
        error_code: ToolErrorCode,
        tool_name: str,
        args: dict,
        error_msg: str,
        attempt: int,  # 第几次尝试
    ) -> RecoveryAction:
        """返回恢复动作"""
        ...
```

### 3. 内置 Error Handlers

#### FileNotFoundHandler
```python
class FileNotFoundHandler(ErrorHandler):
    """处理文件找不到的情况"""
    
    async def handle(self, error_code, tool_name, args, error_msg, attempt):
        file_path = args.get("file_path", "")
        
        if attempt == 1:
            # 第一次：尝试模块解析 (index.ts 等)
            return RecoveryAction(
                action_type="modify_args",
                modified_args={"file_path": f"{file_path}/index.ts"}
            )
        
        elif attempt == 2:
            # 第二次：注入提示 + 自动列目录
            return RecoveryAction(
                action_type="auto_tool",
                auto_tool="Bash",
                auto_args={"command": f"ls -la {os.path.dirname(file_path) or '.'}"},
                hint=f"File '{file_path}' not found. Here are the files in that directory:"
            )
        
        else:
            # 第三次：建议用 Glob 搜索
            filename = os.path.basename(file_path)
            return RecoveryAction(
                action_type="inject_hint",
                hint=f"💡 File not found. Try: Glob(pattern='**/{filename}')"
            )
```

#### PatternNoMatchHandler
```python
class PatternNoMatchHandler(ErrorHandler):
    """处理 Glob/Grep 无匹配的情况"""
    
    async def handle(self, error_code, tool_name, args, error_msg, attempt):
        pattern = args.get("pattern", "")
        
        if attempt == 1:
            # 第一次：静默，让 LLM 自己调整
            return RecoveryAction(action_type="skip")
        
        elif attempt == 2:
            # 第二次：自动列出当前目录帮助定位
            return RecoveryAction(
                action_type="auto_tool",
                auto_tool="Bash",
                auto_args={"command": "ls -la"},
                hint=f"Pattern '{pattern}' matched nothing. Current directory contents:"
            )
        
        else:
            # 第三次：建议更宽泛的模式
            return RecoveryAction(
                action_type="inject_hint",
                hint=(
                    f"Pattern '{pattern}' still not matching.\n"
                    "Options:\n"
                    "1. Try a broader pattern: **/*.py\n"
                    "2. List all files first: Bash('find . -type f | head -20')\n"
                    "3. If this file doesn't exist, use return_result to report"
                )
            )
```

#### EditStringNotFoundHandler
```python
class EditStringNotFoundHandler(ErrorHandler):
    """处理 Edit 找不到目标字符串"""
    
    async def handle(self, error_code, tool_name, args, error_msg, attempt):
        file_path = args.get("file_path", "")
        old_string = args.get("old_string", "")[:50]
        
        if attempt == 1:
            # 第一次：自动读取文件当前内容
            return RecoveryAction(
                action_type="auto_tool",
                auto_tool="Read",
                auto_args={"file_path": file_path},
                hint=f"Could not find '{old_string}...' in file. Current content:"
            )
        
        elif attempt == 2:
            # 第二次：Grep 搜索类似内容
            return RecoveryAction(
                action_type="auto_tool",
                auto_tool="Grep",
                auto_args={"pattern": old_string[:20], "path": file_path},
                hint="Searching for similar content:"
            )
        
        else:
            return RecoveryAction(
                action_type="inject_hint",
                hint=(
                    "Edit failed. The file may have been modified already.\n"
                    "If your change is complete, call return_result."
                )
            )
```

### 4. 集成到 vCPU

```python
class VCPU:
    def __init__(self, ...):
        # Error handler registry
        self._error_handlers: List[ErrorHandler] = [
            FileNotFoundHandler(),
            PatternNoMatchHandler(),
            EditStringNotFoundHandler(),
            # ... 更多 handlers
        ]
        
        # 跟踪每个工具的失败次数
        self._tool_failure_counts: Dict[str, int] = defaultdict(int)
    
    async def _handle_tool_error(self, action, result) -> Optional[ToolResult]:
        error_code = self._classify_error(result.fault)
        tool_name = action.name
        
        # 增加失败计数
        call_signature = f"{tool_name}:{json.dumps(action.args, sort_keys=True)}"
        self._tool_failure_counts[call_signature] += 1
        attempt = self._tool_failure_counts[call_signature]
        
        # 找到合适的 handler
        for handler in self._error_handlers:
            if handler.can_handle(error_code, tool_name):
                recovery = await handler.handle(
                    error_code, tool_name, action.args,
                    result.fault.message, attempt
                )
                return await self._execute_recovery(recovery)
        
        return None  # 无法处理
    
    async def _execute_recovery(self, recovery: RecoveryAction) -> Optional[ToolResult]:
        if recovery.action_type == "skip":
            return None
        
        if recovery.action_type == "inject_hint":
            # 注入系统消息
            self.mmu.add_system_message(recovery.hint)
            return None
        
        if recovery.action_type == "auto_tool":
            # 自动执行恢复工具
            auto_action = ActionIR(
                type="TOOL_CALL",
                name=recovery.auto_tool,
                args=recovery.auto_args,
            )
            auto_result = await self.gate.syscall_tool(auto_action)
            
            # 组合提示和结果
            combined_output = f"{recovery.hint}\n\n{auto_result.output}"
            self.mmu.add_system_message(combined_output)
            
            return None  # 继续执行，不算成功
        
        if recovery.action_type == "modify_args":
            # 用修改后的参数重试
            # (需要重新执行工具)
            ...
```

### 5. 错误分类逻辑

```python
def _classify_error(self, fault: Fault) -> ToolErrorCode:
    """根据错误信息分类"""
    msg = fault.message.lower()
    
    if "not found" in msg or "no such file" in msg:
        if "directory" in msg:
            return ToolErrorCode.DIRECTORY_NOT_FOUND
        return ToolErrorCode.FILE_NOT_FOUND
    
    if "permission denied" in msg:
        return ToolErrorCode.PERMISSION_DENIED
    
    if "is a directory" in msg:
        return ToolErrorCode.IS_A_DIRECTORY
    
    if "no matches" in msg or "no match" in msg:
        return ToolErrorCode.PATTERN_NO_MATCH
    
    if "string not found" in msg or "could not find" in msg:
        return ToolErrorCode.STRING_NOT_FOUND
    
    return ToolErrorCode.REPEATED_FAILURE  # 默认
```

## 优势

1. **智能恢复**：不是简单报错，而是自动执行恢复动作
2. **渐进式**：第 1、2、3 次失败有不同的处理策略
3. **可扩展**：通过注册 handler 支持新的错误类型
4. **透明**：恢复动作对 LLM 可见（作为 system message）

## 实现优先级

1. **Phase 1**：`PatternNoMatchHandler` - 解决 Glob 无匹配问题
2. **Phase 2**：`FileNotFoundHandler` - 自动路径解析
3. **Phase 3**：`EditStringNotFoundHandler` - 编辑恢复
