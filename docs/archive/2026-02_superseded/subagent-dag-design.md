# Subagent DAG Orchestration Design

> Architecture design for task-level DAG orchestration using Subagents

## Summary

This document proposes a new architecture for Subagent-level DAG orchestration in Nimbus. Instead of orchestrating low-level tool calls (Read, Write, Grep), the system will orchestrate high-level task units (Subagents like eye, body, mind, tongue, nose). Each subagent internally uses AgenticRunner for dynamic tool selection, while the outer DAG manages task dependencies, parallel execution, and failure recovery.

## 1. Current State Analysis

### 1.1 Existing DAG Structure

**Evidence**: `src/nimbus/core/types.py:266-472`

The current `TaskNode` is designed for tool-level operations:

```python
@dataclass
class TaskNode:
    id: str
    skill: str              # Tool name like "Read", "Grep", "synthesize"
    params: Dict[str, Any]  # Tool parameters
    depends_on: List[str]   # Dependency task IDs
    status: TaskStatus
    result: Optional[Any]
    # ... failure handling, retry support
```

**Characteristics**:
- Fine-grained: Each node is a single tool call
- Deterministic: Parameters fixed at planning time
- Shallow: Typically 2-5 nodes per DAG

### 1.2 Existing Subagent Implementation

**Evidence**: `src/nimbus/tools/subagent.py:297-745`

The current `SubagentExecutor` creates child CodeAgent instances:

```python
async def _execute_subagent(self, context, prompt, ...):
    # Creates a full CodeAgent per subagent
    child_agent = CodeAgent(
        llm_client=child_llm_client,
        tool_registry=child_registry,  # Restricted tools
        memory_type="simple",
        planner_type="dag",
    )
    response = await child_agent.run(enhanced_prompt)
```

**Characteristics**:
- Full agent per subagent (heavyweight)
- Isolated context with permission restrictions
- Supports foreground and background execution
- Concurrent limiting (MAX_CONCURRENT=5)

### 1.3 Existing Runtime

**Evidence**: `src/nimbus/core/runtime/executor.py:49-290`

The `AsyncRuntime` executes tool-level DAGs:

```python
async def execute_dag(self, dag: TaskDAG, resume: bool = True):
    while not dag.is_completed():
        ready_tasks = dag.get_ready_tasks()
        async with asyncio.TaskGroup() as tg:
            for task_node in ready_tasks:
                tg.create_task(self._execute_task(task_node, dag))
```

**Characteristics**:
- Parallel execution with semaphore control
- Checkpoint persistence support
- ReplanCoordinator integration
- Retry and failure handling (ADR-007)

### 1.4 Gap Analysis

| Aspect | Current (Tool DAG) | Target (Subagent DAG) |
|--------|-------------------|----------------------|
| Granularity | Tool call | Subagent task |
| Planning | Deterministic params | Goal-based prompt |
| Execution | Single tool call | Full agentic loop |
| Context | None | Parent context snapshot |
| Permissions | Uniform | Isolated per subagent |
| Failure | Retry same tool | Replan with new strategy |

## 2. Target Architecture

### 2.1 Architecture Overview

