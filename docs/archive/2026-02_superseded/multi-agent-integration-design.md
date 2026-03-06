# Multi-Agent Integration Design (Simplified)

> Version: 2.0
> Date: 2025-01-25
> Author: Nimbus Architecture Team
> Status: Proposed
> Previous: v1.0 (BrainStage + ComplexityDetector approach - superseded)

## Summary

CodeAgent IS the Brain. This document redesigns the multi-agent architecture to align with the Wukong philosophy: the primary agent (CodeAgent) is the dispatcher, not an optional stage.

**Key Changes from v1.0:**
- ~~ComplexityDetector~~ - Removed (Brain decides, not rules)
- ~~BrainStage~~ - Removed (CodeAgent is already the Brain)
- Focus on **delegation mechanism** and **subagent configuration**
- Reuse existing `SubagentExecutor` and `SubagentConfig`

---

## Design Philosophy

### Wukong Principles Applied

```
Brain (CodeAgent) = Dispatcher + Verifier + Communicator
                  = NOT an optional stage
                  = ALWAYS runs first
```

| Principle | Application |
|-----------|-------------|
| Brain is the dispatcher | CodeAgent receives all requests, decides how to handle |
| Simple tasks: direct | Rule planner handles chat, simple queries |
| Complex tasks: delegate | Spawn coder/explorer/reviewer subagents |
| Brain verifies results | CodeAgent checks subagent outputs before returning |

### Why Not ComplexityDetector?

The v1.0 design added a rule-based ComplexityDetector to decide when to enable "Brain mode". This is backwards:

1. **The LLM is the best judge** - Claude/GPT can decide if a task needs delegation better than keyword rules
2. **Adds unnecessary latency** - Every request runs through complexity detection
3. **False dichotomy** - "Brain enabled" vs "Brain disabled" misses the point: Brain always runs

### Why Not BrainStage?

The v1.0 BrainStage generated strategy before execution. This is also unnecessary:

1. **CodeAgent already does this** - Planning/analysis is what the planner does
2. **Extra LLM call** - BrainStage = additional latency for every complex task
3. **Strategy injection is simpler** - Just include instructions in the subagent prompt

---

## Architecture

### Simplified Flow

```
                     ┌─────────────────────────────────┐
                     │         User Request            │
                     └──────────────┬──────────────────┘
                                    │
                                    ▼
                     ┌─────────────────────────────────┐
                     │     CodeAgent (= Brain)         │
                     │  ┌─────────────────────────┐    │
                     │  │   PlannerPipeline       │    │
                     │  │   - RulePlanner         │    │
                     │  │   - LLMEnhancer         │    │
                     │  └───────────┬─────────────┘    │
                     │              │                   │
                     │              ▼                   │
                     │  ┌─────────────────────────┐    │
                     │  │ Decision: Direct or     │    │
                     │  │ Delegate?               │    │
                     │  └───────────┬─────────────┘    │
                     └──────────────┼──────────────────┘
                                    │
               ┌────────────────────┴────────────────────┐
               │                                         │
               ▼ (simple/direct)               (complex) ▼
    ┌─────────────────────────┐         ┌─────────────────────────┐
    │   AsyncRuntime          │         │   SubagentExecutor      │
    │   (execute DAG)         │         │   (spawn subagent)      │
    └─────────────────────────┘         └───────────┬─────────────┘
                                                    │
                                                    ▼
                                        ┌─────────────────────────┐
                                        │   Coder Subagent        │
                                        │   - Isolated context    │
                                        │   - Restricted tools    │
                                        │   - Returns summary     │
                                        └───────────┬─────────────┘
                                                    │
                                                    ▼
                                        ┌─────────────────────────┐
                                        │   Brain Verifies        │
                                        │   (file exists? tests?) │
                                        └─────────────────────────┘
```

### How Delegation Works

**Option A: Explicit Delegation (Recommended for v1)**

The LLM (Claude/GPT) can explicitly decide to delegate by calling the Subagent tool:

