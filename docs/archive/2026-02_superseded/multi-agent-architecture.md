# Multi-Agent Collaboration Architecture Design

> **Version**: 1.1.0-draft
> **Status**: Proposed
> **Author**: Architect (Mind Avatar)
> **Date**: 2026-01-26
> **Based on**: Agent OS Von Neumann Architecture + Current Kernel Implementation

---

## 核心设计原则 (Simplified)

**内核提供机制（mechanism），应用层决定策略（policy）**

```
┌────────────────────────────────────────────────────────┐
│ Layer 2: Application                                    │
│   多智能体协作逻辑 (Commander-Worker, Pipeline...)      │
│   决定：哪个任务用哪个角色                              │
└────────────────────────────────────────────────────────┘
                          │
                          │ spawn(role="planner", vcpu_affinity=...)
                          ▼
┌────────────────────────────────────────────────────────┐
│ Layer 1: Kernel                                         │
│   Process.vcpu_affinity → vCPU 绑定                     │
│   提供：进程-vCPU 亲和性机制                            │
└────────────────────────────────────────────────────────┘
                          │
                          │ vCPU.llm_client
                          ▼
┌────────────────────────────────────────────────────────┐
│ Layer 0: Infrastructure                                 │
│   vCPU ← LLMClient (gemini-2.0/3.0/claude...)          │
│   提供：不同 ALU 能力                                   │
└────────────────────────────────────────────────────────┘
```

### 最小改动方案

| 层级 | 改动 | 说明 |
|------|------|------|
| **Kernel** | `AgentProcess.vcpu_affinity` | 新增字段，可选绑定 vCPU |
| **Kernel** | `Scheduler` 支持 vCPU 池 | 根据 affinity 选择 vCPU |
| **Infra** | `vCPU` 已支持 | 构造时绑定 LLMClient |
| **App** | 协作逻辑 | 应用层决定角色和协作模式 |

```python
# 最小实现伪代码
class AgentProcess:
    vcpu_affinity: Optional[str] = None  # 新增

class Scheduler:
    def __init__(self, vcpus: Dict[str, vCPU]):
        self._vcpus = vcpus

    async def exec(self, pid):
        proc = self.getproc(pid)
        vcpu = self._vcpus.get(proc.vcpu_affinity) or self._default_vcpu
        await vcpu.execute(proc)
```

**优点**：内核改动极小，多智能体协作的复杂性留给应用层。

---

## Thinking Process

### Problem Understanding

- **Core Problem**: How to enable multiple agents with different LLM backends (e.g., gemini-2.0-flash for execution, gemini-3-flash-preview for planning) to collaborate effectively within the Agent OS architecture?
- **Constraints**:
  1. Must fit within the existing three-layer architecture (Layer 0/1/2)
  2. Should reuse existing primitives (AgentProcess, vCPU, Scheduler, IPC)
  3. Must support different LLM models per agent role
  4. Must maintain process isolation and permission boundaries
  5. Should support both synchronous (planner-worker) and asynchronous (parallel workers) collaboration patterns

### Solution Exploration

| Solution | Description | Pros | Cons |
|----------|-------------|------|------|
| A: Layer 2 Orchestration | Implement collaboration at Application layer using existing Subagent tools | Simple, non-invasive | Limited coordination primitives, no native IPC |
| B: Layer 1 Kernel Extension | Add multi-vCPU support and enhanced IPC to kernel | Native OS-level support, efficient | Requires kernel changes, higher complexity |
| C: Layer 0 Multi-ALU | Create ALU pool with routing at infrastructure | Clean separation | Over-engineering, breaks abstraction |

### Decision Derivation

Based on the Unix philosophy and Von Neumann metaphor already established in Agent OS, **Solution B (Layer 1 Kernel Extension)** is recommended because:

1. Multi-process collaboration is fundamentally an OS responsibility
2. The existing `ProcessManager`, `vCPU`, and `IPC` modules provide the right abstractions
3. LLM model selection per process is analogous to CPU affinity in real OS
4. This approach naturally extends the existing fork/exec/wait model

---

## Summary

