# Nimbus Web UI Enhancement & Refactoring

I have completed a comprehensive diagnosis and refactoring of the Nimbus Web UI and Core communication layer.

## 1. Diagnosis Results

The issues you reported were caused by three main factors:

1.  **Blocking Execution (The "Stuck" Issue)**:
    -   The `SessionManagerV2` was waiting for `agent_os.chat()` to fully complete before sending *any* events to the UI.
    -   For complex tasks, this meant the UI received no updates for a long time, then everything appeared at once.

2.  **Missing Data**:
    -   The `KernelGate` (which executes tools) was not including tool arguments in `TOOL_STARTED` events.
    -   It was also not including the tool output in `TOOL_FINISHED` events.
    -   This made it impossible for the UI to show what tools were doing.

3.  **Basic UI Implementation**:
    -   The `ChatMessage` component was too simple, hiding critical information and not linking tool calls to their results effectively.

## 2. Key Changes & Enhancements

### Backend (Core & Server)

*   **Real-time Streaming**:
    *   Modified `SimpleEventStream` (`src/nimbus/v2/os/gate.py`) to support event listeners.
    *   Updated `AgentOS` (`src/nimbus/v2/agentos.py`) to expose these listeners.
    *   Refactored `SessionManagerV2.stream_chat` (`src/nimbus/server/session_v2.py`) to stream events *immediately* as they happen, solving the "stuck" feeling.

*   **Rich Data Transmission**:
    *   Updated `KernelGate` (`src/nimbus/v2/os/gate.py`) to include full `args` in start events and `output` in finish events.
    *   Fixed event type mapping in `SessionManagerV2` so tool events are correctly identified by the frontend.

### Frontend (Web UI)

*   **Enhanced Data Store**:
    *   Updated `chat-store.ts` to correctly parse and map the new rich data fields from the server.

*   **Premium Chat Component**:
    *   Rewrote `ChatMessage.tsx` to feature:
        *   **Timeline View**: Tool calls and results are merged into a cohesive timeline.
        *   **Live Status**: Real-time "RUNNING", "OK", "ERR" badges.
        *   **Expandable Details**: Clean visualization of input arguments and outputs/errors.
        *   **Better Styling**: Improved aesthetics with glassmorphism effects and clearer typography.

## 3. How to Verify

1.  Restart the Nimbus server.
2.  Refresh the Web UI.
3.  Ask a complex question (e.g., "Analyze the `src` directory and summarize the architecture").
4.  You will now see:
    *   Immediate feedback when tools start.
    *   Arguments displayed while the tool is running.
    *   Results appearing instantly upon completion.
    *   No more "freezing" during execution.
