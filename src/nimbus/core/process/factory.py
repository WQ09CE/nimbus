"""
ProcessFactory - Unified process component assembly.

Extracts the common component creation logic from AgentOS.spawn(),
AgentOS.chat(), and AgentOS.restore_session() into a single build() method.
"""

from __future__ import annotations

from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from loguru import logger

if TYPE_CHECKING:
    from nimbus.agentos import AgentOSConfig, Process
    from nimbus.core.profile import AgentProfile
    from nimbus.os.gate import KernelGate, SimpleEventStream
    from nimbus.tools.composite import CompositeToolRegistry


class ProcessFactory:
    """Unified process component assembly.

    Consolidates the repeated MMU -> NimFS -> Memo -> Gate -> Decoder -> VCPU -> Process
    creation logic that was previously duplicated across spawn(), chat(), and
    restore_session().
    """

    def __init__(
        self,
        llm: Any,
        config: "AgentOSConfig",
        composite_tools: "CompositeToolRegistry",
        events: "SimpleEventStream",
        create_gate_fn: Callable[..., "KernelGate"],
    ):
        self._llm = llm
        self._config = config
        self._composite_tools = composite_tools
        self._events = events
        self._create_gate = create_gate_fn

    def build(
        self,
        pid: str,
        goal: str,
        role: str = "chat",
        system_rules: Optional[str] = None,
        llm_client: Any = None,
        profile: Optional["AgentProfile"] = None,
        tools_override: Optional[List] = None,
        max_iterations: Optional[int] = None,
        write_filter: Optional[Any] = None,
        enable_ipc: bool = False,
        agent_os: Any = None,
        checkpoint: Any = None,
        filter_tools_by_role: bool = True,
    ) -> "Process":
        """Build a fully-assembled Process with all components.

        Args:
            pid: Process ID
            goal: Process goal description
            role: Role identifier (e.g. "chat", "standard", "executor")
            system_rules: Custom system rules (None = use config default)
            llm_client: Override LLM client (None = use default)
            profile: AgentProfile for configuration
            tools_override: Explicit tools list. None=inherit from kernel.
            max_iterations: Override max VCPU iterations
            write_filter: File extension whitelist for Write/Edit
            enable_ipc: Whether to create IPC tools (SendMessage, ReadInbox, SpawnSubAgent)
            agent_os: AgentOS reference needed for IPC tools
            checkpoint: Session checkpoint to restore from
            filter_tools_by_role: Whether to filter tool definitions by role (default True).
                Set to False to include all tools regardless of role (used by restore_session).

        Returns:
            Fully assembled Process instance
        """
        # -- 1. MMU --
        mmu = self._create_mmu(pid, system_rules=system_rules)

        # NimFS: inject workspace so MMU can auto-offload large tool results
        mmu.nimfs_workspace = str(Path.cwd())

        # NimFS-backed Memo (v2): inject NimFSManager for context loading
        workspace = Path.cwd()
        from nimbus.core.nimfs.manager import NimFSManager
        mmu._nimfs_manager = NimFSManager(workspace_path=workspace)

        # -- 2. Memo --
        from nimbus.tools.memo import create_memo_tool
        memo_def, memo_func, session_manager, global_manager = create_memo_tool(workspace, pid)
        mmu._memo_manager = session_manager
        mmu._global_memo_manager = global_manager

        # -- 2.5 Semantic, Episodic & Procedural Memory Tools --
        from nimbus.tools.memory_ops import create_memory_ops_tools
        from nimbus.core.memory.profile_store import ProfileStore
        from nimbus.core.memory.episodic_store import EpisodicStore
        from nimbus.core.memory.procedural_store import ProceduralStore

        profile_store = ProfileStore(workspace)
        episodic_store = EpisodicStore(workspace)
        procedural_store = ProceduralStore(workspace)

        # Attach to MMU for contextual anchoring
        mmu._profile_store = profile_store
        mmu._procedural_store = procedural_store

        (
            read_profile_def, read_profile_func,
            write_profile_def, write_profile_func,
            search_episodic_def, search_episodic_func,
            read_strategy_def, read_strategy_func,
            write_strategy_def, write_strategy_func
        ) = create_memory_ops_tools(profile_store, episodic_store, procedural_store)

        # -- 3. IPC tools (spawn-only) --
        local_tools: Dict[str, Callable] = {
            "Memo": memo_func,
            "ReadProfile": read_profile_func,
            "WriteProfile": write_profile_func,
            "SearchEpisodicLog": search_episodic_func,
            "ReadStrategy": read_strategy_func,
            "WriteStrategy": write_strategy_func
        }
        ipc_tool_defs = []

        if enable_ipc:
            from nimbus.core.ipc.tools import create_send_message_tool, create_read_inbox_tool
            from nimbus.core.ipc.subagent import create_spawn_subagent_tool

            send_msg_def, send_msg_func = create_send_message_tool(agent_os, pid)
            read_inbox_def, read_inbox_func = create_read_inbox_tool(agent_os, pid)
            spawn_sub_def, spawn_sub_func = create_spawn_subagent_tool(agent_os, pid)

            local_tools["SendMessage"] = send_msg_func
            local_tools["ReadInbox"] = read_inbox_func
            local_tools["SpawnSubAgent"] = spawn_sub_func

            ipc_tool_defs = [send_msg_def, read_inbox_def, spawn_sub_def]

        # -- 4. Gate --
        gate = self._create_gate(pid, role, local_tools=local_tools, write_filter=write_filter)

        # -- 5. Decoder --
        from nimbus.core.runtime.decoder import InstructionDecoder
        decoder = InstructionDecoder()

        # -- 6. Tools list --
        if tools_override is not None:
            # Explicit tools list (empty = pure reasoning, no tools)
            if tools_override and isinstance(tools_override[0], str):
                # If using names (strings), fetch definitions from registry
                tools_list = []
                for name in tools_override:
                    defn = self._composite_tools.get_definition(name)
                    if defn:
                        tools_list.append(defn.to_openai_format())
            else:
                # Normalize: convert any ToolDefinition objects to openai format dicts
                tools_list = []
                for t in tools_override:
                    if hasattr(t, "to_openai_format"):
                        tools_list.append(t.to_openai_format())
                    else:
                        tools_list.append(t)
        else:
            # Inherit from kernel + Memo + IPC
            if filter_tools_by_role:
                tools_list = self._composite_tools.get_definitions(format="openai", role=role)
            else:
                tools_list = self._composite_tools.get_definitions(format="openai")
            tools_list.append({
                "type": "function",
                "function": memo_def,
            })
            tools_list.append({"type": "function", "function": read_profile_def})
            tools_list.append({"type": "function", "function": write_profile_def})
            tools_list.append({"type": "function", "function": search_episodic_def})
            tools_list.append({"type": "function", "function": read_strategy_def})
            tools_list.append({"type": "function", "function": write_strategy_def})
            # Append IPC tool definitions if enabled
            for ipc_def in ipc_tool_defs:
                tools_list.append(ipc_def.to_openai_format())

        # -- 7. VCPU config overrides --
        vcpu_config = self._config.vcpu_config
        if max_iterations is not None:
            # Sub-processes: set iteration limit with compact-and-continue.
            # When hitting the limit, compact context and keep going (up to max_compactions).
            # This prevents subagents from stopping mid-task.
            vcpu_config = _dc_replace(
                vcpu_config,
                max_iterations=max_iterations,
                compact_on_limit=True,
                max_compactions=2,
            )

        # Forward profile's max_consecutive_thoughts to VCPU config
        if profile and profile.max_consecutive_thoughts:
            vcpu_config = _dc_replace(
                vcpu_config,
                max_consecutive_thoughts=profile.max_consecutive_thoughts,
            )

        # -- 8. VCPU --
        from nimbus.core.models.manifest import get_model_manifest
        from nimbus.core.runtime.vcpu import VCPU

        alu = llm_client or self._llm
        manifest = _dc_replace(get_model_manifest(alu), role=role)

        vcpu = VCPU(
            alu=alu,
            config=vcpu_config,
            decoder=decoder,
            mmu=mmu,
            gate=gate,
            tools=tools_list,
            session_id=pid,
            manifest=manifest,
        )

        # Restore checkpoint if provided
        if checkpoint is not None:
            vcpu.restore_from_checkpoint(checkpoint)

        # -- 9. Process --
        from nimbus.agentos import Process
        from nimbus.core.ipc.mailbox import Mailbox

        if enable_ipc:
            process = Process(
                pid=pid,
                goal=goal,
                role=role,
                state="PENDING",
                vcpu=vcpu,
                mmu=mmu,
                gate=gate,
                inbox=Mailbox(owner_pid=pid),
                outbox=Mailbox(owner_pid=pid),
            )
        else:
            process = Process(
                pid=pid,
                goal=goal,
                role=role,
                state="PENDING",
                vcpu=vcpu,
                mmu=mmu,
                gate=gate,
            )

        return process

    def _create_mmu(self, pid: str, system_rules: Optional[str] = None):
        """Create an MMU for a process."""
        from nimbus.core.memory.context import PinnedContext
        from nimbus.core.memory.mmu import MMU

        mmu = MMU(config=self._config.mmu_config, process_id=pid)

        sys_rules = system_rules if system_rules is not None else self._config.system_rules

        # Set pinned context
        pinned = PinnedContext(
            system_rules=sys_rules,
            workspace_info=self._config.workspace_info,
            capabilities=self._config.capabilities,
        )
        mmu.set_pinned(pinned)

        return mmu
