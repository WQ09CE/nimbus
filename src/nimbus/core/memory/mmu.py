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

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set, Callable, Awaitable

from nimbus.core.memory.context import (
    Message,
    PinnedContext,
    StackFrame,
    create_root_frame,
)
from nimbus.core.persistence import (
    MemorySnapshotModel,
    PinnedContextModel,
    StackFrameModel,
    MessageModel,
)

# =============================================================================
# Tool Call Value Markers (Simplified: keep/discard binary decision)
# =============================================================================

# Simplified from 4-level (valuable/failed/exploratory/intermediate) to 2-level
# per expert committee recommendation - only 'failed' was actually auto-used
ToolCallValue = Literal["keep", "discard"]


@dataclass
class ToolCallMarker:
    """Tool call 的价值标记（简化版：二元决策）"""

    tool_call_id: str
    tool_name: str
    value: ToolCallValue  # "keep" or "discard"
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
        auto_compact: 是否自动压缩上下文（建议关闭，保护 LLM 上下文完整性）
        remove_failed_tool_calls: 是否移除失败的 tool calls（释放 token 空间）
    """

    max_context_tokens: int = 4000  # STRESS TEST MODE: 4k tokens
    pinned_budget: int = 1000  # Reduced pinned budget
    frame_budget: int = 2500  # Reduced frame budget
    compress_threshold: float = 0.9  # Compress at 90% capacity
    keep_recent_messages: int = 10
    auto_extract_on_pop: bool = True  # Context Stack 提炼
    auto_detect_failures: bool = True  # 自动检测失败
    auto_compact: bool = False  # 关闭自动压缩，保护 LLM 上下文
    remove_failed_tool_calls: bool = True  # 移除失败的 tool calls


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

    def update_env_state(self, state: str) -> None:
        """Update environment state in pinned context."""
        if self._pinned is None:
            self._pinned = PinnedContext()
        self._pinned.update_env_state(state)

    def update_capabilities(self, caps: str) -> None:
        """Update capabilities in pinned context."""
        if self._pinned is None:
            self._pinned = PinnedContext()
        self._pinned.capabilities = caps

    def pin_user_goal(self, goal: str) -> None:
        """
        Pin the user's current goal to the top of context.
        
        This ensures the goal is NEVER lost during compaction.
        The goal is stored as a special anchor that gets replaced
        (not accumulated) on each new execute() call.
        
        Args:
            goal: The user's goal/request text
        """
        if self._pinned is None:
            self._pinned = PinnedContext()

        # Debug logging
        from nimbus.core.logging import get_logger
        logger = get_logger("memory.mmu")
        logger.debug(f"Pinning user goal: {goal[:50]}...")
        
        # Log existing anchors
        for i, anchor in enumerate(self._pinned.custom_anchors):
            logger.debug(f"Existing anchor [{i}]: {anchor[:50]}...")

        # Remove any existing goal anchor (identified by prefix)
        goal_prefix = "# Current Goal\n"
        original_count = len(self._pinned.custom_anchors)
        self._pinned.custom_anchors = [
            a for a in self._pinned.custom_anchors if not a.startswith(goal_prefix)
        ]
        removed = original_count - len(self._pinned.custom_anchors)
        if removed > 0:
            logger.debug(f"Removed {removed} old goal anchor(s)")
        else:
            logger.warning(f"No anchors removed! Prefix '{goal_prefix}' not found.")

        # Add new goal anchor
        self._pinned.custom_anchors.append(f"{goal_prefix}{goal}")

    # =========================================================================
    # Stack Management (Simplified: Single Frame)
    # =========================================================================

    @property
    def current_frame(self) -> StackFrame:
        """Get the current (top) frame."""
        return self._stack[-1]

    @property
    def stack_depth(self) -> int:
        """Get the current stack depth (always 1 in flattened mode)."""
        return 1

    @property
    def is_root_frame(self) -> bool:
        """Check if currently in root frame (always True)."""
        return True

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

    def add_system_message(self, content: str) -> None:
        """Add a system message to the current frame (for hints/instructions)."""
        self.current_frame.add_message(Message(role="system", content=content))

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
        标记 tool call 的价值（简化版：二元决策）。

        Args:
            tool_call_id: Tool call ID
            value: 价值标记 ("keep" or "discard")
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

        frame_id = self.current_frame.frame_id
        if value == "discard":
            # 添加到当前 frame 的 discardable 集合 (Legacy support)
            if frame_id not in self._frame_discardable:
                self._frame_discardable[frame_id] = set()
            self._frame_discardable[frame_id].add(tool_call_id)
        else:
            # 如果标记为 keep，从 discardable 移除
            if frame_id in self._frame_discardable:
                self._frame_discardable[frame_id].discard(tool_call_id)

        # 核心修复：直接标记 Message 对象，解决 ID 冲突问题
        # 从最新的 frame 开始向前搜索
        found = False
        for frame in reversed(self._stack):
            messages = frame.messages
            for i in range(len(messages) - 1, -1, -1):
                msg = messages[i]
                if msg.role == "tool" and msg.tool_call_id == tool_call_id:
                    # 找到了 tool result
                    found = True
                    if value == "discard":
                        msg.meta["discard"] = True

                        # 向前搜索对应的 assistant message
                        for j in range(i - 1, -1, -1):
                            prev = messages[j]
                            if prev.role == "assistant" and prev.tool_calls:
                                for tc in prev.tool_calls:
                                    if tc.get("id") == tool_call_id:
                                        # 标记 assistant message 中的 tool call
                                        if "discard_tool_calls" not in prev.meta:
                                            prev.meta["discard_tool_calls"] = []
                                        if tool_call_id not in prev.meta["discard_tool_calls"]:
                                            prev.meta["discard_tool_calls"].append(tool_call_id)
                                        break
                                else:
                                    continue
                                break
                    else:
                        msg.meta.pop("discard", None)
                        # 注意：暂不支持从 assistant message 中移除 discard 标记（复杂且少见）

                    break  # 只处理最新的一个匹配项

            if found:
                break

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
            value: 价值标记 ("keep" or "discard")
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

        设计原则 (v2 Refactor):
        - Explicit > Implicit: 只检测明确的错误标记，不进行内容猜测。
        - 依赖 vCPU/Tool 层的异常捕获产生的明确错误前缀。
        """
        # 1. 明确的错误前缀（由 vCPU 或工具层添加）
        # 这是最可靠的信号：vCPU 捕获异常后会添加 [Error]
        if content.startswith("[Error]") or content.startswith("Error:"):
            self.mark_tool_call(tool_call_id, "discard", "error_prefix", tool_name)
            return True

        # 2. 结构化异常检测
        # 检测 Python Traceback，这是明确的执行失败
        if "Traceback (most recent call last):" in content:
            self.mark_tool_call(tool_call_id, "discard", "exception", tool_name)
            return True

        # 移除所有基于关键词（如 ENOENT, No such file）的启发式检测。
        # 理由：代码文件本身可能包含这些字符串，导致误判。
        # 真正的"文件未找到"会由工具抛出 FileNotFoundError，被 vCPU 捕获并添加 [Error] 前缀。

        return False

    def clear_markers(self) -> None:
        """清除所有标记"""
        self._tool_markers.clear()
        self._frame_discardable.clear()

    def rollback_incomplete_turn(self) -> int:
        """
        回滚未完成的对话轮次。

        当用户中断任务时，需要移除：
        1. 最后一个 assistant 消息（如果有未完成的 tool calls）
        2. 对应的 tool result 消息

        这样下一个用户消息不会和未完成的状态混在一起。

        Returns:
            移除的消息数量
        """
        if not self._stack:
            return 0

        frame = self.current_frame
        messages = frame._messages

        if not messages:
            return 0

        removed = 0

        # 从后往前找，移除未完成的 turn
        # 一个完整的 turn: user → assistant (with tool_calls) → tool results → assistant (final)
        # 未完成的 turn: user → assistant (with tool_calls) → tool results (可能不完整)

        while messages:
            last_msg = messages[-1]

            # 如果最后是 user 消息，说明没有未完成的 turn
            if last_msg.role == "user":
                break

            # 如果是 tool 消息，移除它
            if last_msg.role == "tool":
                messages.pop()
                removed += 1
                continue

            # 如果是 assistant 消息
            if last_msg.role == "assistant":
                # 如果有 tool_calls 但没有对应的 tool results，移除这个 assistant 消息
                if last_msg.tool_calls:
                    messages.pop()
                    removed += 1
                    continue
                # 如果是纯文本 assistant 消息，停止回滚
                break

            # 其他情况，停止
            break

        if removed > 0:
            from nimbus.core.logging import get_logger

            logger = get_logger("memory.mmu")
            logger.info(f"🔙 Rolled back {removed} incomplete messages")

        return removed

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

        # 3. Context Stack 提炼：Lazy Compaction 策略
        # 默认保留所有历史（包括失败尝试），让 LLM 从错误中学习。
        # 只有在 Token 预算紧张触发压缩时，才考虑过滤无价值消息。

        # Estimate tokens
        frame_tokens = sum(msg.token_estimate() for msg in all_frame_messages)

        # Debug: log token usage
        from nimbus.core.logging import get_logger

        logger = get_logger("memory.mmu")
        logger.debug(
            f"📊 Token budget: max={max_tokens}, pinned={token_count}, "
            f"remaining={remaining_budget}, frame_messages={len(all_frame_messages)}, "
            f"frame_tokens={frame_tokens}"
        )

        # If within budget, include all
        if frame_tokens <= remaining_budget:
            for msg in all_frame_messages:
                messages.append(msg.to_dict())
        else:
            # Over budget - Apply compaction strategies

            # Strategy 1: First try filtering discardable messages (failed tools)
            if self.config.remove_failed_tool_calls and filter_discardable:
                logger.info("🧹 Compaction Level 1: Removing failed tool calls")
                filtered_messages = self._filter_discardable_messages(all_frame_messages)
                filtered_tokens = sum(msg.token_estimate() for msg in filtered_messages)

                if filtered_tokens <= remaining_budget:
                    for msg in filtered_messages:
                        messages.append(msg.to_dict())
                    return messages

                # If still over budget, use filtered messages for next step
                all_frame_messages = filtered_messages
                frame_tokens = filtered_tokens

            # Strategy 2: Compress frames (summarize older frames)
            if self.config.auto_compact:
                logger.warning(
                    f"🗜️ Compaction Level 2: Summarizing frames. {frame_tokens} tokens > {remaining_budget} budget."
                )
                messages.extend(self._compress_frames(remaining_budget))
            else:
                # Auto-compact disabled: keep all messages
                logger.warning(
                    f"⚠️ Context exceeds budget: {frame_tokens} tokens > {remaining_budget} budget. "
                    f"Auto-compact disabled, keeping full context for LLM."
                )
                for msg in all_frame_messages:
                    messages.append(msg.to_dict())

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
        # 注意：不再依赖 discardable_ids 集合过滤，因为会导致 ID 冲突误删
        # 改用 Message.meta 标记进行精确过滤

        filtered: List[Message] = []

        for msg in messages:
            if msg.role == "tool":
                # 跳过被标记的 tool results
                if msg.meta.get("discard"):
                    continue
                filtered.append(msg)
            elif msg.role == "assistant" and msg.tool_calls:
                discard_list = msg.meta.get("discard_tool_calls", [])

                if discard_list:
                    # 过滤 assistant 消息中的 tool_calls
                    filtered_calls = [
                        tc for tc in msg.tool_calls if tc.get("id") not in discard_list
                    ]

                    # 如果还有 tool_calls 或有 content，保留消息
                    if filtered_calls or msg.content:
                        # 创建新消息，保留 meta
                        filtered.append(
                            Message(
                                role=msg.role,
                                content=msg.content,
                                tool_calls=filtered_calls if filtered_calls else None,
                                meta=msg.meta,
                            )
                        )
                else:
                    filtered.append(msg)
            else:
                filtered.append(msg)

        return filtered

    async def archive_and_reset(
        self,
        session_id: str,
        summarizer: Optional[Callable[[List[Message]], Awaitable[str]]] = None,
    ) -> Optional[str]:
        """
        Archive current frame context to file and reset it.

        This implements the "Hybrid Memory Architecture" strategy.
        When context is full:
        1. Generate an Execution Summary (using current model)
        2. Write current messages to a file
        3. Create a summary pointer
        4. Clear current frame messages
        5. Insert pointer + summary as system messages

        Args:
            session_id: Session ID for file organization
            summarizer: Async callback to generate summary from messages

        Returns:
            Path to archive file if successful
        """
        from nimbus.core.logging import get_logger

        logger = get_logger("memory.mmu")

        if not self._stack:
            return None

        frame = self._stack[-1]
        messages = frame.messages

        if len(messages) == 0:
            return None

        # 1. Generate Summary (Rolling Summary)
        summary_text = ""
        if summarizer:
            try:
                logger.info("🧠 Generating rolling summary for archive...")
                # Pass recent messages to summarizer
                summary_text = await summarizer(messages)
            except Exception as e:
                logger.error(f"Failed to generate summary: {e}")
                summary_text = "Summary generation failed."

        # 2. Prepare archive path
        # Use user home directory: ~/.nimbus/sessions/{session_id}/archive/
        home = Path.home()
        archive_dir = home / ".nimbus" / "sessions" / session_id / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"part_{timestamp}_{frame.frame_id[:8]}.md"
        file_path = archive_dir / filename

        # 3. Generate content
        content = f"# Archive: {timestamp}\n\n"
        content += f"## Goal: {frame.goal or 'Root'}\n\n"
        if summary_text:
            content += f"## Execution Summary\n{summary_text}\n\n"

        for msg in messages:
            role = msg.role.upper()
            text = str(msg.content) if msg.content else ""
            if msg.tool_calls:
                text += f"\n[Tool Calls: {len(msg.tool_calls)}]"
                for tc in msg.tool_calls:
                    text += f"\n- {tc.get('function', {}).get('name', 'Unknown')}"

            content += f"### {role}\n{text}\n\n"

        # 4. Write to file
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"🗄️ Archived {len(messages)} messages to {file_path}")
        except Exception as e:
            logger.error(f"Failed to archive memory: {e}")
            return None

        # 5. Reset Frame
        new_messages = []

        # Create a pointer message
        pointer_msg = Message(
            role="system",
            content=(
                f"⚠️ [MEMORY ARCHIVED]\n"
                f"Previous conversation history ({len(messages)} messages) has been archived to release memory.\n"
                f"Archive Location: {file_path}\n"
                f"Use `Read` tool if you need to check specific details from history."
            ),
            meta={"archived": True, "path": str(file_path)},
        )
        new_messages.append(pointer_msg)

        # Create summary message if available
        if summary_text:
            summary_msg = Message(
                role="system",
                content=f"## 📝 Execution Summary (Previous Context)\n{summary_text}",
                meta={"summary": True},
            )
            new_messages.append(summary_msg)

        # Clear and set new messages
        frame.messages = new_messages

        # Also clear discardable markers for this frame as they refer to cleared messages
        if frame.frame_id in self._frame_discardable:
            del self._frame_discardable[frame.frame_id]

        return str(file_path)

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
            is_current = i == len(self._stack) - 1

            if is_current:
                # Keep recent messages from current frame
                messages = frame.to_context_messages()
                if len(messages) > keep_recent:
                    # Add summary of older messages
                    older = messages[:-keep_recent]
                    summary = self._summarize_messages(older)
                    result.append(
                        Message(
                            role="system",
                            content=f"[Earlier conversation summary]\n{summary}",
                            meta={"compressed": True},
                        ).to_dict()
                    )
                    # Add recent messages
                    for msg in messages[-keep_recent:]:
                        result.append(msg.to_dict())
                else:
                    for msg in messages:
                        result.append(msg.to_dict())
            else:
                # Summarize parent frames
                summary = (
                    f"[Frame: {frame.goal}] (completed)"
                    if frame.state == "COMPLETED"
                    else f"[Frame: {frame.goal}] (suspended)"
                )
                result.append(
                    Message(
                        role="system",
                        content=summary,
                        meta={"compressed": True, "frame_id": frame.frame_id},
                    ).to_dict()
                )

        return result

    def _summarize_messages(self, messages: List[Message]) -> str:
        """
        Create a simple summary of messages.

        This is a placeholder - in production, you might use
        an LLM to create a proper summary.

        IMPORTANT: Preserves user's language context to ensure LLM responds
        in the same language after compaction.
        """
        if not messages:
            return "(no earlier messages)"

        # Detect user's language from their messages
        user_language = self._detect_user_language(messages)

        parts = []
        for msg in messages:
            role = msg.role
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            # Truncate long content
            if len(content) > 100:
                content = content[:100] + "..."
            parts.append(f"- [{role}] {content}")

        summary = "\n".join(parts[:5])  # Keep at most 5 summary items

        # Add language context hint
        if user_language == "zh":
            return f"[用户使用中文交流，请用中文回复]\n{summary}"
        elif user_language == "ja":
            return f"[ユーザーは日本語で交流しています。日本語で返信してください]\n{summary}"
        elif user_language == "ko":
            return f"[사용자가 한국어로 대화하고 있습니다. 한국어로 답변해주세요]\n{summary}"
        else:
            return summary

    def _detect_user_language(self, messages: List[Message]) -> str:
        """
        Detect the primary language used by the user.

        Returns: 'zh' for Chinese, 'ja' for Japanese, 'ko' for Korean, 'en' for others
        """
        import re

        user_text = ""
        for msg in messages:
            if msg.role == "user":
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                user_text += content

        if not user_text:
            return "en"

        # Count character types
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", user_text))
        japanese_chars = len(
            re.findall(r"[\u3040-\u309f\u30a0-\u30ff]", user_text)
        )  # Hiragana + Katakana
        korean_chars = len(re.findall(r"[\uac00-\ud7af]", user_text))
        total_chars = len(user_text)

        if total_chars == 0:
            return "en"

        # If more than 10% of characters are CJK, detect as that language
        if chinese_chars / total_chars > 0.1:
            return "zh"
        if japanese_chars / total_chars > 0.05:
            return "ja"
        if korean_chars / total_chars > 0.1:
            return "ko"

        return "en"

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

    def create_snapshot(self) -> MemorySnapshotModel:
        """Create a JSON-serializable snapshot of the MMU state."""
        # Convert PinnedContext
        pinned_model = None
        if self._pinned:
            pinned_model = PinnedContextModel(
                system_rules=self._pinned.system_rules,
                workspace_info=self._pinned.workspace_info,
                env_state=self._pinned.env_state,
                capabilities=self._pinned.capabilities,
                custom_anchors=self._pinned.custom_anchors,
                version=self._pinned.version,
            )

        # Convert Stack
        stack_models = []
        for frame in self._stack:
            msg_models = []
            for msg in frame.messages:
                msg_models.append(
                    MessageModel(
                        role=msg.role,
                        content=msg.content,
                        name=msg.name,
                        tool_call_id=msg.tool_call_id,
                        tool_calls=msg.tool_calls,
                        meta=msg.meta,
                    )
                )

            stack_models.append(
                StackFrameModel(
                    frame_id=frame.frame_id,
                    goal=frame.goal,
                    messages=msg_models,
                    state=frame.state,
                    parent_frame_id=frame.parent_frame_id,
                    result=frame.result,
                    created_at=frame.created_at,
                    meta=frame.meta,
                )
            )

        # Convert discardable (set -> list)
        frame_discardable_list = {k: list(v) for k, v in self._frame_discardable.items()}

        # Convert tool markers (ToolCallMarker objects -> dicts/models)
        # Note: ToolCallMarker is a dataclass, need to convert or ensure it's handled
        # For now assuming we can store as dict or rely on pydantic if registered
        # But wait, ToolCallMarker is a dataclass in mmu.py. Pydantic handles dataclasses,
        # but better to be explicit if we want pure JSON.
        # Let's convert ToolCallMarker to dict explicitly.
        tool_markers_dict = {}
        for k, v in self._tool_markers.items():
            if hasattr(v, "__dict__"):
                tool_markers_dict[k] = v.__dict__
            else:
                tool_markers_dict[k] = v

        return MemorySnapshotModel(
            process_id=self.process_id,
            pinned_context=pinned_model,
            stack=stack_models,
            tool_markers=tool_markers_dict,
            frame_discardable=frame_discardable_list,
        )

    def restore_from_snapshot(self, snapshot: MemorySnapshotModel) -> None:
        """Restore MMU state from a snapshot."""
        self.process_id = snapshot.process_id

        # Restore Pinned
        if snapshot.pinned_context:
            self._pinned = PinnedContext(
                system_rules=snapshot.pinned_context.system_rules,
                workspace_info=snapshot.pinned_context.workspace_info,
                env_state=getattr(snapshot.pinned_context, "env_state", ""),  # Backward compatibility
                capabilities=snapshot.pinned_context.capabilities,
                custom_anchors=snapshot.pinned_context.custom_anchors,
                version=snapshot.pinned_context.version,
            )
        else:
            self._pinned = None

        # Restore Stack
        self._stack = []
        for frame_model in snapshot.stack:
            messages = []
            for msg_model in frame_model.messages:
                messages.append(
                    Message(
                        role=msg_model.role,
                        content=msg_model.content,
                        name=msg_model.name,
                        tool_call_id=msg_model.tool_call_id,
                        tool_calls=msg_model.tool_calls,
                        meta=msg_model.meta,
                    )
                )

            frame = StackFrame(
                frame_id=frame_model.frame_id,
                goal=frame_model.goal,
                messages=messages,
                state=frame_model.state,
                parent_frame_id=frame_model.parent_frame_id,
                result=frame_model.result,
                created_at=frame_model.created_at,
                meta=frame_model.meta,
            )
            self._stack.append(frame)

        # Restore markers
        # Reconstruct ToolCallMarker objects
        self._tool_markers = {}
        for k, v in snapshot.tool_markers.items():
            if isinstance(v, dict):
                self._tool_markers[k] = ToolCallMarker(**v)
            else:
                self._tool_markers[k] = v

        self._frame_discardable = {k: set(v) for k, v in snapshot.frame_discardable.items()}

    def clear(self) -> None:
        """Clear all state and reset to initial."""
        self._pinned = None
        self._stack = [create_root_frame()]