Design a multi-agent collaboration architecture at **Layer 1 (Kernel)** that extends the existing Agent OS with:
1. **Multi-vCPU Pool** - Support multiple vCPUs with different LLM backends
2. **Process Roles** - Define Planner/Executor role semantics with different capabilities
3. **Enhanced IPC** - Add coordination primitives (channels, signals, shared state)
4. **Collaboration Patterns** - Support Commander-Worker, Pipeline, and Swarm patterns

---

## Design

### Architecture Overview

```
+===========================================================================+
|                          LAYER 2: APPLICATION                              |
|  +---------------------------------------------------------------------+  |
|  |  CodeAgent (uses multi-agent collaboration via OS API)              |  |
|  |  - Registers role configurations                                    |  |
|  |  - Receives aggregated results                                      |  |
|  +---------------------------------------------------------------------+  |
+===========================================================================+
                                    |
                                    | spawn_with_role() / coordinate()
                                    v
+===========================================================================+
|                    LAYER 1: AGENT OS (Kernel) - EXTENDED                   |
|  +---------------------------------------------------------------------+  |
|  |                      vCPU Pool (Multi-Processor)                     |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  |  | vCPU-0 (Planner) |  | vCPU-1 (Executor)|  | vCPU-N (...)     |   |  |
|  |  | gemini-3-flash   |  | gemini-2.0-flash |  | configurable     |   |  |
|  |  | Think->Plan      |  | Think->Act->Obs  |  |                  |   |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  +---------------------------------------------------------------------+  |
|  |                      Coordination Subsystem (NEW)                    |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  |  | Channel Manager  |  | Barrier/Rendezvous|  | Shared State    |   |  |
|  |  | (async queues)   |  | (sync points)    |  | (coordination   |   |  |
|  |  | send()/recv()    |  | wait_all()       |  |  memory)        |   |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  +---------------------------------------------------------------------+  |
|  |                      Scheduler (ENHANCED)                            |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  |  | Role-Aware       |  | vCPU Affinity    |  | Load Balancer   |   |  |
|  |  | Scheduling       |  | (LLM routing)    |  | (cost-aware)    |   |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  +---------------------------------------------------------------------+  |
|  |                      Process Manager (ENHANCED)                      |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  |  | Process Roles    |  | Process Groups   |  | IPC Router      |   |  |
|  |  | planner/executor |  | (collaboration   |  | (message        |   |  |
|  |  | /reviewer        |  |  units)          |  |  dispatch)      |   |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  +---------------------------------------------------------------------+  |
+===========================================================================+
                                    |
                                    | Hardware Abstraction
                                    v
+===========================================================================+
|                       LAYER 0: INFRASTRUCTURE                              |
|  +---------------------------------------------------------------------+  |
|  |  ALU Pool (Multiple LLM Clients)                                    |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  |  | gemini-3-flash   |  | gemini-2.0-flash |  | claude-opus      |   |  |
|  |  | (reasoning)      |  | (execution)      |  | (review)         |   |  |
|  |  +------------------+  +------------------+  +------------------+   |  |
|  +---------------------------------------------------------------------+  |
+===========================================================================+
```

### Core Components

#### 1. vCPU Pool (`kernel/vcpu_pool.py`)

Manages multiple vCPU instances, each bound to a specific LLM backend.

```python
class vCPUPool:
    """
    Pool of virtual processors with different LLM backends.

    Analogous to multi-core CPU where each core may have different
    characteristics (e.g., performance cores vs efficiency cores).
    """

    def __init__(self):
        self._vcpus: Dict[str, vCPU] = {}
        self._role_mapping: Dict[str, str] = {}  # role -> vcpu_id

    def register_vcpu(
        self,
        vcpu_id: str,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        roles: List[str],
    ) -> None:
        """Register a vCPU with specific LLM and role capabilities."""
        vcpu = vCPU(llm_client, tool_registry)
        self._vcpus[vcpu_id] = vcpu
        for role in roles:
            self._role_mapping[role] = vcpu_id

    def get_vcpu_for_role(self, role: str) -> Optional[vCPU]:
        """Get vCPU suitable for the specified role."""
        vcpu_id = self._role_mapping.get(role)
        return self._vcpus.get(vcpu_id) if vcpu_id else None

    async def execute_on_role(
        self,
        role: str,
        process: AgentProcess,
    ) -> Any:
        """Execute process on vCPU matching the role."""
        vcpu = self.get_vcpu_for_role(role)
        if vcpu is None:
            vcpu = self._get_default_vcpu()
        return await vcpu.execute(process)
```