```
+------------------------------------------------------------------+
|                         User Request                              |
+------------------------------------------------------------------+
                               |
                               v
+------------------------------------------------------------------+
|                       TaskPlanner                                 |
|  - Analyzes user goal                                            |
|  - Decomposes into subagent tasks                                |
|  - Determines dependencies                                        |
|  - Outputs SubagentDAG                                           |
+------------------------------------------------------------------+
                               |
                               v
+------------------------------------------------------------------+
|                      SubagentDAG                                  |
|  +------------+     +------------+     +------------+            |
|  | SubagentNode|---->| SubagentNode|---->| SubagentNode|          |
|  | type: eye   |     | type: mind  |     | type: body  |          |
|  | goal: "..."  |     | goal: "..."  |     | goal: "..."  |          |
|  +------------+     +------------+     +------------+            |
+------------------------------------------------------------------+
                               |
                               v
+------------------------------------------------------------------+
|                    SubagentRuntime                                |
|  - Executes SubagentDAG in parallel                              |
|  - Manages subagent lifecycle                                    |
|  - Handles context passing                                       |
|  - Coordinates replan on failure                                 |
+------------------------------------------------------------------+
                               |
            +------------------+------------------+
            |                  |                  |
            v                  v                  v
+------------------+  +------------------+  +------------------+
|  AgenticRunner   |  |  AgenticRunner   |  |  AgenticRunner   |
|  (eye subagent)  |  |  (mind subagent) |  |  (body subagent) |
|  Tools: Read,    |  |  Tools: Read,    |  |  Tools: Read,    |
|  Glob, Grep      |  |  Write, Glob     |  |  Write, Edit,    |
|                  |  |                  |  |  Bash, Glob, Grep|
+------------------+  +------------------+  +------------------+
```

### 2.2 Core Components

#### 2.2.1 SubagentNode

```python
@dataclass
class SubagentNode:
    """A node representing a subagent task in the DAG.

    Unlike TaskNode which has fixed params, SubagentNode has a goal
    that the subagent interprets dynamically.
    """
    id: str
    subagent_type: SubagentType  # eye, body, mind, tongue, nose
    goal: str                     # Task description for the subagent
    depends_on: List[str]         # Dependency node IDs

    # Execution state
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[SubagentResult] = None
    error: Optional[str] = None

    # Configuration
    allowed_tools: Optional[Set[str]] = None  # Override default
    model: Optional[str] = None               # Model override
    max_turns: int = 50                       # Max agentic loop turns
    timeout: float = 300.0                    # Timeout in seconds

    # Context management
    context_sources: List[str] = field(default_factory=list)
    """IDs of nodes whose results should be injected into context."""

    # Failure handling
    on_failure: Optional[str] = None          # Fallback node ID
    max_retries: int = 1                      # Retry count
    retry_strategy: str = "same"              # "same", "alternate", "escalate"
```

#### 2.2.2 SubagentDAG

```python
@dataclass
class SubagentDAG:
    """DAG of subagent tasks.

    Unlike TaskDAG which is for tool orchestration, SubagentDAG
    orchestrates higher-level subagent tasks.
    """
    id: str
    user_goal: str
    nodes: Dict[str, SubagentNode]
    created_at: datetime = field(default_factory=datetime.now)

    # Metadata
    complexity: str = "moderate"  # simple, moderate, complex
    estimated_duration: Optional[int] = None  # seconds

    # Replan support
    replan_history: List[SubagentReplanRecord] = field(default_factory=list)

    def get_ready_nodes(self) -> List[SubagentNode]:
        """Get nodes whose dependencies are satisfied."""
        ready = []
        for node in self.nodes.values():
            if node.status != TaskStatus.PENDING:
                continue
            deps_satisfied = all(
                self.nodes[dep_id].status == TaskStatus.COMPLETED
                for dep_id in node.depends_on
                if dep_id in self.nodes
            )
            if deps_satisfied:
                ready.append(node)
        return ready

    def get_context_for_node(self, node_id: str) -> str:
        """Build context from completed dependency results."""
        node = self.nodes[node_id]
        context_parts = []

        for source_id in node.context_sources:
            source_node = self.nodes.get(source_id)
            if source_node and source_node.result:
                context_parts.append(
                    f"## Result from {source_node.subagent_type.value}\n"
                    f"{source_node.result.summary}"
                )

        return "\n\n".join(context_parts)
```

#### 2.2.3 TaskPlanner

