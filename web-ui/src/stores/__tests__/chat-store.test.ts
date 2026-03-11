import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useChatStore } from '../chat-store'
import { server } from '../../mocks/server'
import { http, HttpResponse } from 'msw'

const API_BASE = 'http://localhost:4096'

describe('useChatStore', () => {
  beforeEach(() => {
    const { result } = renderHook(() => useChatStore())
    act(() => {
      result.current.reset()
    })
  })

  it('creates a new session', async () => {
    const { result } = renderHook(() => useChatStore())

    await act(async () => {
      await result.current.createNewSession()
    })

    expect(result.current.session).not.toBeNull()
    expect(result.current.session?.id).toBe('test-session-id')
    expect(result.current.messages).toEqual([])
  })

  it('handles chat streaming flow', async () => {
    const { result } = renderHook(() => useChatStore())

    // Setup session
    await act(async () => {
      await result.current.createNewSession()
    })

    // Send message
    await act(async () => {
      await result.current.sendMessage('Run bash')
    })

    // Stream should be completed after await
    expect(result.current.isStreaming).toBe(false)
    expect(result.current.messages).toHaveLength(2) // User + Assistant
    expect(result.current.messages[0].content).toBe('Run bash')

    // Check final state
    const assistantMsg = result.current.messages[1]
    expect(assistantMsg.role).toBe('assistant')
    expect(assistantMsg.content).toContain('I executed the command')

    // Check tool usage in final message
    expect(Object.keys(assistantMsg.toolCallsMap || {})).toHaveLength(1)
    expect(Object.values(assistantMsg.toolCallsMap || {})[0].name).toBe('Bash')
  })

  it('injects message during streaming (Intervention)', async () => {
    const { result } = renderHook(() => useChatStore())

    // Setup a long-running stream mock
    server.use(
      http.post(`${API_BASE}/api/v1/sessions/:id/chat`, ({ request }) => {
        const stream = new ReadableStream({
          start(controller) {
            const encoder = new TextEncoder()
            const send = (data: any) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`))

            send({ type: 'connected' })
            // Keep stream open
          }
        })
        return new HttpResponse(stream, { headers: { 'Content-Type': 'text/event-stream' } })
      })
    )

    // Setup injection spy
    const injectSpy = vi.fn()
    server.use(
      http.post('/api/v1/sessions/:id/inject', async ({ request }) => {
        const body = await request.json()
        injectSpy(body)
        return HttpResponse.json({ status: 'injected' })
      })
    )

    await act(async () => {
      await result.current.createNewSession()
    })

    // 1. Start streaming
    act(() => {
      result.current.sendMessage('Long task')
    })

    expect(result.current.isStreaming).toBe(true)

    // 2. Inject message while streaming
    await act(async () => {
      await result.current.sendMessage('Stop!')
    })

    // 3. Verify injection behavior
    // Should NOT add a new "step" message yet (optimistic update logic might vary)
    // In current impl, we optimistically add user message
    expect(result.current.messages).toHaveLength(3) // User 1 + User 2 (Inject) + Streaming Assistant
    expect(result.current.messages[1].content).toContain('Stop!')


    // Streaming should STILL be active (don't break the flow)
    expect(result.current.isStreaming).toBe(true)

    // Verify API call
    expect(injectSpy).toHaveBeenCalledWith({ content: 'Stop!' })
  })
})