#### 2. Process Roles (`kernel/proc.py` extension)

Extend `AgentProcess` with role semantics.

```python
class ProcessRole(str, Enum):
    """Process roles for multi-agent collaboration."""

    PLANNER = "planner"      # Task decomposition, decision making
    EXECUTOR = "executor"    # Task execution, tool usage
    REVIEWER = "reviewer"    # Code review, verification
    COMMANDER = "commander"  # Orchestrates other agents
    WORKER = "worker"        # Executes delegated tasks

@dataclass
class AgentProcess:
    # ... existing fields ...

    # New fields for multi-agent collaboration
    role: ProcessRole = ProcessRole.EXECUTOR
    process_group: Optional[str] = None  # For coordinated processes
    vcpu_affinity: Optional[str] = None  # Preferred vCPU/LLM

    # Collaboration state
    collaborators: List[str] = field(default_factory=list)  # PIDs
    coordinator_pid: Optional[str] = None  # For workers
```

#### 3. Coordination Subsystem (`kernel/coord.py`)

New module for inter-process coordination.

```python
class Channel:
    """
    Async message channel between processes.
    Similar to Unix pipes but for structured messages.
    """

    def __init__(self, capacity: int = 100):
        self._queue: asyncio.Queue[IPCMessage] = asyncio.Queue(capacity)

    async def send(self, message: IPCMessage) -> None:
        await self._queue.put(message)

    async def recv(self, timeout: Optional[float] = None) -> IPCMessage:
        return await asyncio.wait_for(self._queue.get(), timeout)


class Barrier:
    """
    Synchronization point for multiple processes.
    All processes must reach the barrier before any can proceed.
    """

    def __init__(self, parties: int):
        self._parties = parties
        self._count = 0
        self._event = asyncio.Event()

    async def wait(self) -> None:
        self._count += 1
        if self._count >= self._parties:
            self._event.set()
        else:
            await self._event.wait()


class SharedState:
    """
    Shared state for coordinated processes.
    Provides atomic operations on shared data.
    """

    def __init__(self):
        self._state: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any:
        async with self._lock:
            return self._state.get(key)

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._state[key] = value

    async def update(self, key: str, updater: Callable[[Any], Any]) -> Any:
        async with self._lock:
            old_value = self._state.get(key)
            new_value = updater(old_value)
            self._state[key] = new_value
            return new_value


class CoordinationManager:
    """
    Manages coordination primitives for process groups.
    """

    def __init__(self):
        self._channels: Dict[str, Channel] = {}
        self._barriers: Dict[str, Barrier] = {}
        self._shared_states: Dict[str, SharedState] = {}

    def create_channel(self, name: str, capacity: int = 100) -> Channel:
        channel = Channel(capacity)
        self._channels[name] = channel
        return channel

    def create_barrier(self, name: str, parties: int) -> Barrier:
        barrier = Barrier(parties)
        self._barriers[name] = barrier
        return barrier

    def create_shared_state(self, group_id: str) -> SharedState:
        state = SharedState()
        self._shared_states[group_id] = state
        return state
```

#### 4. Enhanced Scheduler (`kernel/scheduler.py` extension)

Add role-aware scheduling and vCPU affinity.

