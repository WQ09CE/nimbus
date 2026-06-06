import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useChatStore } from '../chat-store'
import { server } from '../../mocks/server'
import { http, HttpResponse } from 'msw'
import {
  createSSEHandler,
  createOpenStreamHandler,
  SIMPLE_TEXT,
  MULTI_CHUNK_TEXT,
  TOOL_CALL_WITH_RESULT,
  TOOL_STREAMING_OUTPUT,
  PARALLEL_TOOL_CALLS,
  TOOL_RESULT_BEFORE_CALL,
  WITH_USAGE_UPDATE,
  STREAM_ERROR,
  TEXT_TOOL_TEXT,
  MULTI_STEP_INTERLEAVE,
  WITH_HEARTBEATS,
  REMOTE_USER_MESSAGE,
  TOOL_ERROR,
  EMPTY_CHUNKS,
  MULTI_USAGE_UPDATES,
} from '../../mocks/scenarios'

// ============================================================
// Helpers
// ============================================================

/** Install mock handlers for session creation and status check */
function installSessionMocks() {
  server.use(
    http.post('/api/v1/sessions', () =>
      HttpResponse.json({
        id: 'test-session-id',
        status: 'active',
        created_at: new Date().toISOString(),
      })
    ),
    http.get('/api/v1/sessions/:id/messages', () =>
      HttpResponse.json({ items: [] })
    ),
    http.get('/api/v1/sessions/:id/status', () =>
      HttpResponse.json({ running: false })
    ),
    http.get('/api/v1/sessions/:id', () =>
      HttpResponse.json({
        id: 'test-session-id',
        status: 'active',
        created_at: new Date().toISOString(),
      })
    )
  )
}

/** Create a session in the store and return the hook result */
async function setupSession() {
  const { result } = renderHook(() => useChatStore())
  await act(async () => { await result.current.createNewSession() })
  return result
}

// ============================================================
// Tests
// ============================================================

