/**
 * SSE Scenario Library — Reusable event sequences for testing chat-store.
 *
 * Each scenario is an array of SSE events { event, data, delayMs? }.
 * Use `createSSEHandler()` to convert a scenario into an msw handler.
 */

import { http, HttpResponse } from 'msw'

// ============================================================
// Types
// ============================================================

export interface SSEEvent {
    event: string
    data: any
    delayMs?: number // Optional delay before this event
}

// ============================================================
// Helper: Create an msw handler from an SSE event sequence
// ============================================================

/**
 * Creates an msw POST handler that streams the given SSE events.
 * Use with `server.use(createSSEHandler(scenario))` in tests.
 */
export function createSSEHandler(
    events: SSEEvent[],
    path = '/api/v1/sessions/:id/chat'
) {
    return http.post(path, () => {
        const stream = new ReadableStream({
            async start(controller) {
                const encoder = new TextEncoder()
                for (const evt of events) {
                    if (evt.delayMs) {
                        await new Promise(r => setTimeout(r, evt.delayMs))
                    }
                    const payload = `event: ${evt.event}\ndata: ${JSON.stringify(evt.data)}\n\n`
                    controller.enqueue(encoder.encode(payload))
                }
                controller.close()
            },
        })
        return new HttpResponse(stream, {
            headers: { 'Content-Type': 'text/event-stream' },
        })
    })
}

/**
 * Creates an msw GET handler for SSE event subscription (reconnection).
 */
export function createSSEEventsHandler(events: SSEEvent[]) {
    return createSSEHandler(events, '/api/v1/sessions/:id/events')
}

/**
 * Creates a long-running SSE stream that stays open (never closes).
 * Useful for testing injection, interrupt, and watchdog scenarios.
 */
export function createOpenStreamHandler(
    initialEvents: SSEEvent[] = [{ event: 'connected', data: {} }]
) {
    return http.post('/api/v1/sessions/:id/chat', () => {
        const stream = new ReadableStream({
            start(controller) {
                const encoder = new TextEncoder()
                for (const evt of initialEvents) {
                    const payload = `event: ${evt.event}\ndata: ${JSON.stringify(evt.data)}\n\n`
                    controller.enqueue(encoder.encode(payload))
                }
                // Stream stays open — never calls controller.close()
            },
        })
        return new HttpResponse(stream, {
            headers: { 'Content-Type': 'text/event-stream' },
        })
    })
}

// ============================================================
// Scenarios
// ============================================================

/** Simple text-only response */
export const SIMPLE_TEXT: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'message', data: { content: 'Hello, world!' } },
    { event: 'done', data: { status: 'OK' } },
]

/** Multi-chunk text response */
export const MULTI_CHUNK_TEXT: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'message', data: { content: 'Hello' } },
    { event: 'message', data: { content: ', ' } },
    { event: 'message', data: { content: 'world!' } },
    { event: 'done', data: { status: 'OK' } },
]

/** Tool call with result */
export const TOOL_CALL_WITH_RESULT: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'tool_call', data: { id: 'tc-1', name: 'Bash', arguments: { command: 'echo hello' } } },
    { event: 'tool_result', data: { id: 'tc-1', name: 'Bash', output: 'hello\n', status: 'OK' } },
    { event: 'message', data: { content: 'Command executed successfully.' } },
    { event: 'done', data: { status: 'OK' } },
]

/** Tool call with streaming output chunks */
export const TOOL_STREAMING_OUTPUT: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'tool_call', data: { id: 'tc-1', name: 'Bash', arguments: { command: 'ls -la' } } },
    { event: 'tool_output_chunk', data: { id: 'tc-1', action_id: 'tc-1', tool: 'Bash', chunk: 'file1.txt\n' } },
    { event: 'tool_output_chunk', data: { id: 'tc-1', action_id: 'tc-1', tool: 'Bash', chunk: 'file2.txt\n' } },
    { event: 'tool_output_chunk', data: { id: 'tc-1', action_id: 'tc-1', tool: 'Bash', chunk: 'file3.txt\n' } },
    { event: 'tool_result', data: { id: 'tc-1', name: 'Bash', output: 'file1.txt\nfile2.txt\nfile3.txt\n', status: 'OK' } },
    { event: 'message', data: { content: 'Found 3 files.' } },
    { event: 'done', data: { status: 'OK' } },
]

/** Multiple parallel tool calls */
export const PARALLEL_TOOL_CALLS: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'tool_call', data: { id: 'tc-1', name: 'Read', arguments: { path: '/a.txt' } } },
    { event: 'tool_call', data: { id: 'tc-2', name: 'Read', arguments: { path: '/b.txt' } } },
    { event: 'tool_result', data: { id: 'tc-1', name: 'Read', output: 'content-a', status: 'OK' } },
    { event: 'tool_result', data: { id: 'tc-2', name: 'Read', output: 'content-b', status: 'OK' } },
    { event: 'message', data: { content: 'Read both files.' } },
    { event: 'done', data: { status: 'OK' } },
]