```python
class EnhancedScheduler(ProcessManager):
    """
    Extended scheduler with multi-agent support.
    """

    def __init__(self, vcpu_pool: vCPUPool):
        super().__init__()
        self._vcpu_pool = vcpu_pool
        self._coord = CoordinationManager()

    def spawn_group(
        self,
        group_id: str,
        processes: List[Dict[str, Any]],
    ) -> List[str]:
        """
        Spawn a group of coordinated processes.

        Args:
            group_id: Identifier for the process group
            processes: List of process configurations
                       [{"role": "planner", "task": "...", ...}, ...]

        Returns:
            List of PIDs for spawned processes
        """
        pids = []
        shared_state = self._coord.create_shared_state(group_id)

        for proc_config in processes:
            pid = self.fork(
                parent_pid=self.getpid(),
                role=proc_config.get("role", "executor"),
                task=proc_config["task"],
                process_group=group_id,
                vcpu_affinity=proc_config.get("vcpu_affinity"),
                **proc_config,
            )
            pids.append(pid)

            # Set up collaborator relationships
            proc = self.getproc(pid)
            proc.collaborators = pids

        return pids

    async def exec_with_affinity(self, pid: str) -> None:
        """Execute process on appropriate vCPU based on role/affinity."""
        proc = self.getproc(pid)
        if proc is None:
            raise ValueError(f"Process {pid} not found")

        # Determine vCPU
        vcpu = None
        if proc.vcpu_affinity:
            vcpu = self._vcpu_pool._vcpus.get(proc.vcpu_affinity)
        if vcpu is None and proc.role:
            vcpu = self._vcpu_pool.get_vcpu_for_role(proc.role)
        if vcpu is None:
            vcpu = self._vcpu_pool._get_default_vcpu()

        # Execute
        proc.state = ProcessState.RUNNING
        proc.started_at = datetime.now()

        try:
            await vcpu.execute(proc)
        finally:
            if proc._completion_event:
                proc._completion_event.set()
```

#### 5. Enhanced AgentOS (`kernel/__init__.py` extension)

Unified API for multi-agent collaboration.

```python
class AgentOS:
    """Extended Agent OS with multi-agent collaboration support."""

    def __init__(
        self,
        llm_configs: Optional[Dict[str, LLMClient]] = None,
        tool_registry: Optional[ToolRegistry] = None,
        **kwargs,
    ):
        # ... existing init ...

        # Multi-agent extensions
        self._vcpu_pool = vCPUPool()
        self._scheduler = EnhancedScheduler(self._vcpu_pool)

        # Register vCPUs from config
        if llm_configs:
            for role, client in llm_configs.items():
                self._vcpu_pool.register_vcpu(
                    vcpu_id=f"vcpu_{role}",
                    llm_client=client,
                    tool_registry=tool_registry,
                    roles=[role],
                )

    async def coordinate(
        self,
        pattern: str,
        tasks: List[Dict[str, Any]],
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute a coordination pattern with multiple agents.

        Args:
            pattern: Collaboration pattern ("commander-worker", "pipeline", "swarm")
            tasks: Task configurations for each agent

        Returns:
            Aggregated results from all agents
        """
        if pattern == "commander-worker":
            return await self._coordinate_commander_worker(tasks, **kwargs)
        elif pattern == "pipeline":
            return await self._coordinate_pipeline(tasks, **kwargs)
        elif pattern == "swarm":
            return await self._coordinate_swarm(tasks, **kwargs)
        else:
            raise ValueError(f"Unknown coordination pattern: {pattern}")

    async def _coordinate_commander_worker(
        self,
        tasks: List[Dict[str, Any]],
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Commander-Worker pattern:
        1. Commander (Planner) decomposes task into subtasks
        2. Workers (Executors) execute subtasks in parallel
        3. Commander aggregates results
        """
        # Phase 1: Spawn commander (planner)
        commander_task = tasks[0] if tasks else {"role": "planner", "task": "Plan execution"}
        commander_pid = await self.spawn(
            role=commander_task.get("role", "planner"),
            goal=commander_task["task"],
            vcpu_affinity="vcpu_planner",
        )
        commander_result = await self.wait(commander_pid)

        # Phase 2: Parse subtasks from commander output
        subtasks = self._parse_subtasks(commander_result)

        # Phase 3: Spawn workers in parallel
        worker_pids = []
        for subtask in subtasks:
            pid = await self.spawn(
                role="executor",
                goal=subtask,
                vcpu_affinity="vcpu_executor",
            )
            worker_pids.append(pid)

        # Phase 4: Wait for all workers
        results = await asyncio.gather(*[
            self.wait(pid) for pid in worker_pids
        ])

        # Phase 5: Aggregate results
        return {
            "commander": commander_result,
            "workers": results,
            "pattern": "commander-worker",
        }
```