```python
class TaskPlanner:
    """Plans subagent task decomposition from user goals.

    Unlike the existing PlannerPipeline which plans tool calls,
    TaskPlanner plans subagent delegation.
    """

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self._patterns = self._load_patterns()

    async def plan(
        self,
        goal: str,
        context: str,
        available_subagents: Set[SubagentType],
    ) -> SubagentDAG:
        """Generate SubagentDAG from user goal."""

        # Step 1: Complexity classification
        complexity = await self._classify_complexity(goal)

        # Step 2: Rule-based fast path for common patterns
        dag = self._try_rule_match(goal, complexity)
        if dag:
            return dag

        # Step 3: LLM-based decomposition
        dag = await self._llm_decompose(goal, context, available_subagents)

        # Step 4: Validation and repair
        dag = self._validate_and_repair(dag, available_subagents)

        return dag

    def _try_rule_match(self, goal: str, complexity: str) -> Optional[SubagentDAG]:
        """Fast path for common patterns."""

        # Pattern: "Read X and summarize"
        if re.match(r"read\s+.+\s+and\s+(summarize|explain)", goal, re.I):
            return SubagentDAG.create(
                goal,
                [
                    {"id": "t1", "type": "eye", "goal": f"Read and explore: {goal}"},
                    {"id": "t2", "type": "mind", "goal": "Summarize findings",
                     "depends_on": ["t1"], "context_sources": ["t1"]},
                ]
            )

        # Pattern: "Implement X"
        if re.match(r"(implement|create|build|add)\s+", goal, re.I):
            return SubagentDAG.create(
                goal,
                [
                    {"id": "t1", "type": "eye", "goal": "Explore existing code structure"},
                    {"id": "t2", "type": "mind", "goal": "Design implementation approach",
                     "depends_on": ["t1"], "context_sources": ["t1"]},
                    {"id": "t3", "type": "body", "goal": f"Implement: {goal}",
                     "depends_on": ["t2"], "context_sources": ["t1", "t2"]},
                    {"id": "t4", "type": "tongue", "goal": "Run tests to verify",
                     "depends_on": ["t3"], "context_sources": ["t3"]},
                ]
            )

        return None
```

#### 2.2.4 SubagentRuntime

```python
class SubagentRuntime:
    """Executes SubagentDAG with parallel subagent execution.

    Each subagent runs in its own AgenticRunner with isolated context
    and restricted tool permissions.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        workspace: Path,
        config: Optional[RuntimeConfig] = None,
    ):
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.workspace = workspace
        self.config = config or RuntimeConfig(max_concurrent=5)
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        self._coordinator = SubagentReplanCoordinator()

    async def execute(
        self,
        dag: SubagentDAG,
        parent_context: str = "",
    ) -> SubagentExecutionResult:
        """Execute SubagentDAG with parallel subagent execution."""

        start_time = datetime.now()

        while not dag.is_completed():
            # Check for replan pause
            if self._coordinator.is_paused():
                await asyncio.sleep(0.1)
                continue

            ready_nodes = dag.get_ready_nodes()

            if ready_nodes:
                async with asyncio.TaskGroup() as tg:
                    for node in ready_nodes:
                        tg.create_task(
                            self._execute_subagent(node, dag, parent_context)
                        )
            else:
                await asyncio.sleep(0.05)

        return self._build_result(dag, start_time)

    async def _execute_subagent(
        self,
        node: SubagentNode,
        dag: SubagentDAG,
        parent_context: str,
    ) -> None:
        """Execute a single subagent node."""

        async with self._semaphore:
            node.status = TaskStatus.RUNNING
            node.started_at = datetime.now()

            try:
                # Build context from dependencies
                dep_context = dag.get_context_for_node(node.id)
                full_context = f"{parent_context}\n\n{dep_context}".strip()

                # Create restricted tool registry
                child_registry = self._create_restricted_registry(node)

                # Create AgenticRunner
                runner = AgenticRunner(
                    llm_client=self._resolve_llm_client(node),
                    tool_executor=ToolRegistryExecutor(child_registry, self.workspace),
                    config=AgenticConfig(
                        max_iterations=node.max_turns,
                        allowed_tools=node.allowed_tools,
                        workspace=self.workspace,
                    ),
                )

                # Execute agentic loop
                result = await self._run_with_timeout(
                    runner, node.goal, full_context, node.timeout
                )

                node.status = TaskStatus.COMPLETED
                node.result = result

            except Exception as e:
                node.status = TaskStatus.FAILED
                node.error = str(e)
                await self._handle_failure(node, dag)

            finally:
                node.finished_at = datetime.now()

    async def _handle_failure(
        self,
        node: SubagentNode,
        dag: SubagentDAG,
    ) -> None:
        """Handle subagent failure with replan support."""

        if node.max_retries > 0 and node.retry_count < node.max_retries:
            # Retry with same or alternate strategy
            node.retry_count += 1
            node.status = TaskStatus.PENDING
            return

        if node.on_failure:
            # Execute fallback node
            fallback = dag.nodes.get(node.on_failure)
            if fallback:
                fallback.status = TaskStatus.PENDING
                # Inject error context
                fallback.goal = f"{fallback.goal}\n\nPrevious error: {node.error}"
            return

        # Request global replan
        if self._should_replan(node, dag):
            await self._request_replan(node, dag)
        else:
            dag.mark_downstream_skipped(node.id)
```

