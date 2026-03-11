# Spawn Agent Design Document (Nimbus)

## Overview
`spawn_agent` is a core tool that allows a primary agent to instantiate dedicated sub-agents for isolated or complex tasks. This design adheres to Nimbus' minimalist architecture by treating sub-agents as regular tool calls with strong state externalization.

## Core Principles
1. **Tool-based Instantiation**: The parent agent calls `spawn_agent(task, role)` just like any other tool. It blocks until the sub-agent completes or times out.
2. **State Externalization (Scratchpad)**: Sub-agents do not return massive contexts via tool outputs. Instead, they write their findings and progress to a dedicated scratchpad (`.nimbus/sessions/<sub_session_id>/scratchpad.md`).
3. **Controlled Role Mapping**: The parent agent assigns a semantic `role` (e.g., `coder`, `reviewer`), NOT a specific `model_id`. The framework maps roles to models to ensure stability and cost control.
4. **Resilient Timeout Recovery**: If a sub-agent times out, the parent agent receives a timeout response pointing to the sub-agent's scratchpad, allowing it to recover partial progress seamlessly.

## Configuration: Role to Model Mapping
To prevent the parent agent from hallucinating model IDs or making poor routing decisions, model selection is strictly controlled via framework configuration.

The system uses a 3-tier minimalist OS architecture (`core`, `reader`, `worker`):
- **`core`** (Main Agent): Model is dynamically determined by the Session/Web-UI setting for maximum flexibility. It handles planning, maintains the scratchpad, and spawns sub-agents.
- **`reader`** (Sub-agent): Model is strictly defined in config (e.g., `gemini-3-flash-preview`). Has read-only tools (`Grep`, `Read`).
- **`worker`** (Sub-agent): Model is strictly defined in config (e.g., `gemini-3-flash-preview`). Has execution tools (`Write`, `Edit`, `Bash`).

**Location**: `~/.nimbus/config.json`

```json
{
  "agent_roles": {
    "reader": "gemini-3-flash-preview",
    "worker": "gemini-3-flash-preview"
  }
}
```

*When `spawn_agent` is called, it reads this configuration to instantiate the sub-agent with the correct model based on the requested role.*

## Tool Definition (`spawn_agent`)

### Parameters
- **`task`** (string, required): Detailed instructions and context for the sub-agent.
- **`role`** (string, required): The semantic role for the sub-agent. MUST be either `reader` or `worker`.
- **`timeout_seconds`** (integer, optional): Maximum execution time. Defaults to `600`.

### Execution Flow
1. **Init**: Generate a unique `sub_session_id`.
2. **Config**: Resolve `role` to `model_id` using `~/.nimbus/config.json`.
3. **Workspace**: The framework naturally handles the sub-agent's scratchpad at `.nimbus/sessions/<sub_session_id>/scratchpad.md`.
4. **Execute**: Run the `VCPU` loop for the sub-agent asynchronously.
5. **Return**:
   - **Success**: Return a summary and the path to the sub-agent's scratchpad/artifacts.
   - **Timeout/Error**: Catch the exception and return a structured error pointing the parent agent to the scratchpad to recover partial progress.

## Handling Timeouts & Exceptions
Instead of crashing the parent agent, `spawn_agent` catches timeouts and returns a defensive response:

```json
{
  "status": "TIMEOUT",
  "message": "Sub-agent execution timed out after 600 seconds.",
  "recovery_action": "Read the sub-agent's scratchpad at `.nimbus/sessions/<sub_session_id>/scratchpad.md` to see its partial progress and decide how to continue."
}
```
The parent agent, guided by its System Prompt, will use the `Read` tool to inspect the scratchpad and take over the task.