### Data Flow

```
User Goal: "Refactor module X to improve performance"
    |
    v
+-------------------+
| Application Layer |
| CodeAgent.run()   |
+-------------------+
    |
    | coordinate("commander-worker", [...])
    v
+-------------------+
| Agent OS Kernel   |
| CoordinateManager |
+-------------------+
    |
    |  Phase 1: Planning
    |  +-------------------------------+
    |  | spawn(role=planner)           |
    |  | vCPU affinity: gemini-3-flash |
    |  +-------------------------------+
    |       |
    |       | "Decompose into 3 subtasks"
    |       v
    |  +-------------------------------+
    |  | Planner Output:               |
    |  | 1. Analyze current module     |
    |  | 2. Implement optimizations    |
    |  | 3. Write tests               |
    |  +-------------------------------+
    |
    |  Phase 2: Parallel Execution
    |  +-------------------------------+
    |  | spawn(role=executor, task=1)  |
    |  | spawn(role=executor, task=2)  |
    |  | spawn(role=executor, task=3)  |
    |  | vCPU affinity: gemini-2.0-flash|
    |  +-------------------------------+
    |       |
    |       | [parallel execution]
    |       v
    |  +-------------------------------+
    |  | Worker Results:               |
    |  | 1. Analysis complete          |
    |  | 2. Code changes made          |
    |  | 3. Tests written             |
    |  +-------------------------------+
    |
    |  Phase 3: Aggregation
    |  +-------------------------------+
    |  | Aggregate results             |
    |  | Return to application         |
    |  +-------------------------------+
    v
+-------------------+
| AgentResponse     |
| Combined output   |
+-------------------+
```

### Collaboration Patterns

#### Pattern 1: Commander-Worker (Recommended for most cases)

```
                    +------------+
                    | Commander  |
                    | (Planner)  |
                    +-----+------+
                          |
              +-----------+-----------+
              |           |           |
        +-----v-----+ +---v---+ +-----v-----+
        | Worker 1  | |Worker2| | Worker 3  |
        | (Executor)| |(Exec) | | (Executor)|
        +-----------+ +-------+ +-----------+
```

- **Use Case**: Task decomposition, parallel execution
- **LLM Assignment**:
  - Commander: gemini-3-flash-preview (reasoning)
  - Workers: gemini-2.0-flash (execution)

#### Pattern 2: Pipeline

```
        +--------+     +--------+     +--------+
        | Stage1 | --> | Stage2 | --> | Stage3 |
        | (Plan) |     | (Impl) |     | (Review)|
        +--------+     +--------+     +--------+
```

- **Use Case**: Sequential processing with handoff
- **LLM Assignment**: Different models per stage

#### Pattern 3: Swarm (Future)

```
        +-------+
        | Coord |
        +---+---+
            |
    +-------+-------+
    |       |       |
  +---+   +---+   +---+
  | A |<->| B |<->| C |
  +---+   +---+   +---+
```

- **Use Case**: Dynamic task allocation, peer-to-peer
- **LLM Assignment**: Heterogeneous, adaptive

---

## Decisions

### Decision 1: Implement at Layer 1 (Kernel)

- **Decision**: Multi-agent collaboration is implemented at the Kernel layer, not Application
- **Rationale**:
  1. Process coordination is fundamentally an OS responsibility
  2. Reuses existing kernel primitives (AgentProcess, vCPU, IPC)
  3. Provides consistent abstraction for all applications
  4. Enables resource management (LLM costs, concurrency limits)
- **Alternatives**:
  - Layer 2: Would require each application to implement coordination
  - Layer 0: Would couple infrastructure with orchestration logic
- **Risks**: Kernel complexity increases

### Decision 2: vCPU Pool with Role-Based Routing

