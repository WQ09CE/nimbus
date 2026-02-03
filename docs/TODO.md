# Nimbus Project TODOs

Last Updated: 2026-02-03

## High Priority

### 1. Session Persistence & Hibernation (Session V2)
Currently, `checkpoint_manager.py` and `sqlite.py` provide full support for saving/restoring session state (including vCPU execution state and MMU history), but `session_v2.py` does not utilize them.

- **Goal**: Enable "Hibernate" and "Wake" features for sessions.
- **Tasks**:
  - Update `SessionManager.interrupt_session` to call `agent_os.create_checkpoint` and save to DB.
  - Implement `SessionManager.resume_session` (or `wake`) to load from DB checkpoint.
  - Expose API endpoints for explicit hibernation/resumption.
- **Status**: Underlying logic verified in `tests/e2e_checkpoint.py`. Integration missing.

## Medium Priority

### 2. Tool Dispatcher Extraction (Refactor P5)
Original VCPU refactor plan included extracting tool dispatch logic into a separate `ToolDispatcher` class. This was skipped (demoted to P5) due to diminishing returns and state coupling complexity.

- **Goal**: Further reduce VCPU complexity (~100 lines).
- **Tasks**:
  - Move `_handle_tool_call` and related logic to `ToolDispatcher`.
  - Pass `ActionContext` to dispatcher.
- **Status**: Skipped for now. Revisit if VCPU grows too large again.

### 3. Empty Result Handling Refinement
`EmptyResultHandler` was extracted but `_handle_empty_result` in VCPU still contains some logic (auto-recovery execution).

- **Goal**: Move all execution logic into `EmptyResultHandler` or `RecoveryExecutor`.
- **Tasks**:
  - Refactor `_handle_empty_result` to fully delegate to components.

## Completed (Recent)

- [x] **VCPU Refactor**: Reduced from 1759 to ~1400 lines. Extracted `RecoveryExecutor`, `CheckpointManager`, `EmptyResultHandler`.
- [x] **Infinite Context Fix**: Implemented smart summary budget + LLM re-compression to prevent unbounded growth.
- [x] **Message Ordering Fix**: Fixed bug where user messages were injected between tool calls and results.
- [x] **Linting**: Fixed all ruff issues across the codebase.
