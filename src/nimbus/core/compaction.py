"""
Nimbus v2 Compaction - Smart Context Compression

Compaction 模块负责：
1. 检测何时需要压缩（自动触发）
2. 使用 LLM 生成智能摘要
3. 保留重要信息，丢弃冗余内容

设计参考 Pi 的 compaction 模块，但简化以适应 Nimbus 的架构。

压缩策略：
1. Threshold-based: 当上下文达到阈值时触发
2. Overflow-based: 当 LLM 返回上下文溢出错误时触发
3. Manual: 用户手动触发

压缩算法：
1. 识别可压缩区域（旧消息）
2. 保留最近的 N 条消息
3. 使用 LLM 摘要旧消息
4. 用摘要替换旧消息
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

from nimbus.core.memory.context import Message

# =============================================================================
# Compaction Configuration
# =============================================================================


@dataclass
class CompactionConfig:
    """
    Compaction 配置。

    Attributes:
        enabled: 是否启用自动压缩
        threshold_ratio: 触发压缩的上下文使用率阈值 (0.0-1.0)
        keep_recent_messages: 保留最近的消息数量
        keep_recent_tokens: 保留最近消息的 token 数量（优先于 messages）
        min_messages_to_compact: 最少需要多少消息才触发压缩
        summary_max_tokens: 摘要的最大 token 数
    """

    enabled: bool = True
    threshold_ratio: float = 0.85  # 85% 时触发
    keep_recent_messages: int = 10
    keep_recent_tokens: int = 4000
    min_messages_to_compact: int = 5
    summary_max_tokens: int = 2000


@dataclass
class CompactionResult:
    """
    Compaction 结果。

    Attributes:
        summary: 生成的摘要
        tokens_before: 压缩前的 token 数
        tokens_after: 压缩后的 token 数
        messages_removed: 移除的消息数量
        first_kept_entry_id: 保留的第一个条目 ID（用于 session 追踪）
        details: 额外细节
    """

    summary: str
    tokens_before: int
    tokens_after: int
    messages_removed: int
    first_kept_entry_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def compression_ratio(self) -> float:
        """压缩率"""
        if self.tokens_before == 0:
            return 0.0
        return 1.0 - (self.tokens_after / self.tokens_before)


@dataclass
class CompactionPreparation:
    """
    Compaction 准备阶段的数据。

    分离准备和执行阶段，便于：
    1. 让用户/扩展检查将被压缩的内容
    2. 自定义压缩逻辑
    """

    messages_to_compact: List[Message]
    messages_to_keep: List[Message]
    tokens_to_compact: int
    tokens_to_keep: int
    first_kept_index: int
    first_kept_entry_id: Optional[str] = None


# =============================================================================
# LLM Protocol for Compaction
# =============================================================================


class CompactionLLM(Protocol):
    """用于 Compaction 的 LLM 接口"""

    async def summarize(
        self,
        messages: List[Dict[str, Any]],
        custom_instructions: Optional[str] = None,
    ) -> str:
        """
        生成消息摘要。

        Args:
            messages: 要摘要的消息列表（OpenAI 格式）
            custom_instructions: 自定义摘要指令

        Returns:
            摘要文本
        """
        ...


# =============================================================================
# Default LLM Summarizer
# =============================================================================


class DefaultCompactionLLM:
    """
    默认的 Compaction LLM 实现。

    使用通用 LLM 客户端生成摘要。
    """

    def __init__(self, llm_client: Any):
        """
        Args:
            llm_client: 实现 chat(messages, tools) 方法的 LLM 客户端
        """
        self._llm = llm_client

    async def summarize(
        self,
        messages: List[Dict[str, Any]],
        custom_instructions: Optional[str] = None,
    ) -> str:
        """生成消息摘要"""

        # 构建摘要 prompt
        conversation_text = self._format_messages(messages)

        prompt = f"""Summarize the following conversation, preserving:

