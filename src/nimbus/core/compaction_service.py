"""
CompactionService - Context compaction strategy and execution.

Extracts compaction logic from AgentOS into a dedicated service.
Manages three compaction scenarios:
1. Manual compact (user-triggered via API)
2. Proactive auto-compaction (check_compaction)
3. Process-level compaction (compact_process)

The low-level CompactionEngine (in compaction.py) handles message splitting
and LLM-based summarization.  This service sits above it, coordinating with
MMU, SessionManager, and event emission.
"""

from typing import Any, Callable, Coroutine, Dict, Optional

from loguru import logger

from nimbus.core.compaction import CompactionEngine
from nimbus.core.memory.mmu import MMU

# ---------------------------------------------------------------------------
# Type alias for the event emitter callback injected by AgentOS
# ---------------------------------------------------------------------------
EmitEventFn = Callable[[str, str, Dict[str, Any]], None]


class CompactionService:
    """High-level compaction service extracted from AgentOS."""

    def __init__(
        self,
        llm,
        config,  # AgentOSConfig
        compaction_engine: CompactionEngine,
        emit_event_fn: EmitEventFn,
        session_mgr=None,
    ):
        self._llm = llm
        self._config = config
        self._compaction_engine = compaction_engine
        self._emit_event = emit_event_fn
        self._session_mgr = session_mgr

    # =========================================================================
    # 1. Manual compact (replaces AgentOS.compact body)
    # =========================================================================

    async def compact(
        self,
        process,
        custom_instructions: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Manual compaction triggered via API.

        Args:
            process: The Process object (must have a non-None .mmu).
            custom_instructions: Optional custom instructions for the summarizer.

        Returns:
            Dict with compaction stats, or None if nothing to compact.
        """
        if not process or not process.mmu:
            return None

        all_messages = []
        for frame in process.mmu._stack:
            for msg in frame.messages:
                all_messages.append(msg)

        new_messages, result = await self._compaction_engine.compact(
            all_messages, custom_instructions
        )

        if result.messages_removed > 0:
            process.mmu.clear()
            for msg in new_messages:
                process.mmu.add_message(msg)

            if self._session_mgr:
                self._session_mgr.append_compaction(
                    summary=result.summary,
                    first_kept_entry_id=result.first_kept_entry_id or "",
                    tokens_before=result.tokens_before,
                    details=result.details,
                )

            self._emit_event(
                "COMPACTION",
                process.pid,
                {
                    "tokens_before": result.tokens_before,
                    "tokens_after": result.tokens_after,
                    "messages_removed": result.messages_removed,
                },
            )

        return {
            "summary": result.summary,
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "messages_removed": result.messages_removed,
            "compression_ratio": result.compression_ratio,
        }

    # =========================================================================
    # 2. Proactive auto-compaction (replaces AgentOS._check_compaction)
    # =========================================================================

    async def check_compaction(self, process) -> None:
        """
        Proactive auto-compaction: compact before step() when tokens exceed
        threshold.

        Args:
            process: The Process object.
        """
        if not process.mmu:
            return

        mmu = process.mmu
        current_tokens = mmu.estimate_tokens()
        max_tokens = self._config.mmu_config.max_context_tokens
        threshold = int(max_tokens * self._config.mmu_config.compress_threshold)  # 90%

        if current_tokens < threshold:
            return

        # Guard: too few messages -> sliding window handles it
        total_messages = sum(len(f.messages) for f in mmu._stack)
        if total_messages < 10:
            return

        # Guard: max compactions reached
        vcpu = process.vcpu
        if vcpu and vcpu._state.compaction_count >= vcpu.config.max_compactions:
            return

        # Execute
        logger.info(f"[{process.pid}] Auto-compaction: {current_tokens} tokens "
                    f"({current_tokens*100//max_tokens}% of {max_tokens}), {total_messages} msgs")
        self._emit_event("AUTO_COMPACTION_TRIGGERED", process.pid,
                         {"current_tokens": current_tokens, "threshold": threshold})

        tokens_before = current_tokens
        success = await self.compact_process(process.pid, mmu)
        tokens_after = mmu.estimate_tokens()

        if success:
            if vcpu:
                vcpu._state.compaction_count += 1
            pct = 100 - (tokens_after * 100 // tokens_before) if tokens_before else 0
            logger.info(f"[{process.pid}] Auto-compaction done: "
                        f"{tokens_before}->{tokens_after} tokens (-{pct}%)")
        else:
            logger.warning(f"[{process.pid}] Auto-compaction failed, sliding window fallback")
        # Never crash -- sliding window is the ultimate safety net

    # =========================================================================
    # 3. Process-level compaction (replaces AgentOS._compaction_for_process)
    # =========================================================================

    async def compact_process(self, pid: str, mmu: MMU) -> bool:
        """
        Process-level compaction: archive old context and reset MMU.

        Args:
            pid: Process ID (for logging).
            mmu: The MMU instance to compact.

        Returns:
            True if compaction succeeded, False otherwise.
        """
        try:
            session_id = "unknown"
            if hasattr(self._llm, "_client") and hasattr(self._llm._client, "session_id"):
                session_id = self._llm._client.session_id

            # Calculate dynamic summary budget based on pinned context budget
            # Summary should take at most 30% of pinned budget to leave room for system rules
            pinned_budget = mmu.config.pinned_budget  # e.g., 2000 tokens
            summary_token_budget = int(pinned_budget * 0.3)  # e.g., 600 tokens
            # Rough estimate: 1 token ~ 2-3 Chinese chars, 4 English chars
            summary_char_budget = summary_token_budget * 2  # Conservative for Chinese

            # Read Memo/NimFS content to include in summary (so key info survives compaction)
            memo_context = self._read_memo_context(mmu)
            global_memo_context = self._read_global_memo_context(mmu)

            async def generate_summary(messages: list) -> str:
                return await self._generate_summary(
                    messages, mmu, memo_context, global_memo_context,
                    summary_char_budget,
                )

            archive_path = await mmu.archive_and_reset(session_id, summarizer=generate_summary)

            if archive_path:
                logger.info(
                    f"[{pid}] Memory compaction successful: Context archived to {archive_path}"
                )
                return True

            logger.warning(f"[{pid}] Memory archiving skipped (no messages?), but allowing reset")
            return True

        except Exception as e:
            logger.error(f"[{pid}] Compaction failed: {e}")
            return False

    # =========================================================================
    # Internal helpers (extracted from nested closures)
    # =========================================================================

    @staticmethod
    def _read_memo_context(mmu: MMU) -> str:
        """Read Memo content from MMU's memo manager."""
        memo_context = ""
        if hasattr(mmu, '_memo_manager') and mmu._memo_manager:
            try:
                memo_content = mmu._memo_manager.read()
                if memo_content and memo_content.strip():
                    memo_context = memo_content.strip()
            except Exception:
                pass
        return memo_context

    @staticmethod
    def _read_global_memo_context(mmu: MMU) -> str:
        """Read global knowledge from NimFS (preferred) or legacy Global Memo."""
        global_memo_context = ""
        if hasattr(mmu, '_nimfs_manager') and mmu._nimfs_manager:
            try:
                gc = mmu._nimfs_manager.load_context(
                    current_goal="Summarize project knowledge for compaction",
                    max_chars=500
                )
                if gc and gc.strip():
                    global_memo_context = gc.strip()[:300]
            except Exception:
                pass
        if not global_memo_context and hasattr(mmu, '_global_memo_manager') and mmu._global_memo_manager:
            try:
                gc = mmu._global_memo_manager.read()
                if gc and gc.strip():
                    global_memo_context = gc.strip()[:300]
            except Exception:
                pass
        return global_memo_context

    async def _compress_summary(self, text: str, max_chars: int) -> str:
        """Use LLM to intelligently compress a summary that's too long."""
        compress_prompt = (
            f"\u4ee5\u4e0b\u6458\u8981\u8fc7\u957f\uff0c\u8bf7\u7cbe\u7b80\u5230{max_chars}\u5b57\u7b26\u4ee5\u5185\uff0c\u4fdd\u7559\u6700\u5173\u952e\u7684\u4fe1\u606f\uff1a\n\n"
            f"{text}\n\n"
            f"\u8981\u6c42\uff1a\n"
            f"1. \u4f18\u5148\u4fdd\u7559\uff1a\u7528\u6237\u63d0\u4f9b\u7684\u5bc6\u7801/\u5bc6\u94a5\u3001\u5173\u952e\u4ee3\u7801\u3001\u914d\u7f6e\u4fe1\u606f\n"
            f"2. \u5176\u6b21\u4fdd\u7559\uff1a\u5f53\u524d\u4efb\u52a1\u72b6\u6001\u3001\u91cd\u8981\u51b3\u7b56\n"
            f"3. \u53ef\u7701\u7565\uff1a\u8fc7\u7a0b\u7ec6\u8282\u3001\u5df2\u89e3\u51b3\u7684\u95ee\u9898\n"
            f"\u8bf7\u76f4\u63a5\u8f93\u51fa\u7cbe\u7b80\u540e\u7684\u6458\u8981\uff08\u4e0d\u8d85\u8fc7{max_chars}\u5b57\u7b26\uff09\uff1a"
        )
        try:
            response = await self._llm.chat(
                messages=[{"role": "user", "content": compress_prompt}],
                tools=None,
            )
            if response and response.content:
                return response.content[:max_chars]  # Final hard limit
        except Exception as e:
            logger.warning(f"Summary compression failed: {e}")
        # Fallback: simple truncation at sentence boundary
        truncated = text[:max_chars]
        for sep in ["\u3002", ".", "\n"]:
            pos = truncated.rfind(sep)
            if pos > max_chars * 0.7:
                return truncated[: pos + 1] + "...\u005b\u5df2\u538b\u7f29\u005d"
        return truncated + "...\u005b\u5df2\u538b\u7f29\u005d"

    async def _generate_summary(
        self,
        messages: list,
        mmu: MMU,
        memo_context: str,
        global_memo_context: str,
        summary_char_budget: int,
    ) -> str:
        """Generate a summary of the conversation using LLM."""
        try:
            # Extract any previous summary from messages (to preserve cascade info)
            previous_summary = ""
            for m in messages:
                content = str(m.content) if m.content else ""
                if any(marker in content for marker in [
                    "[Memory Recall]", "\u5173\u952e\u4fe1\u606f\u6458\u8981",
                    "\U0001f3af PRIMARY GOAL", "\U0001f4dd EXECUTION STATUS",
                    "[Mission Control]",
                ]):
                    previous_summary = content
                    break

            # Build a prompt for summarization - uniform sampling covers all history
            sample_size = min(len(messages), 50)
            step = max(1, len(messages) // sample_size)
            sampled = messages[::step][-sample_size:]

            # Ensure the first user message is always included (original instruction)
            first_user_msg = None
            for m in messages:
                if m.role == "user":
                    first_user_msg = m
                    break
            if first_user_msg is not None and first_user_msg not in sampled:
                sampled.insert(0, first_user_msg)

            context = "\n".join(
                f"[{m.role.upper()}]: {str(m.content)[:300]}"
                for m in sampled
            )

            # Append Memo content so summarizer preserves key info from notes
            if memo_context:
                context += f"\n\n\u3010\u7528\u6237\u5907\u5fd8\u5f55 Memo\u3011\n{memo_context[:500]}"

            if global_memo_context:
                context += f"\n\n\u3010\u5168\u5c40\u77e5\u8bc6 Global Memo\u3011\n{global_memo_context}"

            # Calculate target length based on whether we're merging
            target_chars = summary_char_budget
            if previous_summary:
                # When merging, be more aggressive about compression
                target_chars = int(summary_char_budget * 0.8)

            # Include previous summary to prevent cascade loss
            if previous_summary:
                summary_prompt = (
                    "\u8bf7\u4f5c\u4e3a\u4efb\u52a1\u7ba1\u7406\u8005\uff0c\u5408\u5e76\u5e76\u66f4\u65b0\u4ee5\u4e0b\u6267\u884c\u6458\u8981\u3002\n\n"
                    f"\u3010\u4e4b\u524d\u7684\u6458\u8981\u3011\n{previous_summary[:1000]}\n\n"
                    f"\u3010\u65b0\u8fdb\u5c55\u3011\n{context}\n\n"
                    "**\u6838\u5fc3\u8981\u6c42**\uff1a\n"
                    "1. \u5fc5\u987b\u4fdd\u7559\u6240\u6709\u5173\u952e\u6280\u672f\u7ec6\u8282\uff08\u4ee3\u7801\u8def\u5f84\u3001\u914d\u7f6e\u503c\u3001\u5bc6\u7801\uff09\u3002\n"
                    "2. \u5fc5\u987b\u8bc4\u4f30\u5f53\u524d\u8fdb\u5ea6\u4e0e\u6700\u7ec8\u76ee\u6807\u7684\u8ddd\u79bb\uff08\u9632\u6b62\u4efb\u52a1\u6f02\u79fb\uff09\u3002\n"
                    "3. \u5fc5\u987b\u4fdd\u7559\u7528\u6237\u7684\u539f\u59cb\u4efb\u52a1\u6307\u4ee4\u548c\u76ee\u6807\u3002\n"
                    f"\u8bf7\u7528\u4e2d\u6587\u56de\u590d\uff08{target_chars}\u5b57\u4ee5\u5185\uff09\u3002\n\n"
                    "**OUTPUT FORMAT**:\n"
                    "NEW_MILESTONES: [Milestone 1], [Milestone 2]\n"
                    "SUMMARY: [Your summary content here]"
                )
            else:
                summary_prompt = (
                    "\u8bf7\u4f5c\u4e3a\u4efb\u52a1\u7ba1\u7406\u8005\uff0c\u603b\u7ed3\u5f53\u524d\u6267\u884c\u72b6\u6001\u3002\n\n"
                    f"\u3010\u5bf9\u8bdd\u5185\u5bb9\u3011\n{context}\n\n"
                    "**\u6838\u5fc3\u8981\u6c42**\uff1a\n"
                    "1. \u63d0\u53d6\u6240\u6709\u5173\u952e\u6280\u672f\u7ec6\u8282\uff08\u4ee3\u7801\u8def\u5f84\u3001\u914d\u7f6e\u503c\u3001\u5bc6\u7801\uff09\u3002\n"
                    "2. \u660e\u786e\u4e0b\u4e00\u6b65\u884c\u52a8\u8ba1\u5212\u3002\n"
                    "3. \u5fc5\u987b\u4fdd\u7559\u7528\u6237\u7684\u539f\u59cb\u4efb\u52a1\u6307\u4ee4\u548c\u76ee\u6807\u3002\n"
                    f"\u8bf7\u7528\u4e2d\u6587\u56de\u590d\uff08{target_chars}\u5b57\u4ee5\u5185\uff09\u3002\n\n"
                    "**OUTPUT FORMAT**:\n"
                    "NEW_MILESTONES: [Milestone 1]\n"
                    "SUMMARY: [Your summary content here]"
                )

            # Use LLM.chat() to generate summary (not complete())
            response = await self._llm.chat(
                messages=[{"role": "user", "content": summary_prompt}],
                tools=None,  # No tools needed for summary
            )

            if response and response.content:
                # Parse response for milestones
                content = response.content
                milestones = []
                summary = content

                if "NEW_MILESTONES:" in content and "SUMMARY:" in content:
                    try:
                        parts = content.split("SUMMARY:", 1)
                        milestone_part = parts[0].replace("NEW_MILESTONES:", "").strip()
                        summary = parts[1].strip()

                        if milestone_part and milestone_part.lower() != "none":
                            milestones = [m.strip() for m in milestone_part.split(",") if m.strip()]
                    except Exception:
                        pass  # Fallback to raw content if parsing fails

                # Register milestones with MMU
                if milestones:
                    mmu.add_milestones(milestones)
                    logger.info(f"\U0001f6a9 Registered milestones: {milestones}")

                # Smart budget check: if over budget, use LLM to re-compress
                if len(summary) > summary_char_budget:
                    logger.warning(
                        f"Summary ({len(summary)} chars) exceeds budget ({summary_char_budget} chars), "
                        f"using LLM to compress..."
                    )
                    summary = await self._compress_summary(summary, summary_char_budget)
                    logger.info(f"Summary compressed to {len(summary)} chars")

                return summary
            return "Summary generation failed"
        except Exception as e:
            logger.warning(f"Summary generation failed: {e}")
            return f"[Summary unavailable: {e}]"