describe('chat-store SSE integration', () => {
  beforeEach(() => {
    const { result } = renderHook(() => useChatStore())
    act(() => { result.current.reset() })
    installSessionMocks()
  })

  // ----------------------------------------------------------
  // Basic text streaming
  // ----------------------------------------------------------

  it('handles simple text response', async () => {
    server.use(createSSEHandler(SIMPLE_TEXT))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Hi') })

    expect(result.current.isStreaming).toBe(false)
    expect(result.current.messages).toHaveLength(2) // user + assistant
    const assistant = result.current.messages[1]
    expect(assistant.role).toBe('assistant')
    expect(assistant.content).toContain('Hello, world!')
  })

  it('concatenates multi-chunk text correctly', async () => {
    server.use(createSSEHandler(MULTI_CHUNK_TEXT))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Hi') })

    const assistant = result.current.messages[1]
    expect(assistant.content).toBe('Hello, world!')
    // Should be one text part (merged) or multiple — verify text is complete
    const textParts = assistant.parts.filter(p => p.type === 'text')
    const fullText = textParts.map(p => p.content).join('')
    expect(fullText).toBe('Hello, world!')
  })

  it('filters empty content chunks', async () => {
    server.use(createSSEHandler(EMPTY_CHUNKS))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Hi') })

    const assistant = result.current.messages[1]
    expect(assistant.content).toBe('Real content')
  })

  // ----------------------------------------------------------
  // Tool calls
  // ----------------------------------------------------------

  it('handles tool call with result', async () => {
    server.use(createSSEHandler(TOOL_CALL_WITH_RESULT))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Run it') })

    const assistant = result.current.messages[1]
    // Tool call should be tracked
    const toolCallsMap = assistant.toolCallsMap || {}
    expect(Object.keys(toolCallsMap)).toHaveLength(1)
    expect(toolCallsMap['tc-1'].name).toBe('Bash')

    // Tool result should be tracked
    const toolResults = assistant.toolResults || []
    expect(toolResults).toHaveLength(1)
    expect(toolResults[0].result).toBe('hello\n')

    // Final text
    expect(assistant.content).toContain('Command executed successfully')
  })

  it('accumulates streaming tool output chunks', async () => {
    server.use(createSSEHandler(TOOL_STREAMING_OUTPUT))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('List files') })

    const assistant = result.current.messages[1]
    const resultsMap = assistant.toolResultsMap || {}

    // The final tool_result should be present
    expect(resultsMap['tc-1']).toBeDefined()
    // The streamed output should have been accumulated
    expect(resultsMap['tc-1'].result).toContain('file1.txt')
    expect(resultsMap['tc-1'].result).toContain('file3.txt')
  })

  it('handles parallel tool calls independently', async () => {
    server.use(createSSEHandler(PARALLEL_TOOL_CALLS))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Read both') })

    const assistant = result.current.messages[1]
    const toolCallsMap = assistant.toolCallsMap || {}
    expect(Object.keys(toolCallsMap)).toHaveLength(2)
    expect(toolCallsMap['tc-1'].name).toBe('Read')
    expect(toolCallsMap['tc-2'].name).toBe('Read')

    const toolResults = assistant.toolResults || []
    expect(toolResults).toHaveLength(2)
  })

  it('handles tool result arriving before tool call (race condition)', async () => {
    server.use(createSSEHandler(TOOL_RESULT_BEFORE_CALL))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Write') })

    const assistant = result.current.messages[1]
    // Should have a tool part even though result came first
    const toolParts = assistant.parts.filter(p => p.type === 'tool')
    expect(toolParts.length).toBeGreaterThanOrEqual(1)

    // Tool result should be in the map
    const resultsMap = assistant.toolResultsMap || {}
    expect(resultsMap['tc-1']).toBeDefined()
    expect(resultsMap['tc-1'].result).toBe('ok')
  })

  it('handles tool error result', async () => {
    server.use(createSSEHandler(TOOL_ERROR))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Read file') })

    const assistant = result.current.messages[1]
    const toolResults = assistant.toolResults || []
    expect(toolResults).toHaveLength(1)
    expect(toolResults[0].error).toBe('File not found')
  })

  it('renders interleaved text-tool-text correctly', async () => {
    server.use(createSSEHandler(TEXT_TOOL_TEXT))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Check file') })

    const assistant = result.current.messages[1]
    // Should have parts in order: text → tool → text
    expect(assistant.parts.length).toBeGreaterThanOrEqual(2)
    expect(assistant.parts[0].type).toBe('text')
    // A tool part should exist somewhere
    const toolIdx = assistant.parts.findIndex(p => p.type === 'tool')
    expect(toolIdx).toBeGreaterThan(0)
  })

  it('preserves order across multiple steps with distinct tool ids', async () => {
    server.use(createSSEHandler(MULTI_STEP_INTERLEAVE))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Run steps') })

    const assistant = result.current.messages[1]
    // Expected interleave: text → tool → text → tool → text (5 parts, 2 distinct tools)
    const kinds = assistant.parts.map(p => p.type)
    expect(kinds).toEqual(['text', 'tool', 'text', 'tool', 'text'])

    const toolParts = assistant.parts.filter(p => p.type === 'tool') as any[]
    const ids = toolParts.map(p => p.toolCall.id)
    expect(new Set(ids).size).toBe(2) // both tool cards preserved, not merged

    // Each tool keeps its own result (not overwritten)
    const rm = assistant.toolResultsMap || {}
    expect(rm['json_extract_txt_0_aaaa']?.result).toBe('AAA')
    expect(rm['json_extract_txt_0_bbbb']?.result).toBe('BBB')

    // Messages are NOT clumped — three separate text parts
    const texts = (assistant.parts.filter(p => p.type === 'text') as any[]).map(p => p.content)
    expect(texts).toEqual(['Step one.', 'Step two.', 'Done.'])
  })

  // ----------------------------------------------------------
  // Usage tracking
  // ----------------------------------------------------------

  it('sets tokenUsage from usage_update event', async () => {
    server.use(createSSEHandler(WITH_USAGE_UPDATE))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Hi') })

    expect(result.current.tokenUsage).not.toBeNull()
    expect(result.current.tokenUsage?.input).toBe(100)
    expect(result.current.tokenUsage?.output).toBe(50)
    expect(result.current.tokenUsage?.total).toBe(170)
  })

  it('updates tokenUsage to latest cumulative value on multiple updates', async () => {
    server.use(createSSEHandler(MULTI_USAGE_UPDATES))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Multi step') })

    // Should reflect the LAST cumulative usage
    expect(result.current.tokenUsage).not.toBeNull()
    expect(result.current.tokenUsage?.input).toBe(220)
    expect(result.current.tokenUsage?.output).toBe(110)
    expect(result.current.tokenUsage?.total).toBe(330)
  })

  // ----------------------------------------------------------
  // Error handling
  // ----------------------------------------------------------

  it('handles stream error event', async () => {
    server.use(createSSEHandler(STREAM_ERROR))
    const result = await setupSession()

    await act(async () => {
      try {
        await result.current.sendMessage('Crash')
      } catch {
        // Error is expected
      }
    })

    // After error, streaming should stop
    expect(result.current.isStreaming).toBe(false)
  })

  // ----------------------------------------------------------
  // Done event and finalization
  // ----------------------------------------------------------

  it('finalizes message on done event (id changes from streaming-assistant)', async () => {
    server.use(createSSEHandler(SIMPLE_TEXT))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Hi') })

    // After done, the assistant message should have a real id (not "streaming-assistant")
    const assistant = result.current.messages[1]
    expect(assistant.id).not.toBe('streaming-assistant')
    expect(assistant.id).toMatch(/^assistant-/)
  })

  // ----------------------------------------------------------
  // Heartbeats
  // ----------------------------------------------------------

  it('ignores heartbeat events without affecting message content', async () => {
    server.use(createSSEHandler(WITH_HEARTBEATS))
    const result = await setupSession()

    await act(async () => { await result.current.sendMessage('Think') })

    const assistant = result.current.messages[1]
    expect(assistant.content).toBe('Done thinking.')
    // Heartbeats should not create extra messages
    expect(result.current.messages).toHaveLength(2)
  })

  // ----------------------------------------------------------
  // Injection during streaming
  // ----------------------------------------------------------

  it('interrupts and re-sends when message sent during active stream', async () => {
    server.use(
      createOpenStreamHandler(),
      http.post('/api/v1/sessions/:id/interrupt', async () => {
        return HttpResponse.json({ success: true })
      })
    )
    const result = await setupSession()

    // Start streaming (open stream, won't complete)
    act(() => { result.current.sendMessage('Long task') })
    expect(result.current.isStreaming).toBe(true)

    // Send a new message while streaming — should interrupt first
    await act(async () => { await result.current.sendMessage('Stop!') })

    // After interrupt, isStreaming should be false (interrupt clears it)
    expect(result.current.isStreaming).toBe(false)
  })

  // ----------------------------------------------------------
  // Session switching
  // ----------------------------------------------------------

  it('cleans up streaming state when session changes', async () => {
    server.use(createOpenStreamHandler())
    const result = await setupSession()

    // Start streaming
    act(() => { result.current.sendMessage('Long task') })
    expect(result.current.isStreaming).toBe(true)

    // Reset simulates session switch cleanup
    act(() => { result.current.reset() })

    expect(result.current.isStreaming).toBe(false)
    expect(result.current.messages).toHaveLength(0)
    expect(result.current.tokenUsage).toBeNull()
  })
})
