# Nimbus Agent Framework

A notebook-style AI assistant framework with DAG planning and tiered memory management.

## Installation

```bash
pip install -e .
```

## Quick Start

### Start the Server

```bash
nimbus serve --port 8080
```

### Manage Sessions

```bash
# List sessions
nimbus session list

# Create a session
nimbus session create --name "my-project"

# Delete a session
nimbus session delete <session_id>
```

### Configuration

```bash
# Show configuration
nimbus config show

# Set configuration
nimbus config set default_memory_type tiered
```

## Features

- DAG-based task planning and parallel execution
- Tiered memory management (pinned, working, episodic)
- RESTful API with SSE streaming
- Permission system for tool execution control
- SQLite-based session persistence
