# Nimbus Sandbox Testing

Sandbox testing framework for Nimbus - integration tests using real LLM backends.

## Overview

The sandbox testing framework allows you to test CodeAgent functionality with actual LLM providers (Ollama, Gemini, OpenRouter) in isolated temporary workspaces. This bypasses the HTTP server layer and tests the core agent capabilities directly.

## Quick Start

```bash
# Enable sandbox tests
export NIMBUS_SANDBOX_TESTS=1

# Configure LLM (default: ollama with qwen3:8b)
export NIMBUS_TEST_PROVIDER=ollama
export NIMBUS_TEST_MODEL=qwen3:8b

# Run all sandbox tests
pytest tests/sandbox/ -v

# Run specific test file
pytest tests/sandbox/test_file_operations.py -v

# Run with verbose output
pytest tests/sandbox/ -v -s
```

## Using Different LLM Providers

### Ollama (Default - Local)

```bash
# Make sure Ollama is running
ollama serve

# Pull a model if needed
ollama pull qwen3:8b

# Run tests
export NIMBUS_SANDBOX_TESTS=1
export NIMBUS_TEST_PROVIDER=ollama
export NIMBUS_TEST_MODEL=qwen3:8b
pytest tests/sandbox/ -v
```

### Gemini (Google AI)

```bash
export NIMBUS_SANDBOX_TESTS=1
export NIMBUS_TEST_PROVIDER=gemini
export GEMINI_API_KEY=your-api-key
pytest tests/sandbox/ -v
```

### OpenRouter

```bash
export NIMBUS_SANDBOX_TESTS=1
export NIMBUS_TEST_PROVIDER=openrouter
export NIMBUS_TEST_MODEL=anthropic/claude-3-haiku
export OPENROUTER_API_KEY=your-api-key
pytest tests/sandbox/ -v
```

## Writing Sandbox Tests

### Basic Test Structure

```python
import pytest
from tests.sandbox.runner import SandboxRunner

pytestmark = pytest.mark.sandbox  # Mark all tests in file as sandbox tests


@pytest.mark.asyncio
async def test_my_scenario(llm_provider, llm_model):
    """Test description."""
    async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
        # 1. Setup workspace files
        runner.create_file("main.py", "def hello(): pass")

        # 2. Run agent task
        response = await runner.run("Add a docstring to the hello function")

        # 3. Verify results
        assert runner.file_exists("main.py")
        content = runner.read_file("main.py")
        assert '"""' in content or "'''" in content
```

### Using Sample Files

```python
from tests.sandbox.scenarios.sample_files import (
    PYTHON_SIMPLE,
    PYTHON_WITH_BUG,
    create_sample_project,
)

async def test_with_sample_project(llm_provider, llm_model):
    async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
        # Create a complete sample project
        create_sample_project(runner.workspace)

        # Or use individual file contents
        runner.create_file("main.py", PYTHON_SIMPLE)
```

### Keeping Workspace for Debugging

```python
async def test_debug_scenario(llm_provider, llm_model):
    async with SandboxRunner(
        provider=llm_provider,
        model=llm_model,
        keep_workspace=True,  # Don't delete after test
    ) as runner:
        runner.create_file("test.py", "# test")
        response = await runner.run("Modify test.py")

        # Print workspace location for inspection
        print(f"Workspace: {runner.workspace}")
```

### Testing with Streaming

```python
async def test_streaming(llm_provider, llm_model):
    async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
        runner.create_file("main.py", "def hello(): pass")

        events = []
        async for status in runner.run_stream("Add docstring"):
            events.append(status)
            print(f"Status: {status}")

        # Check events were received
        assert any(e.get("type") == "complete" for e in events)
```

## SandboxRunner API

### Constructor Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | str | "ollama" | LLM provider name |
| `model` | str | None | Model name (uses provider default if None) |
| `workspace_prefix` | str | "nimbus_sandbox_" | Prefix for temp directory |
| `keep_workspace` | bool | False | Don't delete workspace after test |
| `memory_type` | str | "simple" | Agent memory type (simple, tiered) |
| `planner_type` | str | "dag" | Agent planner type (simple, dag) |
| `tools` | List[str] | None | Tools to register (all if None) |

### Methods

| Method | Description |
|--------|-------------|
| `run(task)` | Run agent with task, return AgentResponse |
| `run_stream(task)` | Run agent with streaming status updates |
| `create_file(path, content)` | Create file in workspace |
| `create_files(dict)` | Create multiple files |
| `read_file(path)` | Read file content |
| `file_exists(path)` | Check if file exists |
| `list_files(pattern)` | List files matching glob pattern |
| `get_file_tree()` | Get formatted file tree string |
| `cleanup()` | Manually clean up workspace |

## Test Markers

- `@pytest.mark.sandbox` - Mark as sandbox test (skipped unless NIMBUS_SANDBOX_TESTS=1)
- `@pytest.mark.slow` - Mark as slow-running test
- `@pytest.mark.asyncio` - Mark as async test (required for all sandbox tests)

## Directory Structure

```
tests/sandbox/
|-- __init__.py              # Package init
|-- conftest.py              # Pytest fixtures and markers
|-- runner.py                # SandboxRunner class
|-- workspace/               # Temp workspace directory (gitignored)
|-- scenarios/               # Test scenarios and sample files
|   |-- __init__.py
|   `-- sample_files.py      # Sample file contents and generators
|-- test_file_operations.py  # File read/write/edit tests
|-- test_code_analysis.py    # Code understanding and analysis tests
|-- test_multi_agent.py      # Multi-file refactoring tests
`-- README.md                # This file
```

## Tips

1. **Start with simpler models**: Local models like qwen3:8b are faster and free for initial testing
2. **Use keep_workspace=True for debugging**: Inspect the actual files when tests fail
3. **Write flexible assertions**: LLM responses can vary, check for key concepts rather than exact text
4. **Test one capability at a time**: Smaller, focused tests are more reliable
5. **Use @pytest.mark.slow for expensive tests**: Run them separately with `pytest -m slow`

## Troubleshooting

### Tests Skipped

Make sure `NIMBUS_SANDBOX_TESTS=1` is set:

```bash
export NIMBUS_SANDBOX_TESTS=1
```

### Connection Errors

For Ollama, ensure the server is running:

```bash
ollama serve
```

For cloud providers, check your API key is set correctly.

### Timeouts

Some tests with larger models may timeout. Increase the default timeout:

```bash
pytest tests/sandbox/ -v --timeout=300
```

### Model Not Found

Pull the model first for Ollama:

```bash
ollama pull qwen3:8b
```
