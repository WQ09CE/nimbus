import hashlib
import re
import json
import logging
from typing import Any, Dict, List, Optional
from nimbus.core.memory.state_manager import StateManager
from nimbus.core.memory.token_budget import approximate_message_tokens, drop_oldest_non_essential

logger = logging.getLogger(__name__)

# NimFS tools whose output should never be re-offloaded.
_NIMFS_NO_OFFLOAD = frozenset({
    "NimFSReadArtifact",
    "NimFSListArtifacts",
    "NimFSWriteArtifact",
    "Memo",
    "Recall",
    "ReadMemo",
})

# Lazy Expansion: auto-expand NimFS refs in _optimize_context
_NIMFS_REF_PATTERN = re.compile(r"nimfs://artifact/([\w\-]+)")
_NIMFS_OFFLOAD_MARKER = "[NimFS Auto-Offload]"
_INLINE_EXPAND_MAX_CHARS = 15_000

class ContextAssembler:
    """
    Assembles the 'Anchor & Stream' context layout for the LLM.
    Handles token buffering, NimFS reference expansions, and image downgrading.
    """
    
    def __init__(self, mmu: Any):
        """Bind precisely to the parent MMU instance (for read operations)."""
        self.mmu = mmu
        self.config = mmu.config

    def _approx_tokens(self, text: str) -> int:
        if not text:
            return 0
        return len(text) // 4

    def _image_key(self, block: Dict[str, Any]) -> str:
        """Generate unique key for an image block using content hash."""
        data = block.get("data", "")
        if isinstance(data, str) and data:
            digest = hashlib.sha256(data.encode("ascii", errors="replace")).hexdigest()[:16]
        else:
            digest = ""
        mime = block.get("mimeType", "")
        return f"{mime}:{digest}"

    def _optimize_context(self, messages: List[Dict[str, Any]], hot_count: int = 0, compact_on_limit: bool = False) -> List[Dict[str, Any]]:
        """
        Optimize context by:
        0. Lazy-expand NimFS offloaded refs if token budget allows.
        1. Downgrading duplicate/budget-exceeding images.
        """
        VIEW_MAX_TOOL_CHARS = 10_000
        HISTORY_MAX_TOOL_CHARS = 1_000

        total = len(messages)
        hot_boundary = 0 if hot_count == 0 else total - hot_count

        # --- Phase 0: Dynamic Tool Result Truncation (Lazy Offload) ---
        if getattr(self.mmu, 'nimfs_workspace', None):
            # Calculate current total characters
            total_chars = 0
            for m in messages:
                c = m.get("content", "")
                if isinstance(c, str):
                    total_chars += len(c)
                elif isinstance(c, list):
                    for block in c:
                        if isinstance(block, dict) and block.get("type") == "text":
                            total_chars += len(block.get("text", ""))

            budget_chars = (self.config.max_context_tokens * 4)
            safe_budget_chars = int(budget_chars * 0.9)  # Leave 10% breathing room
            max_inline_chars = getattr(self.config, 'max_inline_tool_chars', 50_000)
            if max_inline_chars == 0:
                # 0 means disabled offloading
                max_inline_chars = float('inf')

            # Lazy Truncation: Walk through the history, offloading large tool results 
            # if we are exceeding the global budget OR if they exceed the absolute hard cap.
            for i in range(total):
                msg = messages[i]
                if msg.get("role") != "tool":
                    continue
                
                content = msg.get("content", "")
                if not isinstance(content, str):
                    continue
                
                content_len = len(content)
                # Skip if it is small enough to not even be considered for NimFS
                if content_len < min(2000, max_inline_chars):
                    continue
                
                # Should we offload this message?
                needs_offload = False
                if content_len > max_inline_chars:
                    needs_offload = True
                    logger.debug(f"Triggering hard-cap offload for tool result ({content_len} > {max_inline_chars} chars)")
                elif total_chars > safe_budget_chars:
                    needs_offload = True
                    logger.debug(f"Triggering budget offload for tool result ({total_chars} > {safe_budget_chars} chars)")
                
                if needs_offload:
                    tool_name = msg.get("name", "unknown")
                    if tool_name in _NIMFS_NO_OFFLOAD:
                        continue
                        
                    try:
                        from nimbus.core.nimfs.manager import NimFSManager
                        from nimbus.core.nimfs.models import ArtifactTTL
                        
                        manager = NimFSManager(self.mmu.nimfs_workspace)
                        counter = getattr(self.mmu, '_nimfs_offload_counter', 0)
                        task_id = f"mmu-lazy-offload-{self.mmu.process_id or 'proc'}-{counter + i}"
                        
                        ref = manager.write_artifact(
                            content=content,
                            task_id=task_id,
                            producer=f"mmu/{tool_name}",
                            artifact_type="text",
                            ttl=ArtifactTTL.SESSION,
                            summary=content[:150].replace("\n", " "),
                        )
                        
                        preview_len = min(2000, content_len)
                        stub = (
                            f"[NimFS Auto-Offload] Tool '{tool_name}' returned {content_len:,} chars.\n"
                            f"Content was offloaded to prevent context overflow.\n"
                            f"Full output stored at: {ref}\n"
                            f"Use NimFSReadArtifact(ref='{ref}') to retrieve the complete content if needed.\n\n"
                            f"Preview:\n{content[:preview_len]}{'...' if content_len > preview_len else ''}"
                        )
                        
                        # Update the payload and the running tally
                        new_msg = dict(msg)
                        new_msg["content"] = stub
                        messages[i] = new_msg
                        
                        chars_saved = content_len - len(stub)
                        total_chars -= chars_saved
                        
                        if hasattr(self.mmu, '_nimfs_offload_counter'):
                            self.mmu._nimfs_offload_counter += 1
                        else:
                            self.mmu._nimfs_offload_counter = 1
                        
                    except Exception as e:
                        logger.warning(f"Lazy NimFS offload failed for tool {tool_name}: {e}")
                        pass

        # --- Phase 1: Image Downgrade Logic ---
        keep_indices = set()
        seen_keys = set()
        current_image_tokens = 0
        from nimbus.core.memory.context import IMAGE_TOKEN_ESTIMATE

        for i in range(total - 1, -1, -1):
            msg = messages[i]
            content = msg.get("content")
            if not isinstance(content, list):
                keep_indices.add(i)
                continue

            has_image = any(isinstance(b, dict) and b.get("type") in ("image", "image_url") for b in content)
            if not has_image:
                keep_indices.add(i)
                continue

            new_content = []
            msg_image_tokens = 0
            for block in content:
                if isinstance(block, dict) and block.get("type") in ("image", "image_url"):
                    key = self._image_key(block)
                    if key in seen_keys:
                        new_content.append({"type": "text", "text": f"[Duplicate Image Omitted: {key}]"})
                        continue
                    if current_image_tokens + IMAGE_TOKEN_ESTIMATE > self.config.max_image_tokens:
                        new_content.append({"type": "text", "text": f"[Image Omitted: Context Budget Exceeded]"})
                        continue
                    seen_keys.add(key)
                    current_image_tokens += IMAGE_TOKEN_ESTIMATE
                    msg_image_tokens += IMAGE_TOKEN_ESTIMATE
                    new_content.append(block)
                else:
                    new_content.append(block)
            new_msg = dict(msg)
            new_msg["content"] = new_content
            messages[i] = new_msg
            keep_indices.add(i)

        return [messages[i] for i in range(total) if i in keep_indices]

    def assemble(
        self,
        system_prefix: Optional[str] = None,
        model_features: Optional[Any] = None,
        compact_on_limit: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Assemble the full context with "Recent-Anchored Sliding Window".
        Structure:
        1. Pinned Context (Developer Prompt)
        2. Global Summary (Archived History)
        3. Sliding Frame Window (Stream)
        """
        # 1. Pinned Context (Anchor)
        pinned_text = ""
        if self.mmu._pinned:
            if system_prefix:
                pinned_text += f"{system_prefix}\n\n"
            if getattr(self.mmu._pinned, "system_rules", None):
                pinned_text += f"SYSTEM RULES:\n{self.mmu._pinned.system_rules}\n\n"
            if self.mmu._pinned.custom_anchors:
                for k, v in self.mmu._pinned.custom_anchors.items():
                    pinned_text += f"{k.upper()}:\n{v}\n\n"
            if self.mmu._pinned.capabilities:
                pinned_text += f"CAPABILITIES:\n{self.mmu._pinned.capabilities}\n\n"
            if self.mmu._pinned.workspace_info:
                pinned_text += f"WORKSPACE:\n{self.mmu._pinned.workspace_info}\n\n"
            if getattr(self.mmu._pinned, "env_state", ""):
                 pinned_text += f"ENVIRONMENT STATE:\n{self.mmu._pinned.env_state}\n\n"
        
        pinned_tokens = self._approx_tokens(pinned_text)
        
        # 2. State & Memo
        global_summary = getattr(self.mmu, "_global_summary", "")
        clipboard = getattr(self.mmu, "_clipboard", "")
        
        # NimFS Memory Context (auto-loaded)
        memory_context = getattr(self.mmu, "_memory_context", "")

        # Cap memo section to ~20% of total budget to prevent Anchor overflow
        memo_token_budget = self.config.max_context_tokens // 5

        memo_text = ""
        if global_summary:
            memo_text += f"GLOBAL SUMMARY (Previous Sessions):\n{global_summary}\n\n"
        if memory_context:
            memo_text += f"LONG-TERM MEMORY:\n{memory_context}\n\n"
        if clipboard:
            memo_text += f"CLIPBOARD / NOTES:\n{clipboard}\n\n"

        # Truncate memo if exceeding budget
        memo_tokens = self._approx_tokens(memo_text)
        if memo_tokens > memo_token_budget:
            max_chars = memo_token_budget * 4  # Approx 4 chars per token
            memo_text = memo_text[:max_chars] + "\n[... memo truncated due to token budget ...]\n"
            memo_tokens = self._approx_tokens(memo_text)
            logger.warning(f"⚠️ Memo section truncated: {memo_tokens} tokens (budget: {memo_token_budget})")
        
        core_tokens = pinned_tokens + memo_tokens
        remaining_budget = self.config.max_context_tokens - core_tokens

        # 3. Stream Messages
        stream_messages: List[Dict[str, Any]] = []
        
        if self.mmu.stack_depth > 1:
            stream_messages.append({
                "role": "system",
                "content": f"[STACK DEPTH: {self.mmu.stack_depth} - Running sub-task]"
            })

        for frame in self.mmu._stack:
            if not frame.messages:
                continue

            msgs = [msg.to_dict() for msg in frame.to_context_messages()]
            stream_messages.extend(msgs)

        logger.debug(
            f"📊 assemble_context budget: max={self.config.max_context_tokens}, "
            f"pinned+state+memo={core_tokens}, remaining={remaining_budget}, "
            f"stream_msgs={len(stream_messages)}"
        )

        # Truncate window logic if needed
        # (Simplified to fit MVP without large changes, using the token_budget helper)
        hot_count = min(len(stream_messages), self.config.keep_recent_messages)
        
        stream_tokens = sum(approximate_message_tokens(m) for m in stream_messages)
        if stream_tokens > remaining_budget:
            logger.warning(
                f"⚠️ Context window exceeded: {stream_tokens} > {remaining_budget}. "
                f"Applying 'Smart Drop' sliding window."
            )
            while stream_tokens > remaining_budget and len(stream_messages) > hot_count:
                dropped = drop_oldest_non_essential(stream_messages, hot_count, self.config.auto_detect_failures)
                if not dropped:
                    break
                stream_tokens = sum(approximate_message_tokens(m) for m in stream_messages)
                
            if stream_tokens > remaining_budget:
                logger.critical("🚨 Smart Drop failed to reduce context enough. Forcing strict history drop.")
                stream_messages = stream_messages[-hot_count:]
                stream_tokens = sum(approximate_message_tokens(m) for m in stream_messages)

        optimized_stream = self._optimize_context(stream_messages, hot_count=hot_count, compact_on_limit=compact_on_limit)
        
        logger.debug(
            f"📊 hot: {hot_count}/{len(stream_messages)} msgs, "
            f"{stream_tokens} tokens (budget={remaining_budget}), "
            f"history_budget={remaining_budget}"
        )

        final_context = []
        if pinned_text:
            final_context.append({"role": "system", "content": pinned_text})
        if memo_text:
            final_context.append({"role": "system", "content": memo_text})
        
        final_context.extend(optimized_stream)
        
        return final_context
