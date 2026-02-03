import { http, HttpResponse } from 'msw'

const API_BASE = 'http://localhost:4096'

export const handlers = [
  // Mock Create Session
  http.post(`${API_BASE}/api/v1/sessions`, () => {
    return HttpResponse.json({
      id: 'test-session-id',
      status: 'active',
      created_at: new Date().toISOString(),
      memory_type: 'tiered',
      planner_type: 'dag',
    })
  }),

  // Mock Get Session Messages
  http.get(`${API_BASE}/api/v1/sessions/:id/messages`, () => {
    return HttpResponse.json({
      items: [
        {
          id: 'msg-1',
          role: 'user',
          content: 'Hello history',
          created_at: new Date().toISOString(),
        },
        {
          id: 'msg-2',
          role: 'assistant',
          content: 'Hi there',
          created_at: new Date().toISOString(),
        },
      ]
    })
  }),

  // Mock Inject
  http.post(`${API_BASE}/api/v1/sessions/:id/inject`, () => {
    return HttpResponse.json({
      status: 'injected',
      message: 'Message injected'
    })
  }),

  // Mock Chat Stream (SSE)
  http.post(`${API_BASE}/api/v1/sessions/:id/chat`, ({ request }) => {
    const stream = new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder()
        
        const send = (event: string, data: any) => {
          const payload = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
          controller.enqueue(encoder.encode(payload))
        }

        // Simulate a typical thinking flow
        send('connected', {})
        send('message_start', { role: 'assistant' })
        
        // Step 1: Thinking
        send('heartbeat', { kind: 'THOUGHT', content: 'Thinking...' })
        
        // Step 2: Tool Call
        send('tool_call', { 
          id: 'call-1', 
          name: 'Bash', 
          arguments: { command: 'echo hello' } 
        })
        
        // Step 3: Tool Result
        setTimeout(() => {
            send('tool_result', {
              id: 'call-1',
              name: 'Bash',
              output: 'hello\n',
              status: 'OK'
            })
            
            // Step 4: Final Message
            send('message', { content: 'I executed the command.' })
            
            // Done
            send('dag_complete', { status: 'OK' })
            controller.close()
        }, 10) // Small delay to simulate async
      }
    })

    return new HttpResponse(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
      },
    })
  }),
]