/** Tool result arrives before tool call (race condition) */
export const TOOL_RESULT_BEFORE_CALL: SSEEvent[] = [
    { event: 'connected', data: {} },
    // Result arrives first (edge case from backend parallel dispatch)
    { event: 'tool_result', data: { id: 'tc-1', name: 'Write', output: 'ok', status: 'OK' } },
    { event: 'tool_call', data: { id: 'tc-1', name: 'Write', arguments: { path: '/tmp/x.txt' } } },
    { event: 'message', data: { content: 'File written.' } },
    { event: 'done', data: { status: 'OK' } },
]

/** Usage update event */
export const WITH_USAGE_UPDATE: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'message', data: { content: 'Hi there!' } },
    {
        event: 'usage_update', data: {
            step_usage: { input: 100, output: 50, cache_read: 20, cache_write: 0, total: 170, cost: { input: 0.001, output: 0.0005, cache_read: 0, cache_write: 0, total: 0.0015 } },
            cumulative_usage: { input: 100, output: 50, cache_read: 20, cache_write: 0, total: 170, cost: { input: 0.001, output: 0.0005, cache_read: 0, cache_write: 0, total: 0.0015 } },
        }
    },
    { event: 'done', data: { status: 'OK' } },
]

/** Error event */
export const STREAM_ERROR: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'error', data: { message: 'Server disconnected' } },
]

/** Text then tool then text (interleaved) */
export const TEXT_TOOL_TEXT: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'message', data: { content: 'Let me check the file.' } },
    { event: 'tool_call', data: { id: 'tc-1', name: 'Read', arguments: { path: '/tmp/a.txt' } } },
    { event: 'tool_result', data: { id: 'tc-1', name: 'Read', output: 'file content here', status: 'OK' } },
    { event: 'message', data: { content: ' The file contains the expected data.' } },
    { event: 'done', data: { status: 'OK' } },
]

/**
 * Multi-step turn: text → tool → text → tool → text, with DISTINCT tool ids.
 * Guards interleaving order (regression: colliding action_ids merged the second
 * tool into the first card and clumped messages — fixed by unique ids backend-side).
 */
export const MULTI_STEP_INTERLEAVE: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'message', data: { content: 'Step one.' } },
    { event: 'tool_call', data: { action_id: 'json_extract_txt_0_aaaa', tool: 'Bash', args: { command: 'echo AAA' } } },
    { event: 'tool_result', data: { action_id: 'json_extract_txt_0_aaaa', tool: 'Bash', output: 'AAA', status: 'OK' } },
    { event: 'message', data: { content: 'Step two.' } },
    { event: 'tool_call', data: { action_id: 'json_extract_txt_0_bbbb', tool: 'Bash', args: { command: 'echo BBB' } } },
    { event: 'tool_result', data: { action_id: 'json_extract_txt_0_bbbb', tool: 'Bash', output: 'BBB', status: 'OK' } },
    { event: 'message', data: { content: 'Done.' } },
    { event: 'done', data: { status: 'OK' } },
]

/** Heartbeat events (keep-alive) */
export const WITH_HEARTBEATS: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'heartbeat', data: { kind: 'THOUGHT', content: 'Thinking...' } },
    { event: 'heartbeat', data: { kind: 'WORKING' } },
    { event: 'message', data: { content: 'Done thinking.' } },
    { event: 'done', data: { status: 'OK' } },
]

/** User message from another client (multi-tab) */
export const REMOTE_USER_MESSAGE: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'user_message', data: { content: 'Hello from tablet', injected: true } },
    { event: 'message', data: { content: 'I see your message.' } },
    { event: 'done', data: { status: 'OK' } },
]

/** Tool call with error result */
export const TOOL_ERROR: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'tool_call', data: { id: 'tc-1', name: 'Bash', arguments: { command: 'cat /nonexistent' } } },
    { event: 'tool_result', data: { id: 'tc-1', name: 'Bash', output: '', status: 'ERROR', fault: { message: 'File not found' } } },
    { event: 'message', data: { content: 'The file does not exist.' } },
    { event: 'done', data: { status: 'OK' } },
]

/** Empty content chunks (should be filtered) */
export const EMPTY_CHUNKS: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'message', data: { content: '' } },
    { event: 'message', data: { content: 'Real content' } },
    { event: 'message', data: { content: '' } },
    { event: 'done', data: { status: 'OK' } },
]

/** Multiple usage updates (cumulative) */
export const MULTI_USAGE_UPDATES: SSEEvent[] = [
    { event: 'connected', data: {} },
    { event: 'tool_call', data: { id: 'tc-1', name: 'Bash', arguments: { command: 'echo 1' } } },
    { event: 'tool_result', data: { id: 'tc-1', name: 'Bash', output: '1', status: 'OK' } },
    {
        event: 'usage_update', data: {
            step_usage: { input: 100, output: 50, cache_read: 0, cache_write: 0, total: 150 },
            cumulative_usage: { input: 100, output: 50, cache_read: 0, cache_write: 0, total: 150 },
        }
    },
    { event: 'message', data: { content: 'Step 1 done.' } },
    {
        event: 'usage_update', data: {
            step_usage: { input: 120, output: 60, cache_read: 0, cache_write: 0, total: 180 },
            cumulative_usage: { input: 220, output: 110, cache_read: 0, cache_write: 0, total: 330 },
        }
    },
    { event: 'done', data: { status: 'OK' } },
]