### 2.3 Data Flow

```
┌────────────────────────────────────────────────────────────────────┐
│                        User Request                                 │
│                  "Implement a caching layer"                        │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
                              v
┌────────────────────────────────────────────────────────────────────┐
│                        TaskPlanner                                  │
│  1. Classify complexity: MODERATE                                   │
│  2. Match pattern: "implement" -> explore->design->implement->test  │
│  3. Generate SubagentDAG                                           │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
                              v
┌────────────────────────────────────────────────────────────────────┐
│                       SubagentDAG                                   │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐         │
│  │  t1:eye │───>│ t2:mind │───>│ t3:body │───>│t4:tongue│         │
│  │ explore │    │ design  │    │implement│    │  test   │         │
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘         │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
                              v
┌────────────────────────────────────────────────────────────────────┐
│                    SubagentRuntime                                  │
│  Phase 1: Execute t1 (eye)                                         │
│    └── AgenticRunner: Read -> Glob -> Read -> ...                  │
│    └── Result: "Found 5 service files, caching not implemented"    │
│                                                                     │
│  Phase 2: Execute t2 (mind), context=[t1.result]                   │
│    └── AgenticRunner: Read -> Write (design doc)                   │
│    └── Result: "Propose LRU cache with Redis backend"              │
│                                                                     │
│  Phase 3: Execute t3 (body), context=[t1.result, t2.result]        │
│    └── AgenticRunner: Read -> Edit -> Write -> Bash (test)         │
│    └── Result: "Implemented cache.py with 3 functions"             │
│                                                                     │
│  Phase 4: Execute t4 (tongue), context=[t3.result]                 │
│    └── AgenticRunner: Bash (pytest) -> Read                        │
│    └── Result: "All 12 tests passed"                               │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
                              v
┌────────────────────────────────────────────────────────────────────┐
│                      Final Response                                 │
│  "Implemented caching layer with LRU cache and Redis backend.      │
│   Created cache.py with 3 functions. All 12 tests passed."         │
└────────────────────────────────────────────────────────────────────┘
```

## 3. Key Design Decisions

### Decision 1: Separate SubagentDAG from TaskDAG

- **Decision**: Create new `SubagentDAG` and `SubagentNode` types instead of extending existing `TaskDAG`
- **Rationale**:
  - Clean separation of abstraction levels (tool vs task)
  - Different planning strategies for each level
  - Avoid polluting existing working code
  - Allows independent evolution
- **Alternatives Considered**:
  - Extend TaskNode with subagent fields: Would mix abstractions
  - Use same TaskDAG with skill="Subagent": Loses type safety
