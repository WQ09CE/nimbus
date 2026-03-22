"""Spawn Agent Tool — Delegate a goal to a dedicated sub-agent.

Instantiates a real sub-agent (AgentOS) with role-based model selection
and restricted tool sets. The sub-agent runs in the same process with
its own VCPU loop and scratchpad.

Design doc: docs/spawn_agent.md
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from nimbus.core.tools.registry import ToolParameter, ToolRegistry, tool

if TYPE_CHECKING:
    from nimbus.core.path_context import AgentPathContext

logger = logging.getLogger("nimbus.spawn_agent")

# Role → allowed tools mapping
_ROLE_TOOLS: Dict[str, List[str]] = {
    "reader": ["Read", "Grep"],
    "worker": ["Write", "Edit", "Bash"],
}

DEFAULT_TIMEOUT = 600


def _build_sub_agent_tools(role: str) -> ToolRegistry:
    """Build a restricted ToolRegistry for the given role."""
    from nimbus.core.tools.read import read_file
    from nimbus.core.tools.write import write_file
    from nimbus.core.tools.edit import edit_file
    from nimbus.core.tools.bash import bash_command
    from nimbus.core.tools.grep import grep_search
    from nimbus.core.tools.submit_result import submit_result

    allowed = _ROLE_TOOLS.get(role, [])
    registry = ToolRegistry()

    tool_map = {
        "Read": read_file,
        "Grep": grep_search,
        "Write": write_file,
        "Edit": edit_file,
        "Bash": bash_command,
    }

    for name in allowed:
        func = tool_map.get(name)
        if func:
            registry.register_decorated(func)

    # All sub-agents get submit_result — it's their only exit in contract_mode
    registry.register_decorated(submit_result)

    return registry


def _resolve_model_for_role(role: str) -> str:
    """Resolve role → full model name from config."""
    from nimbus.config import get_config
    from nimbus.core.models.registry import ModelRegistry

    cfg = get_config()
    model_alias = cfg.agent_roles.get(role)
    if not model_alias:
        raise ValueError(
            f"No model configured for role '{role}'. "
            f"Set agent_roles.{role} in ~/.nimbus/config.json"
        )

    return ModelRegistry.normalize(model_alias)


def _collect_partial(loop: Any, scratchpad_path: str) -> str:
    """Collect partial results from a sub-agent loop + scratchpad on disk.

    Called on timeout or exception so the parent agent gets everything
    the sub-agent accomplished without needing an extra Read tool call.
    """
    sections: List[str] = []

    # 1. RuntimeLoop.partial_results (tool call outputs accumulated so far)
    partial = getattr(loop, "partial_results", [])
    if partial:
        lines = []
        for i, r in enumerate(partial, 1):
            preview = str(r.output)[:300] if r.output else "(no output)"
            lines.append(f"  [{i}] {r.status}: {preview}")
        sections.append(
            f"**Partial tool results ({len(partial)} calls):**\n" + "\n".join(lines)
        )

    # 2. Scratchpad file on disk (sub-agent may have written progress there)
    try:
        from pathlib import Path

        sp = Path(scratchpad_path)
        if sp.exists():
            content = sp.read_text(encoding="utf-8").strip()
            if content:
                # Cap at 2000 chars to avoid blowing up parent context
                if len(content) > 2000:
                    content = content[:2000] + "\n...(truncated)"
                sections.append(f"**Scratchpad content:**\n```\n{content}\n```")
    except Exception as e:
        logger.debug(f"Could not read scratchpad {scratchpad_path}: {e}")

    if not sections:
        return "**Partial progress:** (none captured)"

    return "\n\n".join(sections)


async def _run_sub_agent(
    role: str,
    goal: str,
    sub_session_id: str,
    timeout_seconds: int,
    on_update: Optional[Callable] = None,  # (chunk: str, ui_detail?: dict) -> None
    _abort_event: Optional[asyncio.Event] = None,
    path_context: Optional[AgentPathContext] = None,
) -> Dict[str, Any]:
    """Instantiate and run a sub-agent AgentOS."""
    from nimbus.adapters.llm_factory import create_llm_client
    from nimbus.core.agent import AgentConfig, AgentOS

    scratchpad_path = f".nimbus/sessions/{sub_session_id}/scratchpad.md"

    # 1. Resolve model
    full_model = _resolve_model_for_role(role)
    provider, model_id = full_model.split("/", 1) if "/" in full_model else ("google", full_model)

    if on_update:
        on_update(f"[spawn] Creating sub-agent [{role}] with model {full_model}\n")

    # 2. Create LLM adapter
    llm_client = await create_llm_client(model=full_model, timeout=120.0)

    # 3. Build restricted tool registry
    sub_tools = _build_sub_agent_tools(role)

    # 4. System prompt for sub-agent
    workspace_root = path_context.workspace_root if path_context else os.getcwd()
    target_root = path_context.target_root if path_context else os.getcwd()
    system_prompt = (
        f"You are a sub-agent with the role of '{role}'. "
        "Complete the goal given to you using ONLY the tools available. "
        "Think step by step. Be concise and precise.\n\n"
        f"# Scratchpad\n"
        f"You have a dedicated scratchpad at `{scratchpad_path}`.\n"
        "**Write progress incrementally** — after each meaningful step, "
        "append your findings and checked-off TODOs to the scratchpad immediately. "
        "Do NOT wait until the end. If you are interrupted at any point, "
        "the scratchpad should already contain all progress so far.\n\n"
        "# Delivering Results\n"
        "When your work is complete, you MUST call `submit_result` to deliver structured results. "
        "Do NOT just say 'done' in text — call the tool.\n\n"
        f"# Working Directory\n"
        f"Workspace root: {workspace_root}\n"
        f"Target directory: {target_root}\n"
        f"You may only write files within the target directory."
    )

    # 5. Configure AgentOS
    agent_config = AgentConfig(
        model=full_model,
        provider=provider,
        max_iterations=50,
        max_consecutive_thoughts=3,
        llm_call_timeout=120.0,
        text_is_final=False,
        contract_mode=True,  # Must exit via submit_result, not text
    )

    agent_os = AgentOS(
        config=agent_config,
        adapter=llm_client,
        tools=sub_tools,
        system_prompt=system_prompt,
        path_context=path_context,
    )

    # 6. Run with timeout, using stream_with_queue to capture partial_results
    loop = agent_os.stream_with_queue(goal, session_id=sub_session_id)

    # Propagate parent abort event to sub-agent loop so bash processes get killed
    # P2 fix: also set up an abort watcher that fully aborts the child loop
    # (sets _interrupted + interrupts child VCPU), not just the abort event
    abort_watcher = None
    if _abort_event is not None:
        loop._abort_event = _abort_event

        async def _watch_parent_abort():
            await _abort_event.wait()
            logger.info(f"[spawn] Parent abort detected, aborting sub-agent [{role}]")
            loop.abort()

        abort_watcher = asyncio.create_task(_watch_parent_abort())

    async def _drain_loop():
        """Drive the loop to completion, return final ToolResult.
        
        Emits dual-channel progress updates:
        - chunk: concise one-liner for parent agent context (low token cost)
        - ui_detail: rich structured data for frontend rendering
        """
        from nimbus.core.protocol import ToolResult
        final = None
        tool_count = 0
        async for event in loop.stream():
            etype = event.get("type")
            if etype == "final":
                final = event["result"]
            elif etype == "tool_call_done" and on_update:
                # Emit progress: one line per tool call completed
                tool_count += 1
                data = event.get("data", event)
                tool_name = data.get("tool", "?")
                status = data.get("status", "OK")
                # tool_call_done uses "output_preview" not "output"
                output_preview = str(data.get("output_preview") or data.get("output") or "")[:200]
                
                # Channel 1 (agent): concise one-liner
                chunk = f"[{role}] 🔧 {tool_name}: {status}\n"
                
                # Channel 2 (UI): structured detail with args from preceding tool_call_start
                ui_detail = {
                    "sub_session_id": sub_session_id,
                    "role": role,
                    "tool": tool_name,
                    "status": status,
                    "step": tool_count,
                    "output_preview": output_preview,
                    "call_id": data.get("call_id"),
                }
                on_update(chunk, ui_detail)
            elif etype == "tool_call_start" and on_update:
                # Capture tool args for richer UI display
                data = event.get("data", event)
                tool_name = data.get("tool", "?")
                args = data.get("args", {})
                # Build a concise args summary
                args_summary = ""
                for k, v in args.items():
                    v_str = str(v)
                    if len(v_str) > 100:
                        v_str = v_str[:97] + "..."
                    args_summary += f"{k}={v_str} "
                args_summary = args_summary.strip()[:200]
                
                ui_detail = {
                    "sub_session_id": sub_session_id,
                    "role": role,
                    "type": "tool_start",
                    "tool": tool_name,
                    "args_summary": args_summary,
                    "args": {k: str(v)[:200] for k, v in args.items()},
                }
                on_update(f"[{role}] 🔧 {tool_name} starting...\n", ui_detail)
            elif etype == "thinking" and on_update:
                # Sub-agent is reasoning
                thought = str(event.get("data", {}).get("text", ""))[:60]
                chunk = f"[{role}] 💭 {thought}...\n"
                ui_detail = {
                    "sub_session_id": sub_session_id,
                    "role": role,
                    "type": "thinking",
                    "thought_preview": thought,
                }
                on_update(chunk, ui_detail)
        return final or ToolResult(status="ERROR", output="Loop ended without result.")

    try:
        result = await asyncio.wait_for(_drain_loop(), timeout=timeout_seconds)

        if on_update:
            on_update(f"[spawn] Sub-agent [{role}] completed.\n")

        # Try to read structured deliverable first
        deliverable_path = f".nimbus/sessions/{sub_session_id}/deliverable.json"
        deliverable = None
        try:
            import json as _json
            if os.path.exists(deliverable_path):
                with open(deliverable_path, "r", encoding="utf-8") as f:
                    deliverable = _json.load(f)
                logger.info(f"Read structured deliverable from {deliverable_path}")
        except Exception as e:
            logger.warning(f"Failed to read deliverable.json: {e}")

        if deliverable:
            # Structured path: return parsed JSON to parent agent
            summary = deliverable.get("summary", "")
            findings = deliverable.get("findings", [])
            artifacts = deliverable.get("artifacts", [])
            findings_text = "\n".join(f"  - {f}" for f in findings) if findings else "  (none)"
            return {
                "output": (
                    f"Sub-agent [{role}] delivered structured results.\n\n"
                    f"**Summary:** {summary}\n\n"
                    f"**Findings:**\n{findings_text}\n\n"
                    f"**Artifacts:** {artifacts}\n"
                    f"**Scratchpad:** `{scratchpad_path}`"
                ),
                "ui_detail": {
                    "role": role,
                    "model": full_model,
                    "status": "completed",
                    "sub_session_id": sub_session_id,
                    "scratchpad": scratchpad_path,
                    "deliverable": deliverable,
                },
            }

        # Fallback: no deliverable.json, use raw text output
        output_text = str(result.output) if result.output else "(no output)"

        # Prevent massive sub-agent outputs from blowing up parent context
        if len(output_text) > 4000:
            output_text = (
                output_text[:4000] 
                + "\n\n...(Output truncated to 4000 chars to protect parent context. "
                f"Full details are in the scratchpad: {scratchpad_path})"
            )

        return {
            "output": (
                f"Sub-agent [{role}] completed (no structured deliverable).\n\n"
                f"**Result:**\n{output_text}\n\n"
                f"**Scratchpad:** `{scratchpad_path}`"
            ),
            "ui_detail": {
                "role": role,
                "model": full_model,
                "status": "completed",
                "sub_session_id": sub_session_id,
                "scratchpad": scratchpad_path,
            },
        }

    except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as exc:
        is_timeout = isinstance(exc, asyncio.TimeoutError)
        is_cancelled = isinstance(exc, asyncio.CancelledError)
        if is_cancelled:
            status = "CANCELLED"
            reason = "cancelled by user interrupt"
        elif is_timeout:
            status = "TIMEOUT"
            reason = f"timed out after {timeout_seconds} seconds"
        else:
            status = "ERROR"
            reason = f"failed with error: {exc}"

        if not is_timeout and not is_cancelled:
            logger.error(f"Sub-agent [{role}] {reason}", exc_info=True)
        if on_update:
            on_update(f"[spawn] Sub-agent [{role}] {reason}.\n")

        # --- Recover partial results ---
        partial_section = _collect_partial(loop, scratchpad_path)

        return {
            "output": (
                f"Sub-agent [{role}] {reason}.\n\n"
                f"{partial_section}\n\n"
                f"**Scratchpad:** `{scratchpad_path}`"
            ),
            "ui_detail": {
                "role": role,
                "model": full_model,
                "status": status,
                "sub_session_id": sub_session_id,
                "scratchpad": scratchpad_path,
                **({"error": str(exc)} if not is_timeout and not is_cancelled else {}),
            },
        }
    finally:
        # Clean up abort watcher
        if abort_watcher and not abort_watcher.done():
            abort_watcher.cancel()


@tool(
    name="spawn_agent",
    description="Spawn a dedicated sub-agent to handle a complex or isolated goal.",
    parameters=[
        ToolParameter(
            name="role",
            type="string",
            description="The role of the sub-agent. MUST be 'reader' (Read/Grep only) or 'worker' (Write/Edit/Bash).",
            required=True,
            enum=["reader", "worker"],
        ),
        ToolParameter(
            name="goal",
            type="string",
            description="The specific goal the sub-agent needs to accomplish, including any context.",
            required=True,
        ),
        ToolParameter(
            name="timeout_seconds",
            type="integer",
            description="Maximum execution time in seconds. Defaults to 600.",
            required=False,
        ),
        ToolParameter(
            name="target_sub_path",
            type="string",
            description=(
                "Optional sub-directory to narrow the sub-agent's workspace scope "
                "(e.g. 'src/utils'). The sub-agent will only write within this directory."
            ),
            required=False,
        ),
    ],
)
async def spawn_agent(
    role: str,
    goal: str = "",
    timeout_seconds: int = DEFAULT_TIMEOUT,
    target_sub_path: Optional[str] = None,
    on_update: Optional[Callable] = None,  # (chunk: str, ui_detail?: dict) -> None
    _abort_event: Optional[asyncio.Event] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Spawn a sub-agent with role-based model and tool restrictions."""

    # Robustness: support both 'goal' and 'task'
    goal = goal or kwargs.get("task")
    if not goal:
        return {
            "output": "Missing required parameter 'goal'.",
            "ui_detail": {"status": "ERROR", "error": "Missing goal"},
        }

    # Validate role
    if role not in _ROLE_TOOLS:
        return {
            "output": f"Invalid role '{role}'. Must be one of: {list(_ROLE_TOOLS.keys())}",
            "ui_detail": {"status": "ERROR", "error": f"Invalid role: {role}"},
        }

    # Check abort early
    if _abort_event and _abort_event.is_set():
        return {
            "output": f"Sub-agent [{role}] aborted before starting.",
            "ui_detail": {"status": "aborted"},
        }

    # Derive child path context from parent (injected by Gate via kwargs)
    from nimbus.core.path_context import AgentPathContext

    parent_path_context: AgentPathContext | None = kwargs.get("_path_context")
    if parent_path_context:
        child_path_context = parent_path_context.derive_for_sub_agent(target_sub_path)
    else:
        child_path_context = AgentPathContext.from_cwd()

    # Generate unique sub-session ID
    sub_session_id = f"sub_{uuid.uuid4().hex[:12]}"

    if on_update:
        on_update(f"[spawn] Spawning sub-agent [{role}] (session: {sub_session_id})...\n")

    return await _run_sub_agent(
        role=role,
        goal=goal,
        sub_session_id=sub_session_id,
        timeout_seconds=timeout_seconds,
        on_update=on_update,
        _abort_event=_abort_event,
        path_context=child_path_context,
    )