```python
# When CodeAgent's LLM determines task is complex, it calls:
{
    "tool": "Subagent",
    "params": {
        "prompt": "Refactor APIClient.old_api() to new_api() across all files",
        "subagent_type": "coder",
        "description": "Refactor old_api"
    }
}
```

The key insight: **The LLM decides**, not a rule-based detector.

**Option B: System Prompt Guidance (Enhancement)**

Enhance CodeAgent's system prompt to guide delegation decisions:

```
You have access to specialized subagents. Use them for:
- coder: Multi-file code changes, refactoring, implementing features
- explorer: Understanding large codebases, finding patterns
- reviewer: Code review before committing

For simple tasks (questions, single-file edits), handle directly.
For complex tasks (multi-file refactoring, large features), delegate to subagent.
```

---

## Core Components (Reusing Existing Code)

### 1. CodeAgent (Unchanged)

Location: `src/nimbus/core/agent.py`

Already has:
- `spawn_subagent()` method
- `_subagent_registry` for loading subagent configs
- `_subagent_executor` for managing subagent lifecycle

No changes needed - CodeAgent is already the Brain.

### 2. SubagentExecutor (Unchanged)

Location: `src/nimbus/tools/subagent.py`

Already has:
- `spawn()` for creating subagents
- `SubagentContext` for isolated context
- Tool permission enforcement
- Foreground/background execution

### 3. SubagentConfig (Unchanged)

Location: `src/nimbus/core/agent_config.py`

Already has:
- YAML-based configuration
- `allowed_tools` list
- `prompt` for system instructions
- `max_turns` limit

### 4. Subagent YAML Configs (Existing)

Location: `src/nimbus/data/agents/`

- `coder.yaml` - Code implementation expert
- `explorer.yaml` - Code exploration expert
- `researcher.yaml` - Research with web search
- `reviewer.yaml` - Code review expert

---

## What Needs To Be Added

### 1. Enhanced Subagent Tool (Minor)

The current `Subagent` tool works, but could be enhanced:

```python
# Current subagent_type options
SUBAGENT_TOOL_PERMISSIONS = {
    "explorer": {"Read", "Glob", "Grep"},
    "researcher": {"Read", "Glob", "Grep", "WebSearch", "WebFetch"},
    "coder": {"Read", "Write", "Edit", "Bash", "Glob", "Grep"},
    "reviewer": {"Read", "Glob", "Grep"},
}
```

**Enhancement**: Support loading custom subagent types from registry:

```python
# Use registry to get tool permissions instead of hardcoded dict
def _validate_tools(self, subagent_type: str) -> Set[str]:
    config = self._registry.get(subagent_type)
    if config:
        return set(config.allowed_tools)
    return SUBAGENT_TOOL_PERMISSIONS.get(subagent_type, set())
```

### 2. System Prompt Enhancement

Add delegation guidance to CodeAgent's system prompt:

```python
DELEGATION_GUIDANCE = """
## Delegation to Subagents

You have specialized subagents available:

| Type | Use For | Tools |
|------|---------|-------|
| coder | Multi-file changes, refactoring, features | Read, Write, Edit, Bash |
| explorer | Finding code, understanding structure | Read, Glob, Grep |
| reviewer | Code review, quality checks | Read, Glob, Grep |

**When to Delegate:**
- Multi-file refactoring
- Complex feature implementation
- Tasks requiring >10 lines of code changes
- Tasks you're uncertain about

**When to Handle Directly:**
- Simple questions
- Single-file edits
- Reading/exploring code
- Explaining code

To delegate, use the Subagent tool with clear instructions.
"""
```

### 3. Verification Step (New Feature)

After subagent completes, Brain should verify:

