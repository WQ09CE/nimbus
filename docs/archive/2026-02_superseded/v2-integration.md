# V2 Integration Design: CodeAgent + AgentOS

> Design Document for integrating v2 AgentOS components with v1 CodeAgent
> Author: Mind Avatar (Architect)
> Date: 2026-01-28

## Summary

This document designs a gradual integration strategy that allows CodeAgent to optionally use v2 runtime components (VCPU, KernelGate, MMU) while maintaining backward compatibility with v1. The integration uses Strategy Pattern for runtime abstraction, Adapter Pattern for tool bridging, and Factory Pattern for configuration-driven creation.

## Design

### Architecture Overview

```
                        ┌─────────────────────────────────────────┐
                        │              CodeAgent                  │
                        │         (Application Layer)             │
                        └───────────────┬─────────────────────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
                    ▼                   ▼                   ▼
           ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
           │ RuntimeStrategy│  │  ToolAdapter   │  │ MemoryAdapter  │
           │   (Protocol)   │  │   (Bridge)     │  │   (Bridge)     │
           └───────┬────────┘  └───────┬────────┘  └───────┬────────┘
                   │                   │                   │
        ┌──────────┴──────────┐       │          ┌────────┴────────┐
        │                     │       │          │                 │
        ▼                     ▼       ▼          ▼                 ▼
┌───────────────┐    ┌───────────────┐   ┌───────────────┐ ┌───────────────┐
│ V1 Runtime    │    │ V2 Runtime    │   │V1 Tiered      │ │  V2 MMU       │
│(SubagentRuntime)   │(AgentOS/VCPU) │   │MemoryManager  │ │               │
└───────────────┘    └───────────────┘   └───────────────┘ └───────────────┘
        │                     │
        │                     │
        ▼                     ▼
┌───────────────┐    ┌───────────────┐
│V1 ToolRegistry│───▶│V2 ToolRegistry│
│   (Source)    │Adapt│   (Target)   │
└───────────────┘    └───────────────┘
```

### Core Components

#### 1. RuntimeStrategy Protocol

A unified interface that both v1 and v2 runtimes implement:

```python
# src/nimbus/core/runtime/strategy.py
from typing import Protocol, AsyncIterator, Dict, Any
from nimbus.core.types import AgentResponse

class RuntimeStrategy(Protocol):
    """Protocol for runtime execution strategy."""

    async def execute(self, goal: str, context: str) -> AgentResponse:
        """Execute a goal and return the final response."""
        ...

    async def execute_stream(
        self, goal: str, context: str
    ) -> AsyncIterator[Dict[str, Any]]:
        """Execute with streaming events."""
        ...
```

#### 2. V1RuntimeAdapter

Wraps the existing SubagentRuntime to implement RuntimeStrategy:

```python
# src/nimbus/core/runtime/v1_adapter.py
class V1RuntimeAdapter:
    """Adapter that wraps V1 SubagentRuntime as RuntimeStrategy."""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        workspace: Path,
    ):
        self.runtime = SubagentRuntime(
            llm_client=llm_client,
            tool_registry=tool_registry,
            workspace=workspace,
        )

    async def execute(self, goal: str, context: str) -> AgentResponse:
        """Execute via V1 SubagentRuntime."""
        # Create TaskPlanner, plan DAG, execute
        ...

    async def execute_stream(
        self, goal: str, context: str
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream via V1 runtime.execute_stream()."""
        ...
```

#### 3. V2RuntimeAdapter

Wraps v2 AgentOS to implement RuntimeStrategy:

```python
# src/nimbus/core/runtime/v2_adapter.py
from nimbus.v2 import AgentOS, AgentOSConfig, ToolResult

class V2RuntimeAdapter:
    """Adapter that wraps V2 AgentOS as RuntimeStrategy."""

    def __init__(
        self,
        llm_client: LLMClient,  # V2 LLMClient protocol
        tool_adapter: "V1ToV2ToolAdapter",
        workspace: Path,
        config: Optional[AgentOSConfig] = None,
    ):
        self.os = AgentOS(
            llm_client=llm_client,
            tools={},  # Will be populated via adapter
            config=config,
        )
        self._register_adapted_tools(tool_adapter)

    def _register_adapted_tools(self, adapter: "V1ToV2ToolAdapter"):
        """Register all V1 tools via the adapter."""
        for name, func, desc, params in adapter.iterate_tools():
            self.os.register_tool(name, func, desc, params)

    async def execute(self, goal: str, context: str) -> AgentResponse:
        """Execute via V2 AgentOS."""
        result: ToolResult = await self.os.run(goal)
        return self._convert_result(result)

    async def execute_stream(
        self, goal: str, context: str
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream via V2 events."""
        # V2 currently runs synchronously, wrap in event stream
        pid = self.os.spawn(goal)
        # Convert V2 Event stream to V1 event format
        ...
```

#### 4. V1ToV2ToolAdapter

Bridges v1 ToolRegistry to v2 ToolRegistry format:

```python
# src/nimbus/core/runtime/tool_adapter.py
from nimbus.tools import ToolRegistry as V1ToolRegistry, ToolDefinition
from nimbus.v2 import ToolRegistry as V2ToolRegistry

class V1ToV2ToolAdapter:
    """Adapts V1 tools for use in V2 runtime."""

    def __init__(self, v1_registry: V1ToolRegistry):
        self.v1_registry = v1_registry

    def iterate_tools(self):
        """Yield (name, adapted_func, description, parameters) for each tool."""
        for name in self.v1_registry.list_tools():
            defn, func = self.v1_registry.get(name)
            adapted_func = self._wrap_v1_tool(name, func)
            params_schema = self._convert_params(defn)
            yield name, adapted_func, defn.description, params_schema

    def _wrap_v1_tool(self, name: str, func) -> callable:
        """Wrap V1 tool function for V2 execution context."""
        async def wrapper(**kwargs):
            # V1 tools expect **context, V2 passes flat kwargs
            # Strip V2-specific kwargs, add V1 context
            return await func(**kwargs)
        return wrapper

    def _convert_params(self, defn: ToolDefinition) -> dict:
        """Convert V1 ToolDefinition params to V2 JSON Schema."""
        properties = {}
        required = []
        for p in defn.parameters:
            properties[p.name] = p.to_json_schema()
            if p.required:
                required.append(p.name)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }
```

#### 5. LLMClientAdapter

Bridges v1 LLMClient to v2 LLMClient protocol:

```python
# src/nimbus/core/runtime/llm_adapter.py
from nimbus.v2.core.runtime.vcpu import LLMClient as V2LLMClient

class V1ToV2LLMAdapter:
    """Adapts V1 LLM client to V2 LLMClient protocol."""

    def __init__(self, v1_client):
        self.v1_client = v1_client

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> "LLMResponse":
        """Convert V1 complete() to V2 chat() interface."""
        # V1 uses complete(prompt) or complete_with_tools()
        # Convert messages to prompt format
        prompt = self._messages_to_prompt(messages)

        if tools:
            response = await self.v1_client.complete_with_tools(prompt, tools)
            return self._wrap_response(response)
        else:
            content = await self.v1_client.complete(prompt)
            return V2LLMResponse(content=content, tool_calls=None)
```

### Data Flow

```
User Input
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│                         CodeAgent.run()                         │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │ 1. Add to memory (v1 TieredMemory or v2 MMU)              │ │
│  │ 2. Get context                                            │ │
│  │ 3. Delegate to RuntimeStrategy.execute_stream()           │ │
│  └───────────────────────────────────────────────────────────┘ │
└──────────────────────────────┬──────────────────────────────────┘
                               │
            ┌──────────────────┴──────────────────┐
            │ config.runtime_version == "v2"?     │
            └──────────────────┬──────────────────┘
                    Yes │              │ No
                        ▼              ▼
            ┌───────────────┐  ┌───────────────┐
            │V2RuntimeAdapter│  │V1RuntimeAdapter│
            └───────┬───────┘  └───────┬───────┘
                    │                  │
                    ▼                  ▼
            ┌───────────────┐  ┌───────────────┐
            │   AgentOS     │  │SubagentRuntime│
            │    ┌─────┐    │  │               │
            │    │VCPU │    │  │               │
            │    │ ↓   │    │  │               │
            │    │Gate │    │  │               │
            │    │ ↓   │    │  │               │
            │    │MMU  │    │  │               │
            │    └─────┘    │  │               │
            └───────────────┘  └───────────────┘
```

## Decisions

### Decision 1: Strategy Pattern for Runtime Abstraction
- **Decision**: Use Strategy Pattern with RuntimeStrategy protocol
- **Rationale**:
  - Clean separation of concerns
  - Easy to test each runtime independently
  - CodeAgent becomes runtime-agnostic
- **Alternatives Considered**:
  - Inheritance (v2 extends v1): Too coupled, hard to maintain
  - Direct conditionals: Scattered if/else, poor maintainability
- **Risk**: May introduce slight overhead from abstraction layer

### Decision 2: Adapter Pattern for Tool Bridging
- **Decision**: Create V1ToV2ToolAdapter to wrap v1 tools for v2
- **Rationale**:
  - Zero changes to existing v1 tool implementations
  - V2 ToolRegistry expects simple `(name, func, desc, params)` registration
  - Adapter handles signature differences
- **Alternatives Considered**:
  - Rewrite all tools for v2: High effort, duplication
  - Dual registration: Maintenance burden
- **Risk**: Adapter may not handle all edge cases (e.g., context injection)

### Decision 3: Configuration-Driven Runtime Selection
- **Decision**: Add `runtime_version` field to CoreAgentConfig
- **Rationale**:
  - Gradual rollout via feature flag
  - Easy A/B testing
  - Per-session switching possible
- **Alternatives Considered**:
  - Environment variable: Less granular control
  - Compile-time switch: Requires rebuild
- **Risk**: Configuration complexity increases