- **Decision**: Create a vCPU pool where each vCPU is bound to an LLM client, with role-based routing
- **Rationale**:
  1. Analogous to multi-core CPU with different core types (P-cores vs E-cores)
  2. Allows optimal LLM selection per task type
  3. Decouples LLM selection from application logic
  4. Supports cost optimization (expensive LLM for planning, cheap for execution)
- **Alternatives**:
  - Single vCPU with LLM switching: More complex state management
  - Application-level LLM selection: Leaky abstraction
- **Risks**: Configuration complexity

### Decision 3: Extend IPC with Channels and Shared State

- **Decision**: Add Channel (async queues) and SharedState (coordination memory) to existing IPC
- **Rationale**:
  1. Existing IPCMessage is point-to-point, need async streaming
  2. SharedState enables complex coordination patterns
  3. Follows Unix philosophy (pipes, shared memory)
- **Alternatives**:
  - Only use IPCMessage: Limited to request-response
  - External message queue: Over-engineering for in-process coordination
- **Risks**: Potential deadlocks if misused

### Decision 4: Commander-Worker as Primary Pattern

- **Decision**: Prioritize Commander-Worker pattern, implement Pipeline and Swarm later
- **Rationale**:
  1. Covers 80% of multi-agent use cases
  2. Maps naturally to Planner/Executor distinction in requirements
  3. Well-understood pattern with clear failure modes
  4. Gemini model characteristics align perfectly (3-flash for planning, 2.0-flash for execution)
- **Alternatives**:
  - All patterns at once: Too much scope
  - Peer-to-peer first: More complex, less common
- **Risks**: May not cover all collaboration needs

---

## Tradeoffs

### 1. Complexity vs Capability

**Choice**: Accept increased kernel complexity for powerful collaboration primitives

- **Cost**: More code to maintain in kernel, steeper learning curve
- **Benefit**: Rich coordination capabilities, better resource utilization
- **Rationale**: Multi-agent collaboration is a core requirement, worth the investment

### 2. Generality vs Optimization

**Choice**: Generic role-based routing over hard-coded model assignments

- **Cost**: Slightly less optimal routing in edge cases
- **Benefit**: Flexibility to change models without code changes
- **Rationale**: LLM landscape changes rapidly, need adaptability

### 3. Isolation vs Efficiency

**Choice**: Maintain process isolation, use explicit coordination primitives

- **Cost**: More message passing overhead
- **Benefit**: Cleaner failure isolation, easier debugging
- **Rationale**: Agent errors can cascade; isolation limits blast radius

---

## Constraints

### Technical Constraints

- Python 3.10+ async/await model
- asyncio event loop (single-threaded concurrency)
- LLM API rate limits and costs
- Token budget management across processes

### Architectural Constraints

- Must extend existing AgentOS, not replace
- Backward compatible with single-agent workflows
- Each layer only calls the layer below
- No circular dependencies between kernel modules

### Operational Constraints

- LLM cost tracking per process/role
- Configurable concurrency limits
- Graceful degradation when LLM unavailable

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Increased kernel complexity | High | Medium | Comprehensive tests, documentation |
| Coordination deadlocks | Medium | High | Timeouts, deadlock detection |
| LLM cost explosion | Medium | High | Budget limits per process group |
| Performance overhead | Medium | Low | Benchmark, optimize hot paths |
| Configuration complexity | High | Medium | Sensible defaults, validation |
| Inconsistent results across LLMs | Medium | Medium | Result validation, retry logic |

---

## Evidence

### Sources

| File | Lines | Finding |
|------|-------|---------|
| `kernel/__init__.py` | 54-278 | AgentOS already has spawn/wait/ps primitives |
| `kernel/scheduler.py` | 28-403 | ProcessManager with fork/exec/wait is extensible |
| `kernel/vcpu.py` | 106-735 | vCPU is cleanly separated, can be pooled |
| `kernel/proc.py` | 36-218 | AgentProcess supports custom fields |
| `kernel/ipc.py` | 1-183 | IPCMessage provides base for coordination |
| `tools/subagent.py` | 314-891 | SubagentExecutor shows coordination patterns |
| `llm/factory.py` | 39-260 | LLMFactory supports multiple providers |

### Assumptions