```python
async def spawn_subagent_and_verify(
    self,
    prompt: str,
    subagent_type: str,
    verify: bool = True,
    **kwargs
) -> Dict[str, Any]:
    """Spawn subagent and optionally verify results."""
    result = await self.spawn_subagent(prompt, subagent_type, **kwargs)

    if verify and result.get("status") == "completed":
        # Extract files_modified from result
        modified_files = result.get("files_modified", [])

        # Verify files exist
        for file_path in modified_files:
            if not Path(file_path).exists():
                result["verification"] = "FAILED: File not found"
                return result

        # Optionally run tests
        if self._should_run_tests():
            test_result = await self._run_tests()
            result["test_result"] = test_result

    return result
```

---

## Decisions

### Decision 1: Remove ComplexityDetector

- **Decision**: Do not implement ComplexityDetector
- **Rationale**:
  1. LLM can judge complexity better than keyword rules
  2. Removes unnecessary latency
  3. Simplifies architecture
  4. Wukong philosophy: Brain (LLM) decides, not rules
- **Alternative**: Keep rule-based detection (rejected: over-engineering)
- **Risk**: LLM might delegate too much/too little (mitigate: tune system prompt)

### Decision 2: Remove BrainStage

- **Decision**: Do not implement BrainStage as a pipeline stage
- **Rationale**:
  1. CodeAgent already IS the Brain
  2. Extra LLM call adds latency without clear benefit
  3. Strategy can be embedded in subagent prompt
  4. Simpler is better for v1
- **Alternative**: Keep BrainStage (rejected: unnecessary complexity)
- **Risk**: May miss some strategy benefits (mitigate: enhance subagent prompts)

### Decision 3: Reuse Existing SubagentExecutor

- **Decision**: Use existing `SubagentExecutor` without major changes
- **Rationale**:
  1. Already implements isolation, permissions, lifecycle
  2. Already supports foreground/background execution
  3. Already has depth limiting (max 3 levels)
  4. Tested and working
- **Alternative**: Build new delegation system (rejected: reinventing the wheel)

### Decision 4: Configuration via YAML

- **Decision**: Subagent types defined in YAML, not hardcoded
- **Rationale**:
  1. User can define custom subagent types
  2. Easy to modify prompts and tool permissions
  3. Follows existing pattern in `src/nimbus/data/agents/`
- **Configuration Path**: `~/.nimbus/agents/` (user) or `src/nimbus/data/agents/` (built-in)

---

## Tradeoffs

### 1. LLM Decision vs Rule-Based Decision

| Approach | Pros | Cons |
|----------|------|------|
| LLM Decides | More accurate, adaptive | May vary, costs tokens |
| Rule-Based | Predictable, fast | Limited, may miss cases |

**Decision**: LLM decides. The extra tokens for the decision are negligible compared to the accuracy gain.

### 2. Single Coder vs Multiple Specialist Subagents

| Approach | Pros | Cons |
|----------|------|------|
| Single Coder | Simpler, one config | Less specialized |
| Multiple Types | Specialized prompts/tools | More configs to maintain |

**Decision**: Support multiple types (coder, explorer, reviewer) but recommend `coder` as default for most tasks.

### 3. Foreground vs Background Execution

| Mode | Use Case |
|------|----------|
| Foreground | Interactive, user waits for result |
| Background | Long tasks, multiple parallel subagents |

**Decision**: Default to foreground. Background is opt-in via `run_in_background=True`.

---

## Implementation Roadmap

### Phase 1: Enhance System Prompt (1 day)

1. Add delegation guidance to CodeAgent's system prompt
2. Test with various task types
3. Tune guidance based on results

### Phase 2: Registry Integration (1 day)

1. Modify `SubagentExecutor._validate_tools()` to use registry
2. Ensure custom subagent types work
3. Add tests for custom configurations

### Phase 3: Verification (2 days)

1. Implement `spawn_subagent_and_verify()`
2. Add file existence checks
3. Add optional test running
4. Add verification to response

### Phase 4: Documentation (1 day)

1. Update user guide
2. Document subagent configuration format
3. Add examples

---

## File Changes

### Modified Files

| File | Change |
|------|--------|
| `src/nimbus/tools/subagent.py` | Use registry for tool permissions |
| `src/nimbus/core/agent.py` | Add `spawn_subagent_and_verify()` |
| `src/nimbus/agents/default.yaml` | Add delegation guidance to prompt |

