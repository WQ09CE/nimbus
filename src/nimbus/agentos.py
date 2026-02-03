"""
Nimbus v2 AgentOS - The Top-Level Integration Layer

AgentOS is the unified entry point for the Nimbus v2 system.
It orchestrates all components: VCPU, MMU, Gate, Scheduler, Decoder.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional

from loguru import logger

if TYPE_CHECKING:
    pass

from nimbus.core.compaction import (
    CompactionConfig,
    CompactionEngine,
    DefaultCompactionLLM,
)
from nimbus.core.memory.context import PinnedContext
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.protocol import Event, Fault, ToolResult
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, LLMClient, VCPUConfig
from nimbus.core.scheduler import (
    DAG,
    EventStream,
    Scheduler,
    SchedulerConfig,
    Task,
)

# Session and Compaction
from nimbus.core.session import SessionManager
from nimbus.os.gate import (
    KernelGate,
    SimpleEventStream,
)
from nimbus.tools.base import ToolDefinition, ToolRegistry

# =============================================================================
# AgentOS Configuration
# =============================================================================


@dataclass
class OSConfig:
    """Configuration for AgentOS (alias for AgentOSConfig for backward compatibility)"""

    workspace_root: str = "."
    max_processes: int = 10


@dataclass
class AgentOSConfig:
    """Configuration for AgentOS."""

    max_processes: int = 10
    default_timeout: float = 300.0
    vcpu_config: VCPUConfig = field(default_factory=VCPUConfig)
    scheduler_config: SchedulerConfig = field(default_factory=SchedulerConfig)
    mmu_config: MMUConfig = field(default_factory=MMUConfig)
    # Kernel tools
    kernel_tools: bool = True
    # System prompt based on pi-coding-agent design
    system_rules: str = """You are an expert coding assistant. You help users by reading files, executing commands, editing code, and writing new files.

Guidelines:
- Use Bash for file operations like ls, grep, find, rg
- Use Read to examine files before editing
- Use Edit for precise changes (old text must match exactly)
- Use Write only for new files or complete rewrites
- Be concise in your responses
- Show file paths clearly when working with files
- Respond in the SAME LANGUAGE as the user (Chinese → Chinese, English → English)
- Use ASCII art for diagrams.

Workflow:
1. Read files to understand the code
2. Edit/Write to make changes
3. Reply to the user when done. You do NOT need to call a special tool to finish.