1. **Key decisions and conclusions** - What was decided or discovered
2. **Important file paths and code changes** - Specific files modified or examined
3. **Current task status** - What's completed, what's pending
4. **Critical context** - Information needed for continuing the conversation

Keep the summary concise but complete. Use bullet points for clarity.

<conversation>
{conversation_text}
</conversation>

{f"Additional instructions: {custom_instructions}" if custom_instructions else ""}

Summary:"""

        # 调用 LLM
        response = await self._llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,  # 摘要不需要工具
        )

        return response.content or ""

    def _format_messages(self, messages: List[Dict[str, Any]]) -> str:
        """格式化消息为文本"""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "tool":
                name = msg.get("name", "tool")
                lines.append(f"[Tool: {name}]\n{content}")
            elif role == "assistant" and msg.get("tool_calls"):
                tool_calls = msg.get("tool_calls", [])
                calls_str = ", ".join(
                    tc.get("function", {}).get("name", "unknown") for tc in tool_calls
                )
                if content:
                    lines.append(f"[Assistant]\n{content}\n(Called tools: {calls_str})")
                else:
                    lines.append(f"[Assistant] (Called tools: {calls_str})")
            else:
                role_label = role.capitalize()
                if content:
                    lines.append(f"[{role_label}]\n{content}")

        return "\n\n".join(lines)


# =============================================================================
# Compaction Engine
# =============================================================================


class CompactionEngine:
    """
    Compaction 引擎 - 核心压缩逻辑。

    Example:
        engine = CompactionEngine(config, llm)

        # 检查是否需要压缩
        if engine.should_compact(messages, max_tokens):
            # 准备压缩
            prep = engine.prepare(messages)
            # 执行压缩
            result = await engine.execute(prep)
    """

    def __init__(
        self,
        config: Optional[CompactionConfig] = None,
        llm: Optional[CompactionLLM] = None,
    ):
        self.config = config or CompactionConfig()
        self._llm = llm

    def set_llm(self, llm: CompactionLLM) -> None:
        """设置用于摘要的 LLM"""
        self._llm = llm

    # =========================================================================
    # Detection
    # =========================================================================

    def should_compact(
        self,
        messages: List[Message],
        max_tokens: int,
    ) -> bool:
        """
        检查是否应该触发压缩。

        Args:
            messages: 当前消息列表
            max_tokens: 最大允许的 token 数

        Returns:
            是否应该压缩
        """
        if not self.config.enabled:
            return False

        if len(messages) < self.config.min_messages_to_compact:
            return False

        current_tokens = sum(m.token_estimate() for m in messages)
        threshold = int(max_tokens * self.config.threshold_ratio)

        return current_tokens > threshold

    def is_overflow_error(self, error_message: str) -> bool:
        """
        检查是否是上下文溢出错误。

        Args:
            error_message: 错误消息

        Returns:
            是否是上下文溢出
        """
        overflow_indicators = [
            "context length",
            "context window",
            "token limit",
            "maximum context",
            "too many tokens",
            "prompt is too long",
            "exceeds the model",
        ]
        error_lower = error_message.lower()
        return any(indicator in error_lower for indicator in overflow_indicators)

    # =========================================================================
    # Preparation
    # =========================================================================

    def prepare(
        self,
        messages: List[Message],
        keep_recent: Optional[int] = None,
    ) -> Optional[CompactionPreparation]:
        """
        准备压缩（不执行）。

        Args:
            messages: 消息列表
            keep_recent: 保留最近的消息数量（覆盖配置）

        Returns:
            压缩准备数据，如果不需要压缩则返回 None
        """
        keep_count = keep_recent or self.config.keep_recent_messages

        if len(messages) <= keep_count:
            return None

        # 分割消息
        split_index = len(messages) - keep_count
        messages_to_compact = messages[:split_index]
        messages_to_keep = messages[split_index:]

        # 检查是否有足够的消息可压缩
        if len(messages_to_compact) < self.config.min_messages_to_compact:
            return None

        # 计算 token
        tokens_to_compact = sum(m.token_estimate() for m in messages_to_compact)
        tokens_to_keep = sum(m.token_estimate() for m in messages_to_keep)

        # 获取第一个保留消息的 entry_id（如果有）
        first_kept_entry_id = None
        if messages_to_keep:
            first_kept_entry_id = messages_to_keep[0].meta.get("entry_id")

        return CompactionPreparation(
            messages_to_compact=messages_to_compact,
            messages_to_keep=messages_to_keep,
            tokens_to_compact=tokens_to_compact,
            tokens_to_keep=tokens_to_keep,
            first_kept_index=split_index,
            first_kept_entry_id=first_kept_entry_id,
        )

    # =========================================================================
    # Execution
    # =========================================================================

    async def execute(
        self,
        preparation: CompactionPreparation,
        custom_instructions: Optional[str] = None,
    ) -> CompactionResult:
        """
        执行压缩。

        Args:
            preparation: 压缩准备数据
            custom_instructions: 自定义摘要指令

        Returns:
            压缩结果
        """
        if not self._llm:
            raise RuntimeError("No LLM configured for compaction")

        # 转换消息格式
        messages_dicts = [m.to_dict() for m in preparation.messages_to_compact]

        # 生成摘要
        summary = await self._llm.summarize(messages_dicts, custom_instructions)

        # 计算结果
        summary_tokens = len(summary) // 4  # 粗略估计
        tokens_after = summary_tokens + preparation.tokens_to_keep

        return CompactionResult(
            summary=summary,
            tokens_before=preparation.tokens_to_compact + preparation.tokens_to_keep,
            tokens_after=tokens_after,
            messages_removed=len(preparation.messages_to_compact),
            first_kept_entry_id=preparation.first_kept_entry_id,
            details={
                "kept_messages": len(preparation.messages_to_keep),
                "summary_tokens": summary_tokens,
            },
        )

    async def compact(
        self,
        messages: List[Message],
        custom_instructions: Optional[str] = None,
    ) -> Tuple[List[Message], CompactionResult]:
        """
        一步完成压缩（准备 + 执行）。

        Args:
            messages: 消息列表
            custom_instructions: 自定义摘要指令

        Returns:
            (压缩后的消息列表, 压缩结果)
        """
        preparation = self.prepare(messages)
        if not preparation:
            # 不需要压缩
            return messages, CompactionResult(
                summary="",
                tokens_before=sum(m.token_estimate() for m in messages),
                tokens_after=sum(m.token_estimate() for m in messages),
                messages_removed=0,
            )

        result = await self.execute(preparation, custom_instructions)

        # 构建新的消息列表
        summary_message = Message(
            role="system",
            content=f"[Previous conversation summary]\n{result.summary}",
            meta={"is_compaction": True, "tokens_before": result.tokens_before},
        )

        new_messages = [summary_message] + preparation.messages_to_keep

        return new_messages, result


# =============================================================================
# Simple Rule-Based Compaction (No LLM)
# =============================================================================


class SimpleCompactionEngine(CompactionEngine):
    """
    简单的基于规则的压缩引擎（不使用 LLM）。

    用于：
    - 测试
    - 没有可用 LLM 时的降级方案
    - 快速压缩
    """

    def __init__(self, config: Optional[CompactionConfig] = None):
        super().__init__(config, llm=None)

    async def execute(
        self,
        preparation: CompactionPreparation,
        custom_instructions: Optional[str] = None,
    ) -> CompactionResult:
        """使用规则生成摘要"""

        # 简单规则：提取关键信息
        summary_parts = []

        # 统计
        user_count = 0
        assistant_count = 0
        tool_count = 0
        tool_names = set()

        for msg in preparation.messages_to_compact:
            if msg.role == "user":
                user_count += 1
            elif msg.role == "assistant":
                assistant_count += 1
            elif msg.role == "tool":
                tool_count += 1
                if msg.name:
                    tool_names.add(msg.name)

        summary_parts.append(
            f"Previous conversation: {user_count} user messages, "
            f"{assistant_count} assistant responses, {tool_count} tool calls"
        )

        if tool_names:
            summary_parts.append(f"Tools used: {', '.join(sorted(tool_names))}")

        # 提取最后一条用户消息作为上下文
        last_user_msg = None
        for msg in reversed(preparation.messages_to_compact):
            if msg.role == "user":
                last_user_msg = msg
                break

        if last_user_msg and isinstance(last_user_msg.content, str):
            content = last_user_msg.content[:200]
            summary_parts.append(f"Last topic: {content}...")

        summary = "\n".join(summary_parts)
        summary_tokens = len(summary) // 4

        return CompactionResult(
            summary=summary,
            tokens_before=preparation.tokens_to_compact + preparation.tokens_to_keep,
            tokens_after=summary_tokens + preparation.tokens_to_keep,
            messages_removed=len(preparation.messages_to_compact),
            first_kept_entry_id=preparation.first_kept_entry_id,
            details={"method": "simple_rules"},
        )


# =============================================================================
# Context Stack Aware Compaction (Deprecated - use MMU directly)
# =============================================================================


class ContextStackAwareCompaction:
    """
    Context Stack 感知的压缩。

    ⚠️ DEPRECATED: 此类的功能已合并到 MMU 中。
    保留此类仅为向后兼容，新代码应直接使用 MMU 的标记功能。

    职责边界（per expert review）：
    - MMU: 内存布局 + 消息管理 + tool call 标记
    - CompactionEngine: LLM 摘要 + 压缩执行
    """

    def __init__(self, config: Optional[CompactionConfig] = None):
        self.config = config or CompactionConfig()
        self._discardable_tool_calls: set = set()

    def mark_tool_call(
        self,
        tool_call_id: str,
        valuable: bool,
        reason: Optional[str] = None,
    ) -> None:
        """标记 tool call。valuable=False 表示 discard。"""
        if not valuable:
            self._discardable_tool_calls.add(tool_call_id)
        else:
            self._discardable_tool_calls.discard(tool_call_id)

    def filter_messages(self, messages: List[Message]) -> List[Message]:
        """过滤被标记为 discard 的消息。"""
        if not self._discardable_tool_calls:
            return messages

        filtered = []
        skip_ids = self._discardable_tool_calls

        for msg in messages:
            if msg.role == "assistant" and msg.tool_calls:
                filtered_calls = [tc for tc in msg.tool_calls if tc.get("id") not in skip_ids]
                if filtered_calls or msg.content:
                    filtered.append(
                        Message(
                            role=msg.role,
                            content=msg.content,
                            tool_calls=filtered_calls if filtered_calls else None,
                            meta=msg.meta,
                        )
                    )
            elif msg.role == "tool":
                if msg.tool_call_id not in skip_ids:
                    filtered.append(msg)
            else:
                filtered.append(msg)
        return filtered

    def clear_markers(self) -> None:
        """清除所有标记"""
        self._discardable_tool_calls.clear()

    def auto_detect_failed_tools(self, messages: List[Message]) -> int:
        """
        自动检测失败的 tool calls（结构化检测，避免误判）。
        """
        marked_count = 0
        for msg in messages:
            if msg.role != "tool" or not msg.tool_call_id:
                continue
            content = msg.content if isinstance(msg.content, str) else ""
            # 只检测明确的错误（per expert review）
            if content.startswith("[Error]") or content.startswith("Error:"):
                self.mark_tool_call(msg.tool_call_id, valuable=False, reason="error_prefix")
                marked_count += 1
            elif "Exception:" in content or "Traceback" in content:
                self.mark_tool_call(msg.tool_call_id, valuable=False, reason="exception")
                marked_count += 1
        return marked_count