- **Risks**: More code to maintain; potential duplication

### Decision 2: Each Subagent Uses AgenticRunner

- **Decision**: Each SubagentNode executes via its own AgenticRunner instance
- **Rationale**:
  - AgenticRunner already supports dynamic tool selection
  - Natural fit for exploratory tasks
  - Allows subagent to adapt based on intermediate results
  - Existing code proven to work
- **Alternatives Considered**:
  - Pre-plan tool DAG for each subagent: Loses flexibility
  - Share single AgenticRunner: Complicates state management
- **Evidence**: `src/nimbus/core/runtime/agentic.py:163-322`

### Decision 3: Context Injection via context_sources

- **Decision**: SubagentNode specifies which predecessor results to inject into context
- **Rationale**:
  - Explicit control over what context each subagent sees
  - Avoids context explosion from accumulating all results
  - Planner can optimize based on task relationships
- **Alternatives Considered**:
  - Inject all predecessor results: Context too large
  - Let subagent query results: More LLM calls
- **Risks**: Planner must correctly identify relevant context sources

### Decision 4: Replan at Task Level

- **Decision**: Implement SubagentReplanCoordinator for task-level replanning
- **Rationale**:
  - Failure at subagent level may require strategy change
  - Can substitute different subagent type (e.g., escalate from body to mind+body)
  - Preserve completed subagent results
- **Evidence**: Existing `ReplanCoordinator` pattern in `src/nimbus/core/runtime/coordinator.py`
- **Alternatives Considered**:
  - Only retry same subagent: Insufficient for complex failures
  - Always replan from scratch: Wastes completed work

## 4. Tradeoffs

### Performance vs Flexibility

- **Choice**: Flexibility (each subagent is a full AgenticRunner)
- **Reason**: Code tasks are inherently exploratory; pre-planning tools is fragile
- **Cost**: Higher latency (multiple LLM calls per subagent)
- **Mitigation**: Parallel execution of independent subagents

### Isolation vs Context Sharing

- **Choice**: Explicit context injection (context_sources)
- **Reason**: Full isolation loses important context; full sharing explodes context window
- **Cost**: Planner must understand which results are relevant
- **Mitigation**: Default patterns encode common context relationships

### Simplicity vs Replan Capability

- **Choice**: Full replan capability (SubagentReplanCoordinator)
- **Reason**: Complex tasks often fail in unexpected ways; static plans insufficient
- **Cost**: More complex runtime logic
- **Mitigation**: Replan only when necessary; preserve completed work

## 5. Constraints

### Technical Constraints

1. **Token Budget**: Each subagent's context (parent + dependencies) must fit in LLM context window
2. **Concurrency Limit**: Maximum 5 concurrent subagents (existing SubagentExecutor limit)
3. **Timeout**: Default 5 minutes per subagent to prevent runaway execution
4. **Recursion Depth**: Maximum 3 levels of nested subagent calls

### Business Constraints

1. **Backward Compatibility**: Existing tool-level DAG must continue to work
2. **Cost Awareness**: Subagent execution is expensive; need cost estimation
3. **Observability**: Must emit events for monitoring and debugging

## 6. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Context overflow | Medium | High | Summarize long results; truncate with indication |
| Subagent loops | Low | High | Detect repetitive tool calls; max iterations |
| Planning errors | Medium | Medium | Validation + repair; fallback to simpler plan |
| Performance regression | Medium | Medium | Benchmark; parallel execution; caching |
| Replan thrashing | Low | Medium | Meaningful change detection; replan budget |

## 7. Integration with Existing System

### 7.1 Relationship with AgenticRunner

```
SubagentRuntime
    |
    +-- Uses AgenticRunner for each subagent
    |   (one instance per SubagentNode execution)
    |
    +-- AgenticRunner uses existing tool execution
        (Read, Write, Glob, Grep, etc.)
```

