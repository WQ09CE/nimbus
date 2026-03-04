"""
RuntimeLoop - Unified process execution loop.

Replaces both AgentOS._run_process() and AgentOS.run_stream() with a single
implementation that supports both sync and streaming consumption modes.

Key improvement: single step() per iteration (fixes the double-step FSM leak bug).
In the old design, _run_process called vcpu.step() twice per loop iteration:
  1. First call with interrupt support -- result discarded
  2. Second call without interrupt support -- result used
This caused FSM state leakage and empty output bugs.

New design: _step_with_interrupt() calls step() exactly once, and the result
is used for all subsequent logic (heart report, fault handling, is_final, etc.).
"""

import asyncio
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from loguru import logger

from nimbus.core.protocol import Event, Fault, StepResult, ToolResult


class RuntimeLoop:
    """Unified process execution loop.

    Supports two consumption modes:
    - run(): synchronous, returns final ToolResult
    - stream(): async generator, yields step events

    Both modes share the same core loop logic via _loop().
    """

    def __init__(
        self,
        process,                  # Process dataclass
        compaction_fn,            # async (pid, mmu) -> bool
        check_compaction_fn,      # async (process) -> None
        heart,                    # Heart daemon
        emit_event_fn,            # (type, pid, data) -> None
        nimfs_gc_fn,              # (process) -> None
        scavenge_fn,              # (process) -> ToolResult
    ):
        self._process = process
        self._compaction_fn = compaction_fn
        self._check_compaction_fn = check_compaction_fn
        self._heart = heart
        self._emit_event = emit_event_fn
        self._nimfs_gc = nimfs_gc_fn
        self._scavenge = scavenge_fn

    # =========================================================================
    # Public API
    # =========================================================================

    async def run(self) -> ToolResult:
        """Run process to completion (sync mode). Replaces _run_process()."""
        process = self._process

        try:
            if process.vcpu is None:
                raise RuntimeError("Process has no VCPU")

            # Prolog: reset VCPU and pin goal if needed
            if not process.vcpu.is_running:
                process.vcpu._reset()
                process.vcpu._is_active = True

                if process.vcpu.config.pin_goal and not process.is_interactive:
                    pinned_goal = await process.vcpu._prepare_goal_for_pinning(process.goal)
                    process.mmu.pin_user_goal(pinned_goal)
                    process.mmu.add_user_message(process.goal)

            final_result = None

            async for event in self._loop():
                etype = event.get("type")
                if etype == "done":
                    final_result = event.get("result")
                elif etype == "interrupted":
                    return ToolResult(
                        status="TIMEOUT",
                        is_final=True,
                        fault=Fault(
                            domain="KERNEL",
                            code="TIMEOUT",
                            message="Interrupted or Timed out",
                        ),
                        output={"post_mortem": process.mmu.get_last_messages(3)}
                    )
                elif etype == "error":
                    # Check if the error is actually a TIMEOUT reported as error
                    res = event.get("result")
                    if res and res.status == "TIMEOUT":
                        return res
                    return event.get("result", ToolResult(status="ERROR"))

            # Epilog
            self._emit_event(
                "PROC_FINISHED",
                process.pid,
                {
                    "state": process.state,
                    "status": final_result.status if final_result else "UNKNOWN",
                },
            )

            # NimFS GC: clean up TASK-level artifacts when a sub-process finishes
            self._nimfs_gc(process)

            return final_result or ToolResult(status="OK")

        except asyncio.TimeoutError:
            # Timeout: attempt to scavenge partial results before destroying the process
            process.state = "TIMEOUT"
            partial_result = self._scavenge(process)
            asyncio.create_task(
                self._heart.inbox.put(
                    topic="session.timeout",
                    payload={
                        "session_id": process.pid,
                        "error": "Process timed out",
                        "partial_salvaged": partial_result.output is not None,
                    },
                )
            )
            self._emit_event(
                "PROC_TIMEOUT",
                process.pid,
                {"partial_salvaged": partial_result.output is not None},
            )
            process.result = partial_result
            return partial_result

        except asyncio.CancelledError:
            # Reset process state
            process.state = "CANCELLED"
            process.interrupt_event.clear()
            asyncio.create_task(
                self._heart.inbox.put(
                    topic="session.timeout",
                    payload={
                        "session_id": process.pid,
                        "error": "Process was cancelled/timed out",
                    },
                )
            )
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
            asyncio.create_task(
                self._heart.inbox.put(
                    topic="session.error",
                    payload={"session_id": process.pid, "error": str(e)},
                )
            )
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

    async def stream(self) -> AsyncIterator[dict]:
        """Stream mode, replacing run_stream().

        Yields events in the same format as the original run_stream():
        - {"type": "planning", "content": "Starting execution..."}
        - {"type": "tool_call", "name": ..., "args": ..., "action_id": ...}
        - {"type": "tool_result", "name": ..., "output": ..., "status": ...}
        - {"type": "text", "content": ...}
        - {"type": "compaction", "message": ...}
        - {"type": "compaction_done", "message": ...}
        - {"type": "done", "result": {"status": ..., "output": ...}}
        - {"type": "error", "message": ...}
        """
        process = self._process
        vcpu = process.vcpu
        mmu = process.mmu

        # Goal setup (matching original run_stream behavior)
        if vcpu.config.pin_goal:
            pinned_goal = await vcpu._prepare_goal_for_pinning(process.goal)
            mmu.pin_user_goal(pinned_goal)
        mmu.add_user_message(process.goal)

        yield {"type": "planning", "content": "Starting execution..."}

        async for event in self._loop():
            etype = event.get("type")

            if etype == "compaction":
                yield event
                continue

            if etype == "compaction_done":
                yield event
                continue

            if etype == "interrupted":
                yield {"type": "error", "message": "Interrupted by user"}
                return

            if etype == "error":
                fault = event.get("fault")
                yield {"type": "error", "message": str(fault) if fault else "Unknown error"}
                return

            if etype == "done":
                result = event.get("result")
                if result:
                    yield {
                        "type": "done",
                        "result": {
                            "status": result.status if hasattr(result, "status") else "OK",
                            "output": result.output if hasattr(result, "output") else result,
                            "error": str(result.fault) if hasattr(result, "fault") and result.fault else None,
                        },
                    }
                else:
                    yield {"type": "done", "result": {"status": "OK", "output": None}}
                return

            if etype == "step":
                # Transform step result into stream events
                step_result = event.get("step_result")
                if step_result:
                    for stream_event in self._step_to_stream_events(step_result):
                        yield stream_event

            if etype == "budget_summary":
                # The final summary after budget exceeded -- emit as done
                result = event.get("result")
                yield {
                    "type": "done",
                    "result": {
                        "status": result.status if result else "OK",
                        "output": result.output if result else None,
                    },
                }
                return

    # =========================================================================
    # Core Loop
    # =========================================================================

    async def _loop(self) -> AsyncIterator[dict]:
        """Core execution loop -- yields step events, drives all logic.

        This is an async generator that yields event dicts. Both run() and
        stream() consume this generator.

        Event types yielded:
        - {"type": "step", "step_result": StepResult}  -- normal step completed
        - {"type": "done", "result": ToolResult}        -- final result
        - {"type": "interrupted"}                       -- user interrupt
        - {"type": "error", "result": ToolResult, "fault": Fault}  -- fatal error
        - {"type": "compaction", "message": str}        -- compaction started
        - {"type": "compaction_done", "message": str}   -- compaction completed
        - {"type": "budget_summary", "result": ToolResult}  -- budget exceeded summary
        """
        process = self._process
        vcpu = process.vcpu

        while vcpu._is_active:
            # ----------------------------------------------------------
            # 1. Single step with interrupt support
            # ----------------------------------------------------------
            step_result = await self._step_with_interrupt()
            if step_result is None:
                # Interrupted by user
                process.state = "CANCELLED"
                process.interrupt_event.clear()
                vcpu._is_active = False
                yield {"type": "interrupted"}
                return

            # ----------------------------------------------------------
            # 2. Heart report (session.iteration)
            # ----------------------------------------------------------
            self._report_to_heart(step_result)

            # ----------------------------------------------------------
            # 3. Drain inbox (IPC messages + plain strings)
            # ----------------------------------------------------------
            self._drain_inbox()

            # ----------------------------------------------------------
            # 4. Proactive compaction check
            # ----------------------------------------------------------
            await self._check_compaction_fn(process)

            # ----------------------------------------------------------
            # 5. Handle CONTEXT_OVERFLOW fault -- compact and retry
            # ----------------------------------------------------------
            if step_result.fault and step_result.fault.code == "CONTEXT_OVERFLOW":
                handled = await self._handle_context_overflow(step_result)
                if handled == "retry":
                    continue
                else:
                    # handled is a ToolResult with error
                    yield {"type": "error", "result": handled, "fault": handled.fault}
                    return

            # ----------------------------------------------------------
            # 6. Non-retryable fault
            # ----------------------------------------------------------
            if step_result.fault and not step_result.fault.retryable:
                if step_result.status == "TIMEOUT":
                    process.state = "CANCELLED"
                    yield {
                        "type": "error",
                        "result": step_result.final_result or ToolResult(status="TIMEOUT", fault=step_result.fault),
                        "fault": step_result.fault,
                    }
                else:
                    process.state = "FAILED"
                    yield {
                        "type": "error",
                        "result": ToolResult(status="ERROR", fault=step_result.fault),
                        "fault": step_result.fault,
                    }
                return

            # ----------------------------------------------------------
            # 7. is_final handling
            # ----------------------------------------------------------
            if step_result.is_final:
                # Only extend execution for chat processes where a user
                # may have sent a new message while the agent was finishing.
                # Sub-agent processes (explorer, implementer, etc.) should
                # terminate immediately -- they have no interactive user.
                if process.is_interactive and process.inbox:
                    logger.info(
                        f"[{process.pid}] Chat process got new user message "
                        f"during final step, extending execution..."
                    )
                    # Reset FSM state so the agent can continue processing
                    # the new message. vcpu.is_done is a property based on
                    # isinstance(_current_state, StateCompleted), so we
                    # reset back to StateInit.
                    from nimbus.core.runtime.states import StateInit

                    process.vcpu._is_active = True
                    process.vcpu._current_state = StateInit()
                    process.vcpu._fsm_ctx.final_result = None
                    continue

                process.state = "SUCCEEDED"
                final_result = step_result.final_result or ToolResult(
                    status="OK", output="Completed"
                )
                yield {"type": "done", "result": final_result}
                return

            # ----------------------------------------------------------
            # 8. Iteration limit check (OS level, AFTER step)
            # ----------------------------------------------------------
            if vcpu.iteration >= vcpu.config.max_iterations:
                result = await self._handle_iteration_limit()
                if result == "compacted":
                    continue
                else:
                    # result is a ToolResult with the summary
                    yield {"type": "budget_summary", "result": result}
                    return

            # ----------------------------------------------------------
            # 9. Yield step events for streaming
            # ----------------------------------------------------------
            yield {"type": "step", "step_result": step_result}

            await asyncio.sleep(0)

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    async def _step_with_interrupt(self) -> Optional[StepResult]:
        """Execute a single vcpu.step() with interrupt support.

        Returns:
            StepResult if step completed normally, None if interrupted.
        """
        process = self._process

        step_task = asyncio.create_task(process.vcpu.step())
        interrupt_task = asyncio.create_task(process.interrupt_event.wait())

        done, pending = await asyncio.wait(
            [step_task, interrupt_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if interrupt_task in done:
            # User clicked stop mid-step -- cancel the VCPU execution cleanly
            step_task.cancel()
            logger.info(f"[{process.pid}] Step cancelled concurrently by interrupt event")
            return None
        else:
            # Cancel the unused interrupt watcher
            interrupt_task.cancel()

        return step_task.result()

    def _report_to_heart(self, step_result: StepResult) -> None:
        """Report iteration to Heart for stall detection."""
        process = self._process

        has_output = False
        if step_result.final_result and step_result.final_result.output:
            has_output = True
        elif step_result.results:
            has_output = any(getattr(r, "output", None) for r in step_result.results)

        asyncio.create_task(
            self._heart.inbox.put(
                topic="session.iteration",
                payload={
                    "session_id": process.pid,
                    "iteration": process.vcpu.iteration,
                    "has_output": has_output,
                },
            )
        )

    def _drain_inbox(self) -> None:
        """Drain inbox messages (IPC messages + plain strings).

        Inbox may be a Mailbox (from spawn) or list (from chat) -- duck typed.
        """
        process = self._process

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

    async def _handle_context_overflow(self, step_result: StepResult):
        """Handle CONTEXT_OVERFLOW fault by compacting and retrying.

        Returns:
            "retry" if compaction succeeded and the step should be retried.
            ToolResult with error if compaction failed or was ineffective.
        """
        process = self._process
        ctx = step_result.fault.context or {}
        overflow_tokens = ctx.get("current_tokens") or 0

        logger.info(
            f"[{process.pid}] Context overflow ({overflow_tokens} tokens), "
            f"triggering compaction..."
        )
        self._emit_event(
            "COMPACTION_TRIGGERED",
            process.pid,
            {"current_tokens": overflow_tokens, "threshold": ctx.get("threshold")},
        )

        # Measure tokens before compaction to verify effectiveness
        tokens_before = process.mmu.estimate_tokens()
        success = await self._compaction_fn(process.pid, process.mmu)
        tokens_after = process.mmu.estimate_tokens()

        if success and tokens_after < tokens_before * 0.8:
            pct = (
                f"-{100 - tokens_after * 100 // tokens_before}%"
                if tokens_before > 0
                else f"freed {tokens_before - tokens_after}"
            )
            logger.info(
                f"[{process.pid}] Compaction effective: "
                f"{tokens_before} -> {tokens_after} tokens "
                f"({pct}), retrying step..."
            )
            return "retry"
        else:
            reason = (
                f"tokens {tokens_before} -> {tokens_after} (insufficient reduction)"
                if success
                else "compaction returned failure"
            )
            logger.error(f"[{process.pid}] Compaction ineffective: {reason}")
            process.state = "FAILED"
            asyncio.create_task(
                self._heart.inbox.put(
                    topic="session.failure",
                    payload={
                        "session_id": process.pid,
                        "error": f"Compaction ineffective: {reason}",
                    },
                )
            )
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="MEMORY",
                    code="COMPACTION_FAILED",
                    message=f"Context overflow and compaction ineffective: {reason}",
                ),
            )

    async def _handle_iteration_limit(self):
        """Handle iteration limit reached.

        Returns:
            "compacted" if compaction succeeded and the loop should continue.
            ToolResult with summary if budget is exhausted.
        """
        process = self._process
        vcpu = process.vcpu

        if (
            vcpu.config.compact_on_limit
            and vcpu._state.compaction_count < vcpu.config.max_compactions
        ):
            # Compact context and continue (within compaction budget)
            compacted = await self._compaction_fn(process.pid, process.mmu)
            if compacted:
                vcpu._state.compaction_count += 1
                logger.info(
                    f"[{process.pid}] Compaction #{vcpu._state.compaction_count}/"
                    f"{vcpu.config.max_compactions} complete, "
                    f"resetting iteration counter (was {vcpu.iteration})"
                )
                vcpu._state.iteration_count = 0
                # Reset FSM state so the agent can continue after compaction.
                # Without this reset, the while-loop exits immediately if
                # step() transitioned to StateCompleted and set _is_active=False.
                from nimbus.core.runtime.states import StateInit

                process.vcpu._is_active = True
                process.vcpu._current_state = StateInit()
                process.vcpu._fsm_ctx.final_result = None
                return "compacted"
            # Compaction failed -- fall through to budget exceeded
            logger.warning(f"[{process.pid}] Compaction failed, stopping process")

        # Budget exceeded (or compaction disabled/failed)
        # Give the LLM one final step to summarize what it did
        logger.info(
            f"[{process.pid}] Iteration budget reached "
            f"({vcpu.iteration}/{vcpu.config.max_iterations}), "
            f"requesting final summary..."
        )
        process.mmu.add_user_message(
            "[SYSTEM] You have reached your iteration limit. "
            "Do NOT call any more tools. Immediately respond with a summary of: "
            "1) what you completed, 2) what remains unfinished."
        )
        # Reset FSM so the final step actually processes the summary prompt.
        from nimbus.core.runtime.states import StateInit

        process.vcpu._is_active = True
        process.vcpu._current_state = StateInit()
        process.vcpu._fsm_ctx.final_result = None

        # Run one final step for the summary
        final_step = await process.vcpu.step()
        process.state = "SUCCEEDED"
        asyncio.create_task(
            self._heart.inbox.put(
                topic="session.failure",
                payload={
                    "session_id": process.pid,
                    "error": "Iteration budget reached, forced summary",
                },
            )
        )

        # Extract LLM's text response as the output
        summary = ""
        if final_step.is_final and final_step.final_result:
            raw = final_step.final_result.output or ""
            summary = raw if isinstance(raw, str) else str(raw)
        elif final_step.actions:
            # LLM might have responded with text (RETURN action)
            for action in final_step.actions:
                if action.kind == "RETURN":
                    summary = action.args.get("result", "")
                    break
                elif action.kind == "THOUGHT":
                    summary = action.args.get("content", "")
        if not summary:
            summary = (
                f"Iteration budget reached ({vcpu.config.max_iterations} iterations). "
                f"Task may be partially complete."
            )
        return ToolResult(status="OK", output=summary)

    def _step_to_stream_events(self, step_result: StepResult) -> List[dict]:
        """Convert a StepResult into stream-format events.

        Preserves the same event format as the original run_stream().
        """
        events = []

        for i, action in enumerate(step_result.actions):
            action_kind = getattr(action, "kind", None)

            if action_kind == "TOOL_CALL":
                tool_name = getattr(action, "name", "unknown")
                tool_args = getattr(action, "args", {})
                tool_id = getattr(action, "id", None)

                events.append(
                    {
                        "type": "tool_call",
                        "name": tool_name,
                        "args": tool_args,
                        "action_id": tool_id,
                    }
                )
                if i < len(step_result.results):
                    tool_result = step_result.results[i]
                    events.append(
                        {
                            "type": "tool_result",
                            "name": tool_name,
                            "args": tool_args,
                            "action_id": tool_id,
                            "output": getattr(tool_result, "output", str(tool_result)),
                            "status": getattr(tool_result, "status", "OK"),
                            "duration_ms": (
                                getattr(tool_result, "meta", {}).get("duration_ms")
                                if hasattr(tool_result, "meta")
                                else None
                            ),
                        }
                    )
            elif action_kind == "THOUGHT":
                content = (
                    action.args.get("content", action.args.get("text", ""))
                    if action.args
                    else ""
                )
                if content:
                    # Check if this thought was blocked by hallucination firewall
                    if (
                        i < len(step_result.results)
                        and getattr(step_result.results[i], "meta", {}).get(
                            "hallucination_blocked"
                        )
                    ):
                        continue  # Skip -- firewall blocked this
                    events.append({"type": "text", "content": content})
            elif action_kind == "RETURN":
                result_value = action.args.get("result", "") if action.args else ""
                events.append(
                    {
                        "type": "done",
                        "result": {
                            "status": "OK",
                            "output": result_value,
                        },
                    }
                )

        return events