1. **Assumption**: Gemini-3-flash-preview is better at planning/reasoning than gemini-2.0-flash
   - *Validation needed*: Benchmark both models on planning tasks

2. **Assumption**: Gemini-2.0-flash is more persistent and completes tasks more reliably
   - *Validation needed*: Run completion rate experiments

3. **Assumption**: Multi-agent overhead is acceptable compared to single-agent
   - *Validation needed*: Latency and cost benchmarks

4. **Assumption**: Most tasks can be decomposed by a planner agent
   - *Validation needed*: Test on diverse real-world tasks

---

## Next Steps

### Phase 1: Foundation (Week 1-2)

1. **Implement vCPU Pool**
   - Create `kernel/vcpu_pool.py`
   - Extend AgentOS initialization to accept multiple LLM clients
   - Add role-based vCPU routing

2. **Extend AgentProcess**
   - Add `role`, `process_group`, `vcpu_affinity` fields
   - Add `collaborators`, `coordinator_pid` for coordination

3. **Unit Tests**
   - Test vCPU pool registration and routing
   - Test process role assignment

### Phase 2: Coordination (Week 2-3)

1. **Implement Coordination Subsystem**
   - Create `kernel/coord.py` with Channel, Barrier, SharedState
   - Integrate with ProcessManager

2. **Enhance Scheduler**
   - Add `spawn_group()` for coordinated process creation
   - Add `exec_with_affinity()` for role-aware execution

3. **Integration Tests**
   - Test channel communication between processes
   - Test barrier synchronization

### Phase 3: Patterns (Week 3-4)

1. **Implement Commander-Worker Pattern**
   - Add `AgentOS.coordinate()` method
   - Implement `_coordinate_commander_worker()`

2. **Application Integration**
   - Update CodeAgent to use coordination API
   - Add configuration for LLM role assignment

3. **E2E Tests**
   - Test full commander-worker workflow
   - Test error handling and recovery

### Phase 4: Optimization (Week 4-5)

1. **Cost Tracking**
   - Add per-process token usage tracking
   - Add group-level budget limits

2. **Performance Tuning**
   - Benchmark coordination overhead
   - Optimize hot paths

3. **Documentation**
   - Update CLAUDE.md with multi-agent APIs
   - Write developer guide for coordination patterns

---

## Appendix: Configuration Example

```yaml
# ~/.nimbus/config.yaml
multi_agent:
  enabled: true

  vcpu_pool:
    - id: vcpu_planner
      provider: gemini
      model: gemini-3-flash-preview
      roles: [planner, commander, reviewer]
      max_concurrent: 1

    - id: vcpu_executor
      provider: gemini
      model: gemini-2.0-flash
      roles: [executor, worker]
      max_concurrent: 5

    - id: vcpu_fallback
      provider: ollama
      model: llama3:70b
      roles: [default]
      max_concurrent: 2

  coordination:
    default_pattern: commander-worker
    timeout_seconds: 300
    max_workers: 5

  cost_limits:
    per_request_usd: 1.0
    per_group_usd: 5.0
```

---

## Appendix: API Usage Example

```python
from nimbus.kernel import AgentOS
from nimbus.llm import create_llm_client

# Create LLM clients for different roles
planner_llm = create_llm_client(provider="gemini", model="gemini-3-flash-preview")
executor_llm = create_llm_client(provider="gemini", model="gemini-2.0-flash")

# Initialize AgentOS with multi-agent support
kernel = AgentOS(
    llm_configs={
        "planner": planner_llm,
        "executor": executor_llm,
    },
    tool_registry=tools,
)

# Use commander-worker pattern
result = await kernel.coordinate(
    pattern="commander-worker",
    tasks=[
        {
            "role": "planner",
            "task": "Decompose: Refactor src/nimbus/core/agent.py for performance",
        },
        # Workers will be spawned based on planner output
    ],
    timeout=300,
)

print(result["commander"])  # Planning output
print(result["workers"])    # Execution results
```

---

*This document was generated by the Mind Avatar (Architect) based on analysis of the Nimbus v0.2.0 codebase and the Agent OS architecture.*