Rules:
- Act immediately on clear instructions, don't ask for confirmation
- After Edit/Write success, just reply to the user (don't re-read to verify)
- If a tool fails, try a different approach (don't retry with identical arguments)
- Trust tool results - if Edit says success, the file IS modified"""
    workspace_info: str = ""
    capabilities: str = ""
    # Session persistence
    session_dir: Optional[Path] = None  # None = ephemeral mode
    enable_session: bool = True
    # Compaction
    compaction_config: CompactionConfig = field(default_factory=CompactionConfig)


# =============================================================================
# Process State
# =============================================================================

ProcessState = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]


@dataclass
class Process:
    """A process managed by AgentOS."""

    pid: str
    goal: str
    role: str = ""
    state: ProcessState = "PENDING"
    vcpu: Optional[VCPU] = None
    mmu: Optional[MMU] = None
    gate: Optional[KernelGate] = None
    result: Optional[ToolResult] = None
    task: Optional[asyncio.Task] = None
    inbox: List[str] = field(default_factory=list)
    signals: Dict[str, bool] = field(default_factory=dict)


# =============================================================================
# AgentOS
# =============================================================================


class AgentOS:
    """Agent Operating System - The Top-Level Integration Layer."""

    def __init__(
        self,
        llm_client: LLMClient,
        tools: Optional[Dict[str, Callable]] = None,
        config: Optional[AgentOSConfig] = None,
    ):
        self._llm = llm_client
        self.config = config or AgentOSConfig()

        # Tool registry
        self._tools = ToolRegistry()

        # 1. Register Kernel Tools (Auto)
        kernel_tools_desc = ""
        if self.config.kernel_tools:
            from nimbus.tools import BASH_TOOL, EDIT_TOOL, READ_TOOL, WRITE_TOOL
            from nimbus.tools.base import ToolDefinition, ToolParameter

            kernel_tools_list = [READ_TOOL, WRITE_TOOL, EDIT_TOOL, BASH_TOOL]
            kernel_tools_desc = "Available tools:\n"

            # Helper to parse legacy tool dicts
            def _parse_legacy_tool(data: Dict[str, Any]) -> ToolDefinition:
                params = []
                schema = data.get("parameters", {})
                props = schema.get("properties", {})
                required = schema.get("required", [])

                for name, spec in props.items():
                    params.append(
                        ToolParameter(
                            name=name,
                            type=spec.get("type", "string"),
                            description=spec.get("description", ""),
                            required=(name in required),
                        )
                    )

                return ToolDefinition(
                    name=data["name"],
                    description=data.get("description", ""),
                    parameters=params,
                )

            for tool_data in kernel_tools_list:
                try:
                    def_obj = _parse_legacy_tool(tool_data)
                    self._tools.register(def_obj, tool_data["function"])

                    params = []
                    for p in def_obj.parameters:
                        p_str = p.name
                        if not p.required:
                            p_str += "?"
                        params.append(p_str)

                    sig = f"{def_obj.name}({', '.join(params)})"
                    desc = def_obj.description.split(".")[0] + "."
                    kernel_tools_desc += f"- {sig}: {desc}\n"
                except ValueError:
                    pass

        # 2. Register User Tools
        if tools:
            from nimbus.tools.base import ToolDefinition

            for name, func in tools.items():
                try:
                    if hasattr(func, "_tool_definition"):
                        self._tools.register_decorated(func)
                    else:
                        definition = ToolDefinition(
                            name=name,
                            description=func.__doc__ or "No description",
                            parameters=[],
                        )
                        self._tools.register(definition, func)
                except ValueError:
                    pass

        # 3. Inject Kernel Tools into System Prompt
        if kernel_tools_desc and "Available tools:" not in self.config.system_rules:
            parts = self.config.system_rules.split("\n\n", 1)
            if len(parts) > 1:
                self.config.system_rules = parts[0] + "\n\n" + kernel_tools_desc + "\n" + parts[1]
            else:
                self.config.system_rules += "\n\n" + kernel_tools_desc

        self._scheduler = Scheduler(
            config=self.config.scheduler_config,
            events=EventStream(),
        )

        self._processes: Dict[str, Process] = {}
        self._events = SimpleEventStream()
        self._current_session_id: Optional[str] = None

        if self.config.enable_session and self.config.session_dir:
            self._session_mgr: Optional[SessionManager] = SessionManager(
                session_dir=self.config.session_dir
            )
        else:
            self._session_mgr = None

        self._compaction_engine = CompactionEngine(
            config=self.config.compaction_config,
            llm=DefaultCompactionLLM(llm_client),
        )

    # =========================================================================
    # Main API
    # =========================================================================

    async def run(self, goal: str, role: str = "") -> ToolResult:
        pid = self.spawn(goal, role=role)
        return await self.wait(pid)

    async def run_stream(self, goal: str, role: str = ""):
        pid = self.spawn(goal, role=role)
        process = self._processes.get(pid)
        if not process:
            yield {"type": "error", "message": f"Process {pid} not found"}
            return

        vcpu = process.vcpu
        mmu = process.mmu

        if vcpu.config.pin_goal:
            pinned_goal = await vcpu._prepare_goal_for_pinning(goal)
            mmu.pin_user_goal(pinned_goal)
        mmu.add_user_message(goal)

        yield {"type": "planning", "content": "Starting execution..."}

        while True:
            result = await vcpu.step()

            # Handle CONTEXT_OVERFLOW fault - trigger compaction and retry
            if result.fault and result.fault.code == "CONTEXT_OVERFLOW":
                ctx = result.fault.context or {}
                yield {
                    "type": "compaction",
                    "message": f"Context overflow ({ctx.get('current_tokens')} tokens), compacting...",
                }
                success = await self._compaction_for_process(pid, mmu)
                if success:
                    yield {"type": "compaction_done", "message": "Compaction complete"}
                    continue  # Retry the step after compaction
                else:
                    yield {"type": "error", "message": "Compaction failed"}
                    return

            for i, action in enumerate(result.actions):
                action_kind = getattr(action, "kind", None)

                if action_kind == "TOOL_CALL":
                    tool_name = getattr(action, "name", "unknown")
                    tool_args = getattr(action, "args", {})
                    tool_id = getattr(action, "id", None)

                    yield {
                        "type": "tool_call",
                        "name": tool_name,
                        "args": tool_args,
                        "call_id": tool_id,
                    }
                    if i < len(result.results):
                        tool_result = result.results[i]
                        yield {
                            "type": "tool_result",
                            "name": tool_name,
                            "args": tool_args,
                            "tool_use_id": tool_id,
                            "content": getattr(tool_result, "output", str(tool_result)),
                        }
                elif action_kind == "THOUGHT":
                    content = action.args.get("content", "") if action.args else ""
                    if content:
                        yield {"type": "text", "content": content}
                elif action_kind == "RETURN":
                    result_value = action.args.get("result", "") if action.args else ""
                    yield {
                        "type": "done",
                        "result": {
                            "status": "OK",
                            "output": result_value,
                        },
                    }
                    return

            if result.is_final:
                yield {
                    "type": "done",
                    "result": {
                        "status": "FAULT" if result.fault else "OK",
                        "output": result.final_result,
                        "error": str(result.fault) if result.fault else None,
                    },
                }
                return

    async def chat(self, message: str, session_id: str | None = None) -> ToolResult:
        is_existing_process = False
        if session_id and session_id in self._processes:
            process = self._processes[session_id]
            is_existing_process = True
        else:
            if not session_id:
                session_id = f"chat-{uuid.uuid4().hex[:8]}"
            mmu = self._create_mmu(session_id)
            gate = self._create_gate(session_id, "chat")
            decoder = InstructionDecoder()

            vcpu = VCPU(
                alu=self._llm,
                decoder=decoder,
                gate=gate,
                mmu=mmu,
                config=self.config.vcpu_config,
                tools=self._tools.get_definitions(format="openai"),
            )

            vcpu.set_compaction_callback(
                lambda sid=session_id, m=mmu: self._compaction_for_process(sid, m)
            )

            process = Process(
                pid=session_id,
                goal="Interactive chat session",
                role="chat",
                state="PENDING",
                vcpu=vcpu,
                mmu=mmu,
                gate=gate,
            )
            self._processes[session_id] = process
            self._emit_event("PROC_SPAWNED", session_id, {"goal": "chat", "role": "chat"})

        if is_existing_process and process.state == "RUNNING":
            from loguru import logger

            logger.info(f"Process {session_id} is busy. Converting chat to injection.")
            self.inject_message(process.pid, message)

            if self._session_mgr:
                from nimbus.core.memory.context import Message

                self._session_mgr.append_message(Message(role="user", content=message))

            return ToolResult(
                status="OK", output="[Instruction appended to running task]", is_final=True
            )

        process.vcpu._reset()

        if self._session_mgr:
            from nimbus.core.memory.context import Message

            self._session_mgr.append_message(Message(role="user", content=message))

        self.inject_message(process.pid, message)

        if process.state != "RUNNING":
            process.state = "RUNNING"
            return await self._run_process(process)

        return ToolResult(status="OK", output="[Already Running]")

    # =========================================================================
    # Process Management
    # =========================================================================

    def list_processes(self) -> List[str]:
        """List all active process IDs."""
        return list(self._processes.keys())

    def get_process(self, pid: str) -> "Process | None":
        """Get a process by ID."""
        return self._processes.get(pid)

    def get_session(self, session_id: str) -> "Process | None":
        return self._processes.get(session_id)

    def end_session(self, session_id: str) -> None:
        if session_id in self._processes:
            process = self._processes.pop(session_id)
            process.state = "COMPLETED"
            self._emit_event("PROC_FINISHED", session_id, {"reason": "session_ended"})

    # =========================================================================
    # Session Management (NEW)
    # =========================================================================

    def new_session(self, parent_session: Optional[str] = None) -> str:
        if self._session_mgr:
            return self._session_mgr.new_session(parent_session)
        return f"ephemeral-{uuid.uuid4().hex[:8]}"

    def load_session(self, session_file: Path) -> bool:
        if not self._session_mgr:
            return False
        return self._session_mgr.load_session(session_file)

    def get_session_stats(self) -> Optional[Dict[str, Any]]:
        if self._session_mgr:
            return self._session_mgr.get_stats()
        return None

    def list_recent_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        if self._session_mgr:
            return self._session_mgr.list_recent_sessions(limit)
        return []

    # =========================================================================
    # Compaction (NEW)
    # =========================================================================

    async def compact(
        self,
        session_id: Optional[str] = None,
        custom_instructions: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        target_session = session_id or self._current_session_id
        if not target_session:
            return None

        process = self._processes.get(target_session)
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
                target_session,
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

    async def _check_compaction(self, process: Process) -> None:
        if not process.mmu:
            return

        current_tokens = process.mmu.estimate_tokens()
        max_tokens = self.config.mmu_config.max_context_tokens

        if self._compaction_engine.should_compact(
            [msg for frame in process.mmu._stack for msg in frame.messages],
            max_tokens,
        ):
            self._emit_event(
                "AUTO_COMPACTION_START",
                process.pid,
                {
                    "tokens": current_tokens,
                    "max_tokens": max_tokens,
                },
            )

            await self.compact(session_id=process.pid)

            self._emit_event("AUTO_COMPACTION_END", process.pid, {})

    async def _compaction_for_process(self, pid: str, mmu: MMU) -> bool:
        try:
            session_id = "unknown"
            if hasattr(self._llm, "_client") and hasattr(self._llm._client, "session_id"):
                session_id = self._llm._client.session_id

            # Calculate dynamic summary budget based on pinned context budget
            # Summary should take at most 30% of pinned budget to leave room for system rules
            pinned_budget = mmu.config.pinned_budget  # e.g., 2000 tokens
            summary_token_budget = int(pinned_budget * 0.3)  # e.g., 600 tokens
            # Rough estimate: 1 token ≈ 2-3 Chinese chars, 4 English chars
            summary_char_budget = summary_token_budget * 2  # Conservative for Chinese

            async def compress_summary(text: str, max_chars: int) -> str:
                """Use LLM to intelligently compress a summary that's too long."""
                compress_prompt = (
                    f"以下摘要过长，请精简到{max_chars}字符以内，保留最关键的信息：\n\n"
                    f"{text}\n\n"
                    f"要求：\n"
                    f"1. 优先保留：用户提供的密码/密钥、关键代码、配置信息\n"
                    f"2. 其次保留：当前任务状态、重要决策\n"
                    f"3. 可省略：过程细节、已解决的问题\n"
                    f"请直接输出精简后的摘要（不超过{max_chars}字符）："
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
                for sep in ["。", ".", "\n"]:
                    pos = truncated.rfind(sep)
                    if pos > max_chars * 0.7:
                        return truncated[: pos + 1] + "...[已压缩]"
                return truncated + "...[已压缩]"

            # Create a summarizer that uses the LLM to generate a summary
            async def generate_summary(messages: list) -> str:
                """Generate a summary of the conversation using LLM."""
                try:
                    # Extract any previous summary from messages (to preserve cascade info)
                    previous_summary = ""
                    for m in messages:
                        content = str(m.content) if m.content else ""
                        if "[Memory Recall]" in content or "关键信息摘要" in content:
                            previous_summary = content
                            break

                    # Build a prompt for summarization
                    context = "\n".join(
                        f"[{m.role.upper()}]: {str(m.content)[:500]}"
                        for m in messages[-20:]  # Last 20 messages
                    )

                    # Calculate target length based on whether we're merging
                    target_chars = summary_char_budget
                    if previous_summary:
                        # When merging, be more aggressive about compression
                        target_chars = int(summary_char_budget * 0.8)

                    # Include previous summary to prevent cascade loss
                    if previous_summary:
                        summary_prompt = (
                            "请合并并更新以下信息摘要。\n\n"
                            f"【之前的摘要】\n{previous_summary[:1000]}\n\n"
                            f"【新对话内容】\n{context}\n\n"
                            "请保留之前摘要中的关键信息（特别是用户提供的密码、代码等），"
                            f"并添加新对话中的重要内容。用中文简洁回复（{target_chars}字以内）："
                        )
                    else:
                        summary_prompt = (
                            "请简洁总结以下对话的关键信息，包括：\n"
                            "1. 用户提供的重要数据（如密码、代码、配置等）\n"
                            "2. 已完成的操作和结果\n"
                            "3. 当前任务状态\n\n"
                            f"对话内容：\n{context}\n\n"
                            f"请用中文简洁总结（{target_chars}字以内）："
                        )

                    # Use LLM.chat() to generate summary (not complete())
                    response = await self._llm.chat(
                        messages=[{"role": "user", "content": summary_prompt}],
                        tools=None,  # No tools needed for summary
                    )

                    if response and response.content:
                        summary = response.content

                        # Smart budget check: if over budget, use LLM to re-compress
                        if len(summary) > summary_char_budget:
                            logger.warning(
                                f"Summary ({len(summary)} chars) exceeds budget ({summary_char_budget} chars), "
                                f"using LLM to compress..."
                            )
                            summary = await compress_summary(summary, summary_char_budget)
                            logger.info(f"Summary compressed to {len(summary)} chars")

                        return summary
                    return "Summary generation failed"
                except Exception as e:
                    logger.warning(f"Summary generation failed: {e}")
                    return f"[Summary unavailable: {e}]"

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

    async def run_dag(self, dag: DAG) -> ToolResult:
        await self._scheduler.submit_dag(dag)

        async def executor(task: Task) -> ToolResult:
            return await self.run(task.spec.goal, role=task.spec.process_role)

        return await self._scheduler.run_dag(dag.id, executor)

    def interrupt(self, session_id: Optional[str] = None) -> bool:
        signalled = False

        targets = []
        if session_id:
            targets.append(session_id)
        else:
            targets = list(self._processes.keys())

        for pid in targets:
            process = self._processes.get(pid)
            if process and process.state == "RUNNING":
                process.signals["interrupt"] = True
                signalled = True
                logger.info(f"[{pid}] Signal 'interrupt' set")

        return signalled

    def inject_message(self, pid: str, message: str) -> bool:
        process = self._processes.get(pid)
        if not process:
            return False

        process.inbox.append(message)
        logger.info(f"[{pid}] Message injected into inbox: {message[:50]}...")
        return True

    async def _run_process(self, process: Process) -> ToolResult:
        try:
            if process.vcpu is None:
                raise RuntimeError("Process has no VCPU")

            if not process.vcpu._is_running:
                process.vcpu._reset()
                process.vcpu._is_running = True

                if process.vcpu.config.pin_goal and process.role != "chat":
                    pinned_goal = await process.vcpu._prepare_goal_for_pinning(process.goal)
                    process.mmu.pin_user_goal(pinned_goal)
                    process.mmu.add_user_message(process.goal)

            final_result = None

            while process.vcpu._is_running and not process.vcpu._is_done:
                if process.signals.get("interrupt"):
                    logger.info(f"[{process.pid}] Interrupted by signal")
                    process.state = "CANCELLED"
                    process.signals["interrupt"] = False
                    process.vcpu._is_done = True
                    return ToolResult(
                        status="CANCELLED",
                        fault=Fault(
                            domain="KERNEL", code="INTERRUPTED", message="Interrupted by user"
                        ),
                    )

                while process.inbox:
                    msg = process.inbox.pop(0)
                    if process.role == "chat":
                        process.mmu.add_user_message(msg)
                    else:
                        process.mmu.add_user_message(f"[User Intervention] {msg}")

                    self._emit_event("USER_INTERVENTION", process.pid, {"content": msg})
                    logger.info(f"[{process.pid}] Processed inbox message: {msg[:50]}...")

                await self._check_compaction(process)

                step_result = await process.vcpu.step()

                # Handle CONTEXT_OVERFLOW fault - trigger compaction and retry
                if step_result.fault and step_result.fault.code == "CONTEXT_OVERFLOW":
                    ctx = step_result.fault.context or {}
                    logger.info(
                        f"[{process.pid}] Context overflow ({ctx.get('current_tokens')} tokens), "
                        f"triggering compaction..."
                    )
                    self._emit_event(
                        "COMPACTION_TRIGGERED",
                        process.pid,
                        {"current_tokens": ctx.get("current_tokens"), "threshold": ctx.get("threshold")},
                    )
                    success = await self._compaction_for_process(process.pid, process.mmu)
                    if success:
                        logger.info(f"[{process.pid}] Compaction successful, retrying step...")
                        continue  # Retry the step after compaction
                    else:
                        logger.error(f"[{process.pid}] Compaction failed")
                        process.state = "FAILED"
                        return ToolResult(
                            status="ERROR",
                            fault=Fault(
                                domain="MEMORY",
                                code="COMPACTION_FAILED",
                                message="Context overflow but compaction failed",
                            ),
                        )

                if step_result.fault and not step_result.fault.retryable:
                    process.state = "FAILED"
                    return ToolResult(status="ERROR", fault=step_result.fault)

                if step_result.is_final:
                    if process.inbox:
                        logger.info(
                            f"[{process.pid}] New messages arrived during final step, extending execution..."
                        )
                        process.vcpu._is_done = False
                        continue

                    process.state = "SUCCEEDED"
                    final_result = step_result.final_result or ToolResult(
                        status="OK", output="Completed"
                    )
                    break

                await asyncio.sleep(0)

            self._emit_event(
                "PROC_FINISHED",
                process.pid,
                {
                    "state": process.state,
                    "status": final_result.status if final_result else "UNKNOWN",
                },
            )

            return final_result or ToolResult(status="OK")

        except asyncio.CancelledError:
            process.state = "CANCELLED"
            process.result = ToolResult(
                status="CANCELLED",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message="Process was cancelled",
                    retryable=True,
                ),
            )
            raise

        except Exception as e:
            process.state = "FAILED"
            process.result = ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message=str(e),
                    retryable=False,
                ),
            )
            return process.result

    # =========================================================================
    # Tool Management
    # =========================================================================

    def register_tool(
        self,
        name: str,
        func: Callable,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Register a tool.

        Args:
            name: Tool name
            func: Tool function
            description: Tool description
            parameters: Tool parameters schema
        """
        if name in self._tools:
            self._tools.unregister(name)

        if hasattr(func, "_tool_definition"):
            self._tools.register_decorated(func)
        else:
            definition = ToolDefinition(
                name=name,
                description=description or func.__doc__ or "",
                parameters=[],
            )
            self._tools.register(definition, func)

    def unregister_tool(self, name: str) -> bool:
        """Unregister a tool."""
        return bool(self._tools.unregister(name))

    def list_tools(self) -> List[str]:
        """List all registered tools."""
        return self._tools.list_tools()

    # =========================================================================
    # Event & State Access
    # =========================================================================

    def add_event_listener(self, listener: Callable[[Event], Any]) -> None:
        """Add a listener for real-time events."""
        if hasattr(self._events, "add_listener"):
            self._events.add_listener(listener)

    def remove_event_listener(self, listener: Callable[[Event], Any]) -> None:
        """Remove an event listener."""
        if hasattr(self._events, "remove_listener"):
            self._events.remove_listener(listener)

    def get_events(self) -> List[Event]:
        """Get all collected events."""
        return self._events.events.copy()

    def clear_events(self) -> None:
        """Clear collected events."""
        self._events.clear()

    def get_state(self) -> Dict[str, Any]:
        """Get AgentOS state for debugging."""
        return {
            "config": {
                "max_processes": self.config.max_processes,
                "default_timeout": self.config.default_timeout,
            },
            "processes": {
                pid: {
                    "goal": p.goal,
                    "role": p.role,
                    "state": p.state,
                }
                for pid, p in self._processes.items()
            },
            "tools": self._tools.list_tools(),
            "event_count": len(self._events.events),
        }

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _create_mmu(self, pid: str) -> MMU:
        """Create an MMU for a process."""
        mmu = MMU(config=self.config.mmu_config, process_id=pid)

        # Set pinned context
        pinned = PinnedContext(
            system_rules=self.config.system_rules,
            workspace_info=self.config.workspace_info,
            capabilities=self.config.capabilities,
        )
        mmu.set_pinned(pinned)

        return mmu

    def _create_gate(self, pid: str, role: str = "") -> KernelGate:
        """Create a KernelGate for a process."""
        return KernelGate(
            pid=pid,
            tool_executor=self._tools,
            event_stream=self._events,
            default_timeout=self.config.default_timeout,
        )

    def _emit_event(self, event_type: str, pid: str, data: Dict[str, Any]) -> None:
        """Emit an event."""
        self._events.emit(
            Event(
                type=event_type,  # type: ignore
                pid=pid,
                data=data,
            )
        )


# =============================================================================
# Factory Functions
# =============================================================================


def create_agent_os(
    llm_client: LLMClient,
    tools: Optional[Dict[str, Callable]] = None,
    system_rules: str = "",
    max_processes: int = 10,
    default_timeout: float = 300.0,
    workspace: Optional["Path"] = None,
    register_defaults: bool = True,
) -> AgentOS:
    """
    Factory function to create an AgentOS with common defaults.

    Args:
        llm_client: LLM client for vCPUs
        tools: Initial tool registry (additional to defaults)
        system_rules: System rules for all processes
        max_processes: Maximum concurrent processes
        default_timeout: Default execution timeout
        workspace: Workspace path for tool sandboxing
        register_defaults: Whether to register default v2 tools (Read, Glob, etc.)

    Returns:
        Configured AgentOS instance with default tools registered
    """
    from pathlib import Path

    if workspace is None:
        workspace = Path.cwd()

    config = AgentOSConfig(
        max_processes=max_processes,
        default_timeout=default_timeout,
        system_rules=system_rules or AgentOSConfig.system_rules,
        workspace_info=f"Workspace: {workspace}",
    )

    os = AgentOS(llm_client=llm_client, tools=tools, config=config)

    # Register default v2 native tools
    if register_defaults:
        from nimbus.tools import register_default_tools

        register_default_tools(os, workspace=workspace)

    return os
