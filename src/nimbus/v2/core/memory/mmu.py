"""
Nimbus v2 Memory Management Unit (MMU)

The MMU is responsible for:
1. Managing the context window for LLM interactions
2. Maintaining the call stack (SUB_CALL / RETURN)
3. Assembling context from Pinned + Stack
4. Token budget management and compression
5. **Context Stack 提炼** - pop_frame 时智能提取有价值内容

Memory Layout:
┌─────────────────────────────────┐
│        Pinned Context           │  ← Always at top (immutable)
│  - System Rules                 │
│  - Workspace Info               │
│  - Capabilities                 │
├─────────────────────────────────┤
│        Root Frame               │  ← Main conversation
│  - messages[...]                │
├─────────────────────────────────┤
│        Sub Frame 1              │  ← First SUB_CALL
│  - goal: "explore codebase"     │
│  - messages[...]                │
├─────────────────────────────────┤
│        Sub Frame 2              │  ← Nested SUB_CALL
│  - goal: "find auth module"     │
│  - messages[...]                │
└─────────────────────────────────┘
         ↑ Current frame (top of stack)

Design Principles:
- Pinned context is NEVER compressed
- Each frame is isolated (has its own message history)
- Context is assembled bottom-up (current frame first)
- Token budget is enforced during assembly
- **NEW**: pop_frame 时自动提炼有价值内容，丢弃失败的探索

Context Stack 提炼策略:
1. 失败的 tool calls 被标记并过滤
2. 探索性调用（找错方向）被识别并过滤
3. 只有有价值的结论被合并到父 frame
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Set, Tuple

from nimbus.v2.core.memory.context import (
    PinnedContext,
    StackFrame,
    Message,
    create_root_frame,
    create_sub_frame,
)


# =============================================================================
# Tool Call Value Markers
# =============================================================================

ToolCallValue = Literal["valuable", "failed", "exploratory", "intermediate"]


@dataclass
class ToolCallMarker:
    """Tool call 的价值标记"""
    tool_call_id: str
    tool_name: str
    value: ToolCallValue
    reason: Optional[str] = None


@dataclass
class MMUConfig:
    """
    Configuration for MMU.

    Attributes:
        max_context_tokens: Maximum tokens for the entire context
        pinned_budget: Token budget for pinned context
        frame_budget: Token budget per frame
        compress_threshold: Trigger compression at this percentage
        keep_recent_messages: Number of recent messages to keep when compressing
        auto_extract_on_pop: 是否在 pop_frame 时自动提炼有价值内容
        auto_detect_failures: 是否自动检测失败的 tool calls
    """
    max_context_tokens: int = 16000
    pinned_budget: int = 2000
    frame_budget: int = 8000
    compress_threshold: float = 0.9  # Compress at 90% capacity
    keep_recent_messages: int = 10
    auto_extract_on_pop: bool = True  # Context Stack 提炼
    auto_detect_failures: bool = True  # 自动检测失败


class MMU:
    """
    Memory Management Unit.

    Manages the context window for a single process.
    Handles the call stack for SUB_CALL/RETURN operations.
    
    **Context Stack 提炼功能**:
    - 自动检测失败的 tool calls
    - pop_frame 时提炼有价值内容
    - 过滤无价值的探索性调用

    Example:
        mmu = MMU(config=MMUConfig())
        mmu.set_pinned(PinnedContext(system_rules="Be helpful"))

        # Add conversation
        mmu.add_user_message("Hello")
        mmu.add_assistant_message("Hi there!")

        # Subprocess call with Context Stack 提炼
        mmu.push_frame("explore codebase")
        mmu.add_user_message("Find the auth module")
        # ... subprocess work (some tools fail, some succeed) ...
        
        # pop_frame 自动提炼有价值内容
        result = mmu.pop_frame()  # 自动提取结论，丢弃失败的探索

        # Assemble context for LLM (已过滤无价值内容)
        messages = mmu.assemble_context()
    """

    def __init__(self, config: Optional[MMUConfig] = None, process_id: str = ""):
        """
        Initialize MMU.

        Args:
            config: MMU configuration
            process_id: Process ID for this MMU instance
        """
        self.config = config or MMUConfig()
        self.process_id = process_id

        # Pinned context (always at top)
        self._pinned: Optional[PinnedContext] = None

        # Call stack (root frame at index 0, current frame at end)
        self._stack: List[StackFrame] = []

        # Initialize with root frame
        self._stack.append(create_root_frame())
        
        # Context Stack 提炼：Tool call 价值标记
        self._tool_markers: Dict[str, ToolCallMarker] = {}
        
        # 每个 frame 的无价值 tool call IDs
        self._frame_discardable: Dict[str, Set[str]] = {}

    # =========================================================================
    # Pinned Context Management
    # =========================================================================

    def set_pinned(self, pinned: PinnedContext) -> None:
        """Set the pinned context."""
        self._pinned = pinned

    def get_pinned(self) -> Optional[PinnedContext]:
        """Get the pinned context."""
        return self._pinned

    def update_system_rules(self, rules: str) -> None:
        """Update system rules in pinned context."""
        if self._pinned is None:
            self._pinned = PinnedContext()
        self._pinned.system_rules = rules

    def update_workspace_info(self, info: str) -> None:
        """Update workspace info in pinned context."""
        if self._pinned is None:
            self._pinned = PinnedContext()
        self._pinned.workspace_info = info

    def update_capabilities(self, caps: str) -> None:
        """Update capabilities in pinned context."""
        if self._pinned is None:
            self._pinned = PinnedContext()
        self._pinned.capabilities = caps

    # =========================================================================
    # Stack Management (SUB_CALL / RETURN)
    # =========================================================================

    @property
    def current_frame(self) -> StackFrame:
        """Get the current (top) frame."""
        return self._stack[-1]

    @property
    def stack_depth(self) -> int:
        """Get the current stack depth."""
        return len(self._stack)

    @property
    def is_root_frame(self) -> bool:
        """Check if currently in root frame."""
        return len(self._stack) == 1

    def push_frame(self, goal: str, meta: Optional[Dict[str, Any]] = None) -> str:
        """
        Push a new frame onto the stack (SUB_CALL).

        Args:
            goal: Goal for the new frame
            meta: Additional metadata

        Returns:
            Frame ID of the new frame
        """
        parent_id = self.current_frame.frame_id
        new_frame = create_sub_frame(parent_id, goal)
        if meta:
            new_frame.meta.update(meta)

        # Suspend current frame
        self.current_frame.state = "SUSPENDED"

        # Push new frame
        self._stack.append(new_frame)

        return new_frame.frame_id

    def pop_frame(
        self,
        result: Any = None,
        extract_valuable: bool = True,
    ) -> Optional[Any]:
        """
        Pop the current frame (RETURN) with Context Stack 提炼.

        Args:
            result: Result to pass back to parent frame (if None, auto-extract)
            extract_valuable: 是否自动提取有价值内容（默认 True）

        Returns:
            The extracted/provided result, or None if at root frame
            
        Context Stack 提炼逻辑:
        1. 如果 result 为 None 且 extract_valuable=True，自动提取有价值内容
        2. 过滤被标记为 failed/exploratory 的 tool calls
        3. 只有结论和成功的操作被保留
        """
        if self.is_root_frame:
            # Can't pop root frame
            return None

        frame = self._stack.pop()
        frame_id = frame.frame_id
        
        # 如果没有提供 result，尝试自动提取
        if result is None and extract_valuable and self.config.auto_extract_on_pop:
            result = self._extract_valuable_content(frame)
        
        frame.complete(result)

        # Resume parent frame
        self.current_frame.state = "ACTIVE"

        # 只添加精炼后的结果到父 frame
        summary = self._format_frame_result(frame, result)
        self.current_frame.add_assistant_message(summary)
        
        # 清理该 frame 的标记
        if frame_id in self._frame_discardable:
            del self._frame_discardable[frame_id]
        
        # 清理相关的 tool markers
        markers_to_remove = [
            tc_id for tc_id, marker in self._tool_markers.items()
            if marker.tool_call_id in self._get_frame_tool_call_ids(frame)
        ]
        for tc_id in markers_to_remove:
            del self._tool_markers[tc_id]

        return result
    
    def _extract_valuable_content(self, frame: StackFrame) -> str:
        """
        从 frame 中提取有价值的内容。
        
        策略:
        1. 过滤失败的 tool calls
        2. 提取成功操作的结果
        3. 提取助手的结论性陈述
        """
        valuable_parts = []
        frame_id = frame.frame_id
        discardable = self._frame_discardable.get(frame_id, set())
        
        # 收集有价值的 tool results
        successful_tools = []
        for msg in frame.messages:
            if msg.role == "tool":
                tool_call_id = msg.tool_call_id
                # 检查是否被标记为无价值
                if tool_call_id and tool_call_id not in discardable:
                    content = msg.content if isinstance(msg.content, str) else ""
                    # 检查是否是错误
                    if not content.startswith("[Error]"):
                        successful_tools.append({
                            "name": msg.name,
                            "result": content[:200] if len(content) > 200 else content
                        })
        
        # 收集助手的结论
        last_assistant_content = None
        for msg in reversed(frame.messages):
            if msg.role == "assistant" and not msg.tool_calls:
                content = msg.content if isinstance(msg.content, str) else ""
                if content:
                    last_assistant_content = content
                    break
        
        # 构建结果
        if successful_tools:
            tool_summary = "; ".join(
                f"{t['name']}: {t['result'][:50]}..." 
                for t in successful_tools[:3]  # 最多 3 个
            )
            valuable_parts.append(f"Successful operations: {tool_summary}")
        
        if last_assistant_content:
            valuable_parts.append(f"Conclusion: {last_assistant_content[:300]}")
        
        if not valuable_parts:
            return f"Completed task: {frame.goal}"
        
        return " | ".join(valuable_parts)
    
    def _format_frame_result(self, frame: StackFrame, result: Any) -> str:
        """格式化 frame 结果为消息"""
        return f"[Subtask completed] {frame.goal}\nResult: {result}"
    
    def _get_frame_tool_call_ids(self, frame: StackFrame) -> Set[str]:
        """获取 frame 中所有的 tool call IDs"""
        ids = set()
        for msg in frame.messages:
            if msg.tool_call_id:
                ids.add(msg.tool_call_id)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    if isinstance(tc, dict) and "id" in tc:
                        ids.add(tc["id"])
        return ids

    def get_frame(self, frame_id: str) -> Optional[StackFrame]:
        """Get a frame by ID."""
        for frame in self._stack:
            if frame.frame_id == frame_id:
                return frame
        return None

    # =========================================================================
    # Message Management
    # =========================================================================

    def add_message(self, message: Message) -> None:
        """Add a message to the current frame."""
        self.current_frame.add_message(message)

    def add_user_message(self, content: str) -> None:
        """Add a user message to the current frame."""
        self.current_frame.add_user_message(content)

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to the current frame."""
        self.current_frame.add_assistant_message(content)

    def add_assistant_with_tool_calls(
        self, content: Optional[str], tool_calls: List[Dict[str, Any]]
    ) -> None:
        """Add an assistant message with tool calls to the current frame."""
        self.current_frame.add_assistant_with_tool_calls(content, tool_calls)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        """Add a tool result to the current frame."""
        self.current_frame.add_tool_result(tool_call_id, name, content)
        
        # 自动检测失败
        if self.config.auto_detect_failures:
            self._auto_detect_tool_failure(tool_call_id, name, content)
    
    # =========================================================================
    # Context Stack 提炼 - Tool Call 标记
    # =========================================================================
    
    def mark_tool_call(
        self,
        tool_call_id: str,
        value: ToolCallValue,
        reason: Optional[str] = None,
        tool_name: str = "",
    ) -> None:
        """
        标记 tool call 的价值。
        
        Args:
            tool_call_id: Tool call ID
            value: 价值标记 ("valuable", "failed", "exploratory", "intermediate")
            reason: 标记原因
            tool_name: 工具名称
        """
        marker = ToolCallMarker(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            value=value,
            reason=reason,
        )
        self._tool_markers[tool_call_id] = marker
        
        # 如果标记为无价值，添加到当前 frame 的 discardable 集合
        if value in ("failed", "exploratory", "intermediate"):
            frame_id = self.current_frame.frame_id
            if frame_id not in self._frame_discardable:
                self._frame_discardable[frame_id] = set()
            self._frame_discardable[frame_id].add(tool_call_id)
        else:
            # 如果标记为 valuable，从 discardable 移除
            frame_id = self.current_frame.frame_id
            if frame_id in self._frame_discardable:
                self._frame_discardable[frame_id].discard(tool_call_id)
    
    def mark_recent_tool_calls(
        self,
        value: ToolCallValue,
        count: int = 1,
        tool_names: Optional[List[str]] = None,
        reason: Optional[str] = None,
    ) -> int:
        """
        批量标记最近的 tool calls。
        
        Args:
            value: 价值标记
            count: 标记数量
            tool_names: 只标记这些工具（可选）
            reason: 标记原因
        
        Returns:
            实际标记的数量
        """
        marked = 0
        tool_results = []
        
        # 收集当前 frame 的 tool results
        for msg in reversed(self.current_frame.messages):
            if msg.role == "tool" and msg.tool_call_id:
                if tool_names is None or msg.name in tool_names:
                    tool_results.append((msg.tool_call_id, msg.name or ""))
                    if len(tool_results) >= count:
                        break
        
        # 标记
        for tool_call_id, tool_name in tool_results:
            self.mark_tool_call(tool_call_id, value, reason, tool_name)
            marked += 1
        
        return marked
    
    def get_tool_markers(self) -> Dict[str, ToolCallMarker]:
        """获取所有 tool call 标记"""
        return self._tool_markers.copy()
    
    def get_discardable_count(self) -> int:
        """获取当前 frame 中被标记为无价值的 tool call 数量"""
        frame_id = self.current_frame.frame_id
        return len(self._frame_discardable.get(frame_id, set()))
    
    def _auto_detect_tool_failure(
        self,
        tool_call_id: str,
        tool_name: str,
        content: str,
    ) -> bool:
        """
        自动检测 tool call 是否失败。
        
        Returns:
            是否检测到失败
        """
        # 检测明确的错误
        if content.startswith("[Error]"):
            self.mark_tool_call(tool_call_id, "failed", "error_prefix", tool_name)
            return True
        
        # 检测常见失败模式
        content_lower = content.lower()
        failure_indicators = [
            "not found",
            "no such file",
            "permission denied",
            "failed to",
            "error:",
            "does not exist",
            "cannot find",
            "no matches",
        ]
        
        for indicator in failure_indicators:
            if indicator in content_lower:
                # 对于某些情况，可能是正常的（如 grep 没找到）
                # 只有在内容很短时才判定为失败
                if len(content) < 200:
                    self.mark_tool_call(tool_call_id, "failed", f"detected_{indicator}", tool_name)
                    return True
        
        return False
    
    def clear_markers(self) -> None:
        """清除所有标记"""
        self._tool_markers.clear()
        self._frame_discardable.clear()

    # =========================================================================
    # Context Assembly
    # =========================================================================

    def assemble_context(
        self,
        max_tokens: Optional[int] = None,
        filter_discardable: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Assemble the full context for LLM.

        This method combines:
        1. Pinned context (system message)
        2. Stack frames (from root to current)
        3. **Context Stack 提炼**: 过滤无价值的 tool calls

        The result is a list of messages ready for LLM API.

        Args:
            max_tokens: Optional token limit (uses config default if not specified)
            filter_discardable: 是否过滤被标记为无价值的 tool calls

        Returns:
            List of message dicts for LLM API
        """
        max_tokens = max_tokens or self.config.max_context_tokens
        messages: List[Dict[str, Any]] = []
        token_count = 0

        # 1. Add pinned context (always first)
        if self._pinned:
            pinned_msg = self._pinned.to_system_message()
            pinned_tokens = self._pinned.token_estimate()

            if pinned_tokens <= self.config.pinned_budget:
                messages.append(pinned_msg.to_dict())
                token_count += pinned_tokens

        # 2. Add stack frames (from root to current)
        # We need to be smart about token budget here
        remaining_budget = max_tokens - token_count

        # Collect all frame messages
        all_frame_messages: List[Message] = []
        for frame in self._stack:
            frame_messages = frame.to_context_messages()
            all_frame_messages.extend(frame_messages)

        # 3. Context Stack 提炼：过滤无价值的 tool calls
        if filter_discardable:
            all_frame_messages = self._filter_discardable_messages(all_frame_messages)

        # Estimate tokens
        frame_tokens = sum(msg.token_estimate() for msg in all_frame_messages)

        # If within budget, include all
        if frame_tokens <= remaining_budget:
            for msg in all_frame_messages:
                messages.append(msg.to_dict())
        else:
            # Need to compress - keep recent messages from current frame
            # and summaries from parent frames
            messages.extend(self._compress_frames(remaining_budget))

        return messages
    
    def _filter_discardable_messages(
        self,
        messages: List[Message],
    ) -> List[Message]:
        """
        过滤被标记为无价值的消息。
        
        策略:
        1. 移除被标记的 tool result 消息
        2. 从 assistant 消息中移除对应的 tool_calls
        """
        # 收集所有需要移除的 tool call IDs
        discardable_ids: Set[str] = set()
        for frame_discardable in self._frame_discardable.values():
            discardable_ids.update(frame_discardable)
        
        if not discardable_ids:
            return messages
        
        filtered: List[Message] = []
        
        for msg in messages:
            if msg.role == "tool":
                # 跳过被标记的 tool results
                if msg.tool_call_id and msg.tool_call_id in discardable_ids:
                    continue
                filtered.append(msg)
            elif msg.role == "assistant" and msg.tool_calls:
                # 过滤 assistant 消息中的 tool_calls
                filtered_calls = [
                    tc for tc in msg.tool_calls
                    if tc.get("id") not in discardable_ids
                ]
                
                # 如果还有 tool_calls 或有 content，保留消息
                if filtered_calls or msg.content:
                    filtered.append(Message(
                        role=msg.role,
                        content=msg.content,
                        tool_calls=filtered_calls if filtered_calls else None,
                        meta=msg.meta,
                    ))
            else:
                filtered.append(msg)
        
        return filtered

    def _compress_frames(self, budget: int) -> List[Dict[str, Any]]:
        """
        Compress frames to fit within budget.

        Strategy:
        1. Keep all messages from current frame (up to keep_recent_messages)
        2. Summarize parent frames

        Args:
            budget: Token budget for frames

        Returns:
            Compressed message list
        """
        result: List[Dict[str, Any]] = []
        keep_recent = self.config.keep_recent_messages

        # Process frames from root to current
        for i, frame in enumerate(self._stack):
            is_current = (i == len(self._stack) - 1)

            if is_current:
                # Keep recent messages from current frame
                messages = frame.to_context_messages()
                if len(messages) > keep_recent:
                    # Add summary of older messages
                    older = messages[:-keep_recent]
                    summary = self._summarize_messages(older)
                    result.append(Message(
                        role="system",
                        content=f"[Earlier conversation summary]\n{summary}",
                        meta={"compressed": True}
                    ).to_dict())
                    # Add recent messages
                    for msg in messages[-keep_recent:]:
                        result.append(msg.to_dict())
                else:
                    for msg in messages:
                        result.append(msg.to_dict())
            else:
                # Summarize parent frames
                summary = f"[Frame: {frame.goal}] (completed)" if frame.state == "COMPLETED" else f"[Frame: {frame.goal}] (suspended)"
                result.append(Message(
                    role="system",
                    content=summary,
                    meta={"compressed": True, "frame_id": frame.frame_id}
                ).to_dict())

        return result

    def _summarize_messages(self, messages: List[Message]) -> str:
        """
        Create a simple summary of messages.

        This is a placeholder - in production, you might use
        an LLM to create a proper summary.
        """
        if not messages:
            return "(no earlier messages)"

        parts = []
        for msg in messages:
            role = msg.role
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            # Truncate long content
            if len(content) > 100:
                content = content[:100] + "..."
            parts.append(f"- [{role}] {content}")

        return "\n".join(parts[:5])  # Keep at most 5 summary items

    # =========================================================================
    # Token Management
    # =========================================================================

    def estimate_tokens(self) -> int:
        """Estimate total tokens in current context."""
        total = 0

        if self._pinned:
            total += self._pinned.token_estimate()

        for frame in self._stack:
            total += frame.token_estimate()

        return total

    def needs_compression(self) -> bool:
        """Check if context needs compression."""
        current = self.estimate_tokens()
        threshold = int(self.config.max_context_tokens * self.config.compress_threshold)
        return current > threshold

    # =========================================================================
    # State Management
    # =========================================================================

    def get_state(self) -> Dict[str, Any]:
        """Get MMU state for checkpointing."""
        return {
            "process_id": self.process_id,
            "pinned": self._pinned.__dict__ if self._pinned else None,
            "stack_depth": len(self._stack),
            "current_frame_id": self.current_frame.frame_id,
            "total_messages": sum(len(f.messages) for f in self._stack),
            "estimated_tokens": self.estimate_tokens(),
        }

    def clear(self) -> None:
        """Clear all state and reset to initial."""
        self._pinned = None
        self._stack = [create_root_frame()]
