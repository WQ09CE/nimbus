
import asyncio
import uuid
from typing import Any, Callable, Dict, List, Optional, Literal

from loguru import logger
from nimbus.core.protocol import ToolResult, Fault
from nimbus.core.process.state import Process, ProcessState
from nimbus.core.profile import AgentProfile
from nimbus.core.runtime.vcpu import VCPU

class ProcessManager:
    """Manages the lifecycle and execution of all sub-agent processes."""

    def __init__(self, agent_os):
        self.agent_os = agent_os
        self._processes: Dict[str, Process] = {}

    @property
    def _factory(self):
        return self.agent_os._factory

    @property
    def _events(self):
        return self.agent_os._events

    @property
    def _llm(self):
        return self.agent_os._llm

    @property
    def heart(self):
        return self.agent_os.heart

    def _emit_event(self, *args, **kwargs):
        self.agent_os._emit_event(*args, **kwargs)

    def _ensure_heart_running(self):
        self.agent_os._ensure_heart_running()

    async def _check_compaction(self, *args, **kwargs):
        await self.agent_os._check_compaction(*args, **kwargs)

    async def _compaction_for_process(self, *args, **kwargs):
        return await self.agent_os._compaction_for_process(*args, **kwargs)

    def _nimfs_gc_task(self, *args, **kwargs):
        self.agent_os._nimfs_gc_task(*args, **kwargs)

    def spawn(
            self,
            goal: str,
            role: str = "",
            system_rules: Optional[str] = None,
            max_iterations: Optional[int] = None,
            llm_client: Optional[Any] = None,
            tools_override: Optional[List] = None,
            profile: Optional[AgentProfile] = None,  # NEW
        ) -> str:
            """
            Spawn a new process with the given goal and role.
            
            Args:
                goal: The goal/task for the process
                role: Optional role identifier for the process
                system_rules: Optional custom system rules (overrides kernel default)
                max_iterations: Optional max VCPU iterations (overrides kernel default)
                llm_client: Optional LLM client for this process (overrides kernel default)
                tools_override: Optional explicit tools list. None=inherit from kernel, []=no tools (pure reasoning)
                profile: Optional AgentProfile to configure the process (recommended for v2)
                
            Returns:
                Process ID (pid) of the spawned process
            """
            # Generate unique process ID
            pid = f"proc-{uuid.uuid4().hex[:8]}"
    
            # --- Profile Resolution ---
            _role = role
            _system_rules = system_rules
            _max_iterations = max_iterations
            _tools_filter = tools_override
    
            if profile:
                _role = profile.role or _role
                _system_rules = profile.system_prompt or _system_rules
                _max_iterations = profile.max_iterations or _max_iterations
                # If profile defines allowed tools, treat them as a filter/override
                # Note: This logic assumes tools are already registered in kernel.
                # Phase 3 will handle dynamic tool loading from profile.
                if profile.allowed_tools:
                    _tools_filter = profile.allowed_tools
    
            # Default to "standard" if no role was resolved from profile or caller
            if not _role:
                _role = "standard"
    
            # Extract write_filter from profile
            _write_filter = None
            if profile and profile.write_filter:
                _write_filter = profile.write_filter
    
            # Extract is_interactive / text_is_final from profile
            _is_interactive = False
            _text_is_final = True
            if profile:
                _is_interactive = profile.is_interactive
                _text_is_final = profile.text_is_final
    
            # Create process via factory (unified component assembly)
            process = self._factory.build(
                pid=pid,
                goal=goal,
                role=_role,
                system_rules=_system_rules,
                llm_client=llm_client,
                profile=profile,
                tools_override=_tools_filter,
                max_iterations=_max_iterations,
                write_filter=_write_filter,
                enable_ipc=True,
                agent_os=self,
                is_interactive=_is_interactive,
                text_is_final=_text_is_final,
            )
    
            # Register process
            self._processes[pid] = process
    
            # Emit spawn event
            manifest = process.vcpu.manifest
            _manifest_model = getattr(manifest, "model_id", "") or ""
            _model_short = _manifest_model.split("/")[-1] if "/" in _manifest_model else _manifest_model
    
            self._emit_event("PROC_SPAWNED", pid, {"goal": goal, "role": _role, "model": _model_short, "model_full": _manifest_model})
    
            return pid

    async def wait(self, pid: str, timeout: Optional[float] = None) -> ToolResult:
            """Wait for a process to complete and return its result.
    
            Args:
                pid: Process ID to wait for
                timeout: Optional timeout in seconds
    
            Returns:
                ToolResult from the process
    
            Raises:
                RuntimeError: If process not found
            """
            process = self._processes.get(pid)
            if not process:
                raise RuntimeError(f"Process {pid} not found")
    
            def _timeout_result() -> ToolResult:
                process.state = "CANCELLED"
                # Get last messages for post-mortem
                post_mortem = []
                if process.mmu:
                    try:
                        post_mortem = process.mmu.get_last_messages(3)
                    except Exception:
                        pass
                        
                return ToolResult(
                    status="TIMEOUT",
                    output={"post_mortem": post_mortem},
                    is_final=True,
                    fault=Fault(
                        domain="KERNEL",
                        code="TIMEOUT",
                        message=f"Process {pid} timed out after {timeout}s",
                    ),
                )
    
            async def _run_with_soft_timeout(coro):
                """Two-phase timeout: soft signal at 85%, hard kill in remaining 15%."""
                if not timeout:
                    return await coro
    
                # Phase durations
                t_soft = timeout * 0.85
                t_finalize = min(max(timeout * 0.15, 30.0), 60.0)
    
                task = asyncio.ensure_future(coro)
    
                # Phase 1: wait for soft timeout
                try:
                    return await asyncio.wait_for(asyncio.shield(task), timeout=t_soft)
                except asyncio.TimeoutError:
                    # Signal the vCPU to finalize with partial results
                    if process.vcpu:
                        process.vcpu.signals["soft_timeout"] = True
                    # The process.signals dict is removed, so this line is removed.
                    # process.signals["soft_timeout"] = True
                    logger.warning(
                        f"Process {pid} hit soft timeout at {t_soft:.0f}s, "
                        f"giving {t_finalize:.0f}s to finalize"
                    )
    
                # Phase 2: wait for finalize window
                try:
                    return await asyncio.wait_for(task, timeout=t_finalize)
                except asyncio.TimeoutError:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                    raise asyncio.TimeoutError()
    
            # If process is pending, start it
            if process.state == "PENDING":
                self._ensure_heart_running()
                process.state = "RUNNING"
                try:
                    return await _run_with_soft_timeout(self._run_process(process))
                except asyncio.TimeoutError:
                    return _timeout_result()
    
            # If process is already running with a task, wait for it
            if process.task and not process.task.done():
                try:
                    return await _run_with_soft_timeout(process.task)
                except asyncio.TimeoutError:
                    return _timeout_result()
    
            # If process is completed, return the result
            if process.state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                return process.result or ToolResult(status="OK")
    
            # Otherwise, run the process
            process.state = "RUNNING"
            try:
                return await _run_with_soft_timeout(self._run_process(process))
            except asyncio.TimeoutError:
                return _timeout_result()

    async def wait_all(
            self,
            pids: List[str],
            timeout: Optional[float] = None,
        ) -> Dict[str, "ToolResult"]:
            """
            Wait for multiple processes to complete in parallel.
    
            Args:
                pids: List of process IDs to wait for
                timeout: Optional timeout in seconds (applies to each process)
    
            Returns:
                Dict mapping pid -> ToolResult
            """
            from nimbus.core.protocol import Fault
    
            async def _safe_wait(pid: str) -> tuple:
                try:
                    result = await self.wait(pid, timeout=timeout)
                    return pid, result
                except Exception as e:
                    return pid, ToolResult(
                        status="ERROR",
                        fault=Fault(domain="KERNEL", code="WAIT_FAIL", message=str(e)),
                    )
    
            tasks = [_safe_wait(pid) for pid in pids]
            completed = await asyncio.gather(*tasks)
            return dict(completed)

    async def run(self, goal: str, role: str = "") -> ToolResult:
            pid = self.spawn(goal, role=role)
            return await self.wait(pid)

    async def run_stream(self, goal: str, role: str = ""):
            from nimbus.core.process.loop import RuntimeLoop
    
            pid = self.spawn(goal, role=role)
            process = self._processes.get(pid)
            if not process:
                yield {"type": "error", "message": f"Process {pid} not found"}
                return
    
            async def transform_context_hook(ctx) -> None:
                self._drain_process_inbox(process)
                await self._check_compaction(process)
    
            if process.vcpu:
                process.vcpu.transform_context_hook = transform_context_hook
    
            loop = RuntimeLoop(
                process=process,
                compaction_fn=self._compaction_for_process,
                check_compaction_fn=self._check_compaction,
                heart=self.heart,
                emit_event_fn=self._emit_event,
                nimfs_gc_fn=self._nimfs_gc_task,
                scavenge_fn=self._scavenge_partial_result,
            )
            async for event in loop.stream():
                yield event

    def terminate(self, pid: str, reason: str = "manual_terminate") -> bool:
            """
            Safely stop a running process.
            """
            process = self._processes.get(pid)
            if not process:
                return False
    
            if process.state == "RUNNING":
                process.state = "CANCELLED"
                if process.vcpu:
                    process.vcpu.signals["hard_timeout"] = True
                
                if process.task and not process.task.done():
                    process.task.cancel()
                
                logger.info(f"[AgentOS] Process {pid} terminated. Reason: {reason}")
                self._emit_event("PROC_TERMINATED", pid, {"reason": reason})
                return True
            return False

    def list_processes(self) -> List[str]:
            """List all active process IDs."""
            return list(self._processes.keys())

    def get_active_processes(self) -> List[str]:
            """Get list of currently running process IDs."""
            return [pid for pid, p in self._processes.items() if p.state == "RUNNING"]

    def get_process(self, pid: str) -> "Process | None":
            """Get a process by ID."""
            return self._processes.get(pid)

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
                    process.interrupt_event.set()
                    signalled = True
                    logger.info(f"[{pid}] Interrupt event set")
    
            return signalled

    def inject_message(self, pid: str, message: "str | list") -> bool:
            """Inject a user message into a running process's inbox.
    
            Returns True if message was accepted (process is RUNNING and will consume it).
            Returns False if process doesn't exist or is not RUNNING.
            When False is returned, inbox is NOT modified — caller is responsible for
            persisting the message via storage/MMU directly.
            """
            process = self._processes.get(pid)
            if not process:
                return False
    
            if process.state != "RUNNING":
                logger.warning(f"[{pid}] Process not running (state={process.state}), cannot inject")
                return False
    
            if isinstance(message, str):
                # Backwards compatibility: Wrap simple strings as 'user' interventions
                from nimbus.core.ipc.message import IPCMessage
                msg_obj = IPCMessage(
                    sender_pid="user",
                    target_pid=pid,
                    type="event",
                    payload={"content": message}
                )
                process.inbox.append(msg_obj)
            else:
                # Full IPCMessage injection
                process.inbox.append(message)
    
            preview = str(message)[:50]
            logger.info(f"[{pid}] Message injected into inbox: {preview}...")
            return True

    def _drain_process_inbox(self, process: Process) -> None:
            """Drain inbox messages (IPC messages + plain strings).
    
            Inbox may be a Mailbox (from spawn) or list (from chat) -- duck typed.
            """
            import logging
            logger = logging.getLogger("kernel.os")
    
            while process.inbox:
                if hasattr(process.inbox, "qsize"):
                    # Mailbox (from spawn)
                    if process.inbox.qsize() == 0:
                        break
                    try:
                        msg = process.inbox._queue.get_nowait()
                    except Exception:
                        break
                else:
                    # List (from chat)
                    msg = process.inbox.pop(0)
    
                if not msg:
                    break
    
                # Handle both IPCMessage objects and plain strings
                if hasattr(msg, "type") and hasattr(msg, "payload"):
                    # IPCMessage from inject_message() or IPC system
                    if msg.type == "request":
                        task_goal = msg.payload.get("goal", "")
                        process.mmu.add_user_message(f"[Task Assignment] {task_goal}")
                    elif msg.type == "response":
                        result_data = msg.payload.get("result", "")
                        process.mmu.add_user_message(
                            f"[Sub-Agent Result from {msg.sender_pid}] {result_data}"
                        )
                    else:
                        content = msg.payload.get("content", str(msg.payload))
                        if process.is_interactive:
                            process.mmu.add_user_message(content)
                        else:
                            process.mmu.add_user_message(f"[User Intervention] {content}")
    
                    self._emit_event(
                        "USER_INTERVENTION", process.pid, {"content": str(msg.payload)}
                    )
                    logger.info(
                        f"[{process.pid}] Processed inbox message {msg.id} from {msg.sender_pid}"
                    )
                else:
                    # Plain string from _handle_interventions() or legacy code
                    content = str(msg)
                    if process.is_interactive:
                        process.mmu.add_user_message(content)
                    else:
                        process.mmu.add_user_message(f"[User Intervention] {content}")
                    self._emit_event(
                        "USER_INTERVENTION", process.pid, {"content": content}
                    )
                    logger.info(
                        f"[{process.pid}] Processed inbox string message: {content[:50]}..."
                    )

    async def _run_process(self, process: Process) -> ToolResult:
            from nimbus.core.process.loop import RuntimeLoop
    
            self._ensure_heart_running()
    
            async def transform_context_hook(ctx) -> None:
                self._drain_process_inbox(process)
                await self._check_compaction(process)
    
            if process.vcpu:
                process.vcpu.transform_context_hook = transform_context_hook
    
            loop = RuntimeLoop(
                process=process,
                compaction_fn=self._compaction_for_process,
                check_compaction_fn=self._check_compaction,
                heart=self.heart,
                emit_event_fn=self._emit_event,
                nimfs_gc_fn=self._nimfs_gc_task,
                scavenge_fn=self._scavenge_partial_result,
            )
            return await loop.run()

    def _scavenge_partial_result(self, process: Process) -> ToolResult:
            """
            Scavenge partial results from a timed-out process before it is destroyed.
    
            Accesses the process's current MMU frame, extracts any internal monologue
            (assistant messages) and artifact references produced so far, and packages
            them into a ToolResult with ``is_partial=True`` so callers can distinguish
            salvaged data from a normal completion.
    
            Also implements 'Post-Mortem' analysis: captures last 3 messages as diagnosis context.
    
            Args:
                process: The timed-out Process whose frame will be inspected.
    
            Returns:
                ToolResult with status="TIMEOUT", is_partial=True, and whatever
                partial data could be recovered.
            """
            try:
                frame = process.mmu.current_frame if process.mmu else None
                internal_monologue: List[str] = []
                artifacts = []
                post_mortem_context = []
    
                if frame is not None:
                    # Post-Mortem: capture last 3 messages
                    all_msgs = frame.messages
                    last_msgs = all_msgs[-3:] if len(all_msgs) >= 3 else all_msgs
                    for m in last_msgs:
                        post_mortem_context.append({
                            "role": m.role,
                            "content": str(m.content)[:500] + "..." if m.content and len(str(m.content)) > 500 else m.content
                        })
    
                    for msg in frame.messages:
                        if msg.role == "assistant" and msg.content:
                            internal_monologue.append(msg.content)
                        # Collect any artifact refs stored in message metadata
                        if hasattr(msg, "meta") and msg.meta:
                            for ref in msg.meta.get("artifacts", []):
                                artifacts.append(ref)
    
                partial_output = {
                    "is_partial": True,
                    "pid": process.pid,
                    "goal": process.goal,
                    "internal_monologue": internal_monologue,
                    "salvaged_artifacts": artifacts,
                    "frame_id": frame.frame_id if frame else None,
                    "post_mortem": post_mortem_context
                }
    
                logger.info(
                    f"[scavenge] PID={process.pid} salvaged "
                    f"{len(internal_monologue)} thought(s), "
                    f"{len(artifacts)} artifact(s)."
                )
    
                return ToolResult(
                    status="TIMEOUT",
                    output=partial_output,
                    is_final=False,
                    fault=Fault(
                        domain="KERNEL",
                        code="TIMEOUT",
                        message=f"Process {process.pid} timed out; post-mortem diagnostics attached.",
                        retryable=True,
                    ),
                )
    
            except Exception as exc:  # pragma: no cover – best-effort scavenge
                logger.warning(f"[scavenge] Failed to salvage partial result for PID={process.pid}: {exc}")
                return ToolResult(
                    status="TIMEOUT",
                    output={"is_partial": True, "pid": process.pid, "goal": process.goal},
                    is_final=False,
                    fault=Fault(
                        domain="KERNEL",
                        code="TIMEOUT",
                        message=f"Process {process.pid} timed out; scavenge also failed: {exc}",
                        retryable=True,
                    ),
                )

    async def spawn_batch(
            self,
            tasks: List[Dict[str, Any]],
            timeout: Optional[float] = None,
            strategy: Literal["wait_all", "wait_any", "wait_threshold"] = "wait_all",
            threshold: float = 0.6,
            parent_action_id: Optional[str] = None,
            specialist_names: Optional[List[str]] = None,
            original_indices: Optional[List[int]] = None,
        ) -> List[ToolResult]:
            """
            Spawn and run multiple sub-processes concurrently (parallel dispatch).
    
            Each entry in *tasks* is a dict accepted by :meth:`spawn` – at minimum
            it must contain a ``"goal"`` key.  Every process is given its own
            ``sub_session_id`` (equal to its ``pid``) so SSE events can be filtered
            per sub-task.
    
            Args:
                tasks:    List of task dicts.  Supported keys mirror :meth:`spawn`
                          parameters: ``goal``, ``role``, ``system_rules``,
                          ``max_iterations``, ``llm_client``, ``tools_override``,
                          ``profile``.
                timeout:  Per-task wall-clock timeout in seconds.  When a task
                          exceeds this limit ``asyncio.wait_for`` raises
                          ``asyncio.TimeoutError`` which triggers
                          :meth:`_scavenge_partial_result` inside
                          :meth:`_run_process`.
                strategy: Aggregation strategy:
                          - ``"wait_all"``       – wait for every task (default).
                          - ``"wait_any"``       – return as soon as the first
                            task finishes; cancel the rest.
                          - ``"wait_threshold"`` – return when at least
                            ``threshold`` fraction of tasks have finished.
                threshold: Fraction of tasks that must complete for
                           ``"wait_threshold"`` strategy (default 0.6).
    
            Returns:
                List of :class:`ToolResult` in the same order as *tasks*.
                Failed / timed-out entries contain the salvaged partial result.
            """
            if not tasks:
                return []
    
            batch_id = f"batch-{uuid.uuid4().hex[:8]}"
            logger.info(
                f"[spawn_batch] batch_id={batch_id} launching {len(tasks)} tasks "
                f"strategy={strategy} timeout={timeout}"
            )
    
            # 1. Spawn all processes (synchronous, non-blocking).
            pids: List[str] = []
            for i, task_spec in enumerate(tasks):
                goal = task_spec.get("goal", "")
                if not goal:
                    raise ValueError(f"tasks[{i}] is missing required 'goal' key")
    
                pid = self.spawn(
                    goal=goal,
                    role=task_spec.get("role", ""),
                    system_rules=task_spec.get("system_rules"),
                    max_iterations=task_spec.get("max_iterations"),
                    llm_client=task_spec.get("llm_client"),
                    tools_override=task_spec.get("tools_override"),
                    profile=task_spec.get("profile"),
                )
                pids.append(pid)
                # Tag the process so SSE listeners can filter by sub_session_id
                proc = self._processes[pid]
                proc.signals["batch_id"] = batch_id          # type: ignore[assignment]
                proc.signals["sub_session_id"] = pid         # type: ignore[assignment]
                # Batch routing metadata for frontend (parallel native calls)
                if parent_action_id:
                    proc.signals["parent_action_id"] = parent_action_id  # type: ignore[assignment]
                slot_index = original_indices[i] if original_indices and i < len(original_indices) else i
                proc.signals["batch_slot_index"] = slot_index  # type: ignore[assignment]
                if specialist_names and i < len(specialist_names):
                    proc.signals["specialist"] = specialist_names[i]  # type: ignore[assignment]
                self._emit_event(
                    "BATCH_TASK_SPAWNED",
                    pid,
                    {"batch_id": batch_id, "sub_session_id": pid, "index": i, "goal": goal},
                )
    
            # 2. Build coroutines with per-task timeout wrapping.
            async def _run_with_timeout(pid: str) -> ToolResult:
                proc = self._processes[pid]
                coro = self._run_process(proc)
                if timeout is not None:
                    try:
                        return await asyncio.wait_for(coro, timeout=timeout)
                    except asyncio.TimeoutError:
                        # asyncio.wait_for cancelled the inner coroutine; scavenge now.
                        proc.state = "TIMEOUT"
                        partial_result = self._scavenge_partial_result(proc)
                        asyncio.create_task(self.heart.inbox.put(
                            topic="session.timeout",
                            payload={
                                "session_id": pid,
                                "error": "Process timed out (spawn_batch)",
                                "partial_salvaged": partial_result.output is not None,
                            },
                        ))
                        self._emit_event(
                            "PROC_TIMEOUT",
                            pid,
                            {"batch_id": batch_id, "partial_salvaged": partial_result.output is not None},
                        )
                        proc.result = partial_result
                        return partial_result
                return await coro
    
            coroutines = [_run_with_timeout(pid) for pid in pids]
    
            # 3. Execute according to strategy.
            results: List[Optional[ToolResult]] = [None] * len(pids)
    
            if strategy == "wait_any":
                # asyncio.wait FIRST_COMPLETED – cancel remaining tasks
                aws = {asyncio.ensure_future(c): i for i, c in enumerate(coroutines)}
                done, pending = await asyncio.wait(
                    list(aws.keys()), return_when=asyncio.FIRST_COMPLETED
                )
                for fut in done:
                    idx = aws[fut]
                    try:
                        results[idx] = fut.result()
                    except Exception as exc:
                        results[idx] = ToolResult(
                            status="ERROR",
                            fault=Fault(domain="KERNEL", code="SYSTEM_ERROR", message=str(exc)),
                        )
                for fut in pending:
                    fut.cancel()
                    idx = aws[fut]
                    results[idx] = ToolResult(
                        status="CANCELLED",
                        fault=Fault(domain="KERNEL", code="SYSTEM_ERROR", message="Cancelled by wait_any strategy"),
                    )
    
            elif strategy == "wait_threshold":
                need = max(1, int(len(pids) * threshold))
                aws = {asyncio.ensure_future(c): i for i, c in enumerate(coroutines)}
                completed = 0
                pending = set(aws.keys())
                while completed < need and pending:
                    done, pending = await asyncio.wait(
                        list(pending), return_when=asyncio.FIRST_COMPLETED
                    )
                    for fut in done:
                        idx = aws[fut]
                        completed += 1
                        try:
                            results[idx] = fut.result()
                        except Exception as exc:
                            results[idx] = ToolResult(
                                status="ERROR",
                                fault=Fault(domain="KERNEL", code="SYSTEM_ERROR", message=str(exc)),
                            )
                # Cancel remaining if threshold reached
                if completed >= need:
                    for fut in pending:
                        fut.cancel()
                        idx = aws[fut]
                        results[idx] = ToolResult(
                            status="CANCELLED",
                            fault=Fault(domain="KERNEL", code="SYSTEM_ERROR", message="Cancelled after threshold reached"),
                        )
    
            else:  # wait_all (default)
                gathered = await asyncio.gather(*coroutines, return_exceptions=True)
                for i, res in enumerate(gathered):
                    if isinstance(res, Exception):
                        results[i] = ToolResult(
                            status="ERROR",
                            fault=Fault(domain="KERNEL", code="SYSTEM_ERROR", message=str(res)),
                        )
                    else:
                        results[i] = res  # type: ignore[assignment]
    
            self._emit_event(
                "BATCH_FINISHED",
                batch_id,
                {
                    "batch_id": batch_id,
                    "total": len(pids),
                    "succeeded": sum(1 for r in results if r and r.status == "OK"),
                    "failed": sum(1 for r in results if r and r.status in ("ERROR", "TIMEOUT", "CANCELLED")),
                },
            )
    
            logger.info(
                f"[spawn_batch] batch_id={batch_id} finished. "
                f"results={[r.status if r else 'None' for r in results]}"
            )
    
            return [r or ToolResult(status="ERROR") for r in results]