### New Files

None required - reuse existing infrastructure.

### Files NOT Changed (from v1.0)

| File | Reason |
|------|--------|
| `src/nimbus/core/multi_agent/complexity.py` | Not implementing |
| `src/nimbus/core/multi_agent/brain_stage.py` | Not implementing |
| `src/nimbus/core/planner/pipeline.py` | No BrainStage to add |

---

## Constraints

### Technical Constraints

- **Context Size**: Subagent context is a snapshot, not live. Must be sufficient for task.
- **Tool Permissions**: Subagent tools must be subset of parent's tools.
- **Depth Limit**: Max 3 levels of nested subagents (prevent runaway).

### Operational Constraints

- **Timeout**: Subagent has default 50 turns max. Long tasks may hit this limit.
- **Cost**: Each subagent is a separate LLM conversation. Multiple subagents = multiple costs.

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| LLM over-delegates | Medium | Low | Tune system prompt |
| LLM under-delegates | Medium | Medium | Add explicit delegation examples |
| Subagent fails silently | Low | High | Verification step |
| Context too limited | Medium | Medium | Include key files in snapshot |
| User confusion about delegation | Medium | Low | Clear documentation |

---

## Evidence

### Sources

- **Wukong Protocol**: `/Users/wangqing/.claude/rules/00-wukong-core.md`
  - "Brain = dispatcher, not executor"
  - "Simple tasks direct, complex tasks delegate"
  - "Brain verifies results"

- **Existing Code**:
  - `src/nimbus/tools/subagent.py:296-766` - SubagentExecutor (working implementation)
  - `src/nimbus/core/agent_config.py:42-156` - SubagentConfig (YAML-based config)
  - `src/nimbus/core/agent.py:1043-1216` - spawn_subagent() (already implemented)

- **Existing Configs**:
  - `src/nimbus/data/agents/coder.yaml` - Coder subagent definition
  - `src/nimbus/data/agents/explorer.yaml` - Explorer subagent definition

### Assumptions

1. **LLM Quality**: Assume Claude/GPT can make good delegation decisions with proper prompting
2. **Task Granularity**: Assume most tasks can be handled by a single subagent spawn
3. **Context Sufficiency**: Assume 5-turn history snapshot is sufficient for most subtasks

---

## Next Steps

1. [x] ~~Design v2.0~~ (this document)
2. [ ] Review with team
3. [ ] Implement Phase 1 (system prompt enhancement)
4. [ ] Test with benchmark tasks
5. [ ] Implement Phase 2-4 based on results

---

## Appendix

### A. Subagent Configuration Format

```yaml
# ~/.nimbus/agents/my-specialist.yaml
name: my-specialist
description: "Custom specialist for specific tasks"
mode: subagent
allowed_tools:
  - Read
  - Write
  - Glob
  - Grep
prompt: |
  You are a specialist in [domain].

  Your responsibilities:
  - [Responsibility 1]
  - [Responsibility 2]

  Your constraints:
  - [Constraint 1]
  - [Constraint 2]

  Output format:
  - [Format requirements]
max_turns: 30
```

### B. Delegation Examples in System Prompt

```markdown
## Delegation Examples

**Should Delegate (coder):**
- "Rename old_api() to new_api() across all files"
- "Add logging to all API endpoints"
- "Refactor the authentication module"

**Should NOT Delegate (handle directly):**
- "What does this function do?"
- "Fix the typo on line 42"
- "List all Python files in src/"

**Delegation Call:**
To delegate, use the Subagent tool:
- prompt: Clear task description
- subagent_type: coder/explorer/reviewer
- description: Short summary for status display
```

### C. Verification Checklist

After subagent completes, Brain verifies:

```
[ ] Files modified exist on disk
[ ] No syntax errors (try to read back)
[ ] Tests pass (if applicable)
[ ] Summary matches expected outcome
```
