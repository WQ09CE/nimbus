# Nimbus Agent Framework

> Production-ready AI Agent framework with DAG planning and tiered memory.

## Project Overview

**Nimbus** is a modular AI Agent framework (v0.2.0 Alpha) designed for code exploration, analysis, and task execution. It features a three-stage planning pipeline, parallel DAG execution, and multi-tier memory management.

**Core Capabilities:**
- DAG-based parallel task execution
- Rule-based fast path + LLM planning hybrid
- Four-tier memory with automatic compression
- Multi-protocol support (REST / OpenCode / ACP / AI SDK v6)
- Subagent system with permission isolation

## Architecture Overview

```
+-----------------------------------------------------------+
|                    CLI / HTTP API                          |
|  nimbus serve | /api/v1/* | /session/* | /acp/*           |
+-----------------------------------------------------------+
|                      Server Layer                          |
|  SessionManager | SSE Hub | PermissionManager             |
+-----------------------------------------------------------+
|                       CodeAgent                            |
|  +---------------+  +---------------+  +---------------+   |
|  | PlannerPipeline|  | AsyncRuntime |  | TieredMemory |   |
|  | - ContextAnalyzer| | - DAG Exec  |  | - Pinned 1K  |   |
|  | - RulePlanner    | | - Parallel  |  | - Working 4K |   |
|  | - LLMEnhancer    | | - Retry     |  | - Episodic 8K|   |
|  | - Validator      | +-------------+  | - Semantic 4K|   |
|  +---------------+                     +---------------+   |
+-----------------------------------------------------------+
|                   Skills & Tools                           |
|  Read | Glob | Grep | Bash | Subagent | WebSearch | ...   |
+-----------------------------------------------------------+
|                   Storage Layer                            |
|  SQLite (Sessions | Messages | DAGs | Checkpoints)        |
+-----------------------------------------------------------+
```

## Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `src/nimbus/core/agent.py` | ~1200 | **CodeAgent** - Main orchestrator |
| `src/nimbus/core/planner/pipeline.py` | ~380 | **PlannerPipeline** - 3-stage planning |
| `src/nimbus/core/planner/rule_planner.py` | ~510 | **RulePlanner** - 28+ pattern rules |
| `src/nimbus/core/runtime/executor.py` | ~660 | **AsyncRuntime** - DAG parallel executor |
| `src/nimbus/core/memory.py` | ~1075 | **TieredMemoryManager** - 4-tier memory |
| `src/nimbus/core/types.py` | ~300 | Core data types (TaskDAG, TaskNode, etc.) |
| `src/nimbus/server/app.py` | ~150 | FastAPI application factory |
| `src/nimbus/server/api.py` | ~250 | REST API routes |
| `src/nimbus/server/compat/opencode.py` | ~400 | OpenCode TUI compatibility |
| `src/nimbus/tools/__init__.py` | ~200 | Tool registry and decorators |

## Development Guide

### Installation

```bash
# Basic installation
pip install -e .

# With LLM providers
pip install -e ".[llm]"

# Full installation (dev + llm + rag)
pip install -e ".[all]"
```

### Running the Server

```bash
# Default port 4096 (OpenCode compatible)
nimbus serve

# Custom port
nimbus serve --port 8080
```

### Running Tests

```bash
# Unit tests (fast, no LLM required)
pytest tests/ -v

# Skip slow/LLM tests
pytest tests/ -v -m "not slow"

# E2E tests (requires running server)
python tests/e2e_readonly_agent.py
python tests/e2e_context_test.py
python tests/e2e_tiered_memory.py

# Capability tests
pytest tests/capabilities/ -v
```

### Code Style

- **Formatter**: ruff (line-length=100)
- **Type Checker**: mypy (strict mode)
- **Python**: 3.10+ required

```bash
# Format code
ruff format src/ tests/

# Check types
mypy src/nimbus/
```

### LLM Configuration

Create `llm.yaml` in project root:

```yaml
default_provider: anthropic

providers:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-sonnet-4-20250514

  openai:
    api_key: ${OPENAI_API_KEY}
    model: gpt-4o
```

## Key Concepts

### Planning Pipeline