### Decision 4: Preserve V1 as Default
- **Decision**: Keep v1 runtime as default, v2 is opt-in
- **Rationale**:
  - Production stability
  - V2 is still alpha (see `__version__ = "2.0.0-alpha"`)
  - Gradual validation path
- **Alternatives Considered**:
  - V2 as default: Too risky for production
- **Risk**: V2 adoption may be slow

## Tradeoffs

1. **Abstraction vs Performance**: Adding adapter layers introduces slight overhead, but gains flexibility and testability. Acceptable since LLM latency dominates.

2. **Compatibility vs Simplicity**: Maintaining dual runtime support adds complexity, but enables gradual migration without breaking changes.

3. **Feature Parity vs Incremental Value**: V2 has different capabilities (VCPU halting, structured Fault). Initial integration may not expose all v2 features to maintain v1 API contract.

4. **Tool Wrapping vs Native V2 Tools**: Adapting v1 tools means v2's stricter typing (ActionIR) is partially bypassed. Future tools should be v2-native.

## Constraints

### Technical Constraints
- V1 LLMClient uses `complete(prompt)` vs V2 uses `chat(messages, tools)`
- V1 tools receive `**context` kwargs, V2 tools receive flat `**args`
- V1 memory is string-based context, V2 MMU uses structured Messages

### Architectural Constraints
- Cannot break existing v1 API (backward compatibility)
- V2 EventStream must map to V1 event format for streaming
- Session management remains in v1 SessionManager

### Operational Constraints
- V2 is alpha, not production-ready
- Some v2 LLM clients (GeminiV2) are experimental

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| V2 runtime has undiscovered bugs | High | Medium | Keep v1 as default, extensive testing |
| Tool adapter misses edge cases | Medium | Medium | Integration tests for each tool |
| Memory adapter loses state | Medium | High | Add checkpoint compatibility tests |
| Performance regression | Low | Medium | Benchmark before enabling v2 |
| Configuration confusion | Medium | Low | Clear documentation, logging |

## Evidence

- **V2 AgentOS API**: `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/v2/agentos.py:306-344` - `run()` and `run_dag()` methods
- **V2 ToolRegistry**: `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/v2/agentos.py:147-236` - Tool registration interface
- **V1 CodeAgent**: `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/agent.py:381-488` - Current `run()` and `_run_task_mode()`
- **V1 ToolRegistry**: `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/tools/base.py:361-573` - Tool definition and execution
- **V2 LLMClient Protocol**: `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/v2/core/runtime/vcpu.py:58-81`
- **V2 Protocol Types**: `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/v2/core/protocol.py:42-275`

## Assumptions

1. **Assumption**: V1 tools do not rely on v1-specific context that cannot be provided by v2
   - *Validation needed*: Audit all tool implementations for context dependencies

2. **Assumption**: V2 AgentOS can handle the same workload patterns as V1 SubagentRuntime
   - *Validation needed*: Load testing with representative workloads

3. **Assumption**: V2 events can be mapped to V1 event format without loss
   - *Validation needed*: Event schema comparison

## Next Steps

### Phase 1: Infrastructure (Week 1-2)
1. [ ] Create `RuntimeStrategy` protocol in `src/nimbus/core/runtime/strategy.py`
2. [ ] Implement `V1RuntimeAdapter` wrapping SubagentRuntime
3. [ ] Add `runtime_version` to `CoreAgentConfig`
4. [ ] Unit tests for V1 adapter

### Phase 2: Tool Bridging (Week 2-3)
5. [ ] Implement `V1ToV2ToolAdapter`
6. [ ] Test each core tool (Read, Glob, Grep, Bash) through adapter
7. [ ] Implement `V1ToV2LLMAdapter` for LLM client bridging
8. [ ] Integration tests for tool execution

### Phase 3: V2 Integration (Week 3-4)
9. [ ] Implement `V2RuntimeAdapter` wrapping AgentOS
10. [ ] Wire up CodeAgent to use RuntimeStrategy based on config
11. [ ] End-to-end tests comparing v1 and v2 outputs
12. [ ] Performance benchmarking

### Phase 4: Validation (Week 4-5)
13. [ ] Run existing test suite with v2 runtime
14. [ ] Fix compatibility issues discovered
15. [ ] Documentation updates
16. [ ] Feature flag for gradual rollout

## Appendix: Configuration Schema

```yaml
# core.yaml addition
runtime:
  version: "v1"  # "v1" | "v2"
  v2_options:
    max_iterations: 50
    default_timeout: 60.0
    max_sub_call_depth: 10
```

## Appendix: File Structure

```
src/nimbus/core/runtime/
├── __init__.py
├── strategy.py           # RuntimeStrategy protocol (NEW)
├── v1_adapter.py         # V1RuntimeAdapter (NEW)
├── v2_adapter.py         # V2RuntimeAdapter (NEW)
├── tool_adapter.py       # V1ToV2ToolAdapter (NEW)
├── llm_adapter.py        # V1ToV2LLMAdapter (NEW)
├── executor.py           # (existing) AsyncRuntime
└── agentic.py            # (existing) AgenticRunner
```