- SubagentRuntime orchestrates at task level
- AgenticRunner orchestrates at tool level
- No modification to AgenticRunner needed

### 7.2 Relationship with Existing DAG System

```
CodeAgent.run(goal)
    |
    +-- For complex goals: TaskPlanner -> SubagentDAG -> SubagentRuntime
    |
    +-- For simple goals: PlannerPipeline -> TaskDAG -> AsyncRuntime (unchanged)
```

- Coexistence: Both systems work independently
- Router decides which system to use based on complexity
- Existing TaskDAG/AsyncRuntime unchanged

### 7.3 Permission Integration

- SubagentNode.allowed_tools restricts tools per subagent
- Inherits from existing SUBAGENT_TOOL_PERMISSIONS mapping
- Child registry created per subagent execution

**Evidence**: `src/nimbus/tools/subagent.py:51-56`

```python
SUBAGENT_TOOL_PERMISSIONS = {
    "explorer": {"Read", "Glob", "Grep"},
    "researcher": {"Read", "Glob", "Grep", "WebSearch", "WebFetch"},
    "coder": {"Read", "Write", "Edit", "Bash", "Glob", "Grep"},
    "reviewer": {"Read", "Glob", "Grep"},
}
```

## 8. Implementation Phases

### Phase 1: Core Types (1 week)

- [ ] Define SubagentNode dataclass
- [ ] Define SubagentDAG dataclass
- [ ] Define SubagentResult, SubagentExecutionResult
- [ ] Add serialization/deserialization

### Phase 2: TaskPlanner (2 weeks)

- [ ] Implement complexity classifier
- [ ] Implement rule-based fast path (5-10 common patterns)
- [ ] Implement LLM-based decomposition
- [ ] Add validation and repair logic

### Phase 3: SubagentRuntime (2 weeks)

- [ ] Implement basic parallel execution
- [ ] Implement context injection
- [ ] Integrate with AgenticRunner
- [ ] Add timeout and error handling

### Phase 4: Replan Support (1 week)

- [ ] Implement SubagentReplanCoordinator
- [ ] Add retry strategies (same, alternate, escalate)
- [ ] Implement on_failure fallback mechanism
- [ ] Add replan history tracking

### Phase 5: Integration (1 week)

- [ ] Integrate with CodeAgent router
- [ ] Add observability events
- [ ] Write comprehensive tests
- [ ] Performance benchmarking

## 9. Evidence References

| Evidence | Location | Description |
|----------|----------|-------------|
| TaskNode definition | `src/nimbus/core/types.py:266-472` | Existing tool-level task node |
| TaskDAG definition | `src/nimbus/core/types.py:476-700` | Existing DAG structure |
| AsyncRuntime | `src/nimbus/core/runtime/executor.py:49-290` | Parallel DAG execution |
| SubagentExecutor | `src/nimbus/tools/subagent.py:297-745` | Current subagent implementation |
| AgenticRunner | `src/nimbus/core/runtime/agentic.py:163-322` | Agentic loop runtime |
| ReplanCoordinator | `src/nimbus/core/runtime/coordinator.py:67-600` | Replan coordination |
| PlannerPipeline | `src/nimbus/core/planner/pipeline.py:57-551` | Planning pipeline |

## 10. Assumptions

1. **LLM Capability**: Assumes LLM can reliably decompose goals into subagent tasks
2. **Context Relevance**: Assumes explicit context_sources are sufficient (no dynamic context lookup)
3. **Failure Recovery**: Assumes most failures can be recovered via retry or fallback
4. **Parallelism**: Assumes independent subagents can safely execute in parallel

## 11. Next Steps

1. **Review**: Get feedback on this design from team
2. **Prototype**: Build minimal SubagentNode + SubagentRuntime
3. **Benchmark**: Compare tool-DAG vs subagent-DAG for representative tasks
4. **Iterate**: Refine based on prototype learnings