```
User Goal
    |
    v
+---------------+
| try_rule_match| ---- Fast path (skip context construction)
+---------------+
    | (miss)
    v
+---------------+
|ContextAnalyzer| ---- Detect context-dependent questions
+---------------+
    |
    v
+---------------+
|  RulePlanner  | ---- Pattern matching (28+ rules)
+---------------+
    |
    v
+---------------+
|  LLMEnhancer  | ---- LLM-based DAG generation
+---------------+
    |
    v
+---------------+
|   Validator   | ---- Validate and repair DAG
+---------------+
    |
    v
  TaskDAG
```

### Memory Tiers

| Tier | Budget | Purpose | Compression |
|------|--------|---------|-------------|
| Pinned | 1K tokens | Critical info (workspace, instructions) | Never |
| Working | 4K tokens | Current task state | Manual |
| Episodic | 8K tokens | Conversation history | Auto (every 6 turns) |
| Semantic | 4K tokens | RAG cache | LRU eviction |

### Subagent Types

| Type | Purpose | Allowed Tools |
|------|---------|---------------|
| eye | Code exploration | Read, Glob, Grep |
| body | Code implementation | Read, Write, Edit, Bash |
| mind | Architecture design | Read, Write, Glob, Grep |
| tongue | Testing | Read, Glob, Bash |
| nose | Code review | Read, Glob, Grep |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | GET | Health check |
| `/api/v1/sessions` | POST | Create session |
| `/api/v1/sessions/{id}/chat` | POST | Chat (SSE stream) |
| `/session` | POST | Create session (OpenCode) |
| `/session/{id}/message` | POST | Send message (OpenCode SSE) |
| `/acp/v1/agents` | GET | List agents (ACP) |
| `/v1/chat/completions` | POST | Chat completions (AI SDK v6) |

## Common Patterns

### Adding a New Tool

```python
# src/nimbus/tools/my_tool.py
from nimbus.tools import tool

@tool(
    name="MyTool",
    description="Does something useful",
    parameters={
        "param1": {"type": "string", "description": "First param"},
    }
)
async def my_tool(param1: str, workspace: Path) -> str:
    return f"Result: {param1}"

# Register in agent.py _create_default_tools()
registry.register_decorated(my_tool)
```

### Adding a Planning Rule

```python
# In src/nimbus/core/planner/rule_planner.py PLANNING_RULES
{
    "name": "my_pattern",
    "pattern": r"^(?:do|execute)\s+(.+)$",
    "mode": "dag",
    "tasks": [
        {"skill": "MyTool", "params_template": {"param1": "$1"}},
    ],
}
```

### Adding a Skill

```python
# In src/nimbus/skills/my_skill.py
async def my_skill(query: str, **kwargs) -> str:
    return f"Processed: {query}"

# Register in agent
agent.register_skill("my_skill", my_skill)
```

## Testing Patterns

### Unit Test Example

```python
# tests/test_my_feature.py
import pytest
from nimbus.core.planner import RulePlanner

@pytest.mark.asyncio
async def test_my_rule():
    planner = RulePlanner()
    ctx = PlanningContext(goal="do something", ...)
    result = await planner.process(ctx)
    assert result.rule_dag is not None
```

### E2E Test Example

```python
# tests/e2e_my_test.py
async def test_end_to_end():
    async with httpx.AsyncClient() as client:
        # Create session
        resp = await client.post(f"{BASE_URL}/session")
        session_id = resp.json()["session_id"]

        # Send message
        async with client.stream("POST", f"{BASE_URL}/session/{session_id}/message", json={"content": "test"}) as stream:
            async for line in stream.aiter_lines():
                # Process SSE events
                pass
```

## Troubleshooting

### Common Issues

1. **"Unknown skill or tool"** - Ensure tool is registered in `_create_default_tools()`
2. **Empty DAG** - Check rule patterns in `rule_planner.py`
3. **Memory overflow** - Check token budgets in `MemoryConfig`
4. **Timeout errors** - Adjust `RuntimeConfig.default_timeout`

### Debug Logging

```python
# Enable debug logging
import logging
logging.getLogger("nimbus").setLevel(logging.DEBUG)

# Or via environment
export NIMBUS_LOG_LEVEL=DEBUG
```

## License

MIT
