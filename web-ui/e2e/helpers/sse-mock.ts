/**
 * SSE Mock Interceptor for Playwright E2E Tests
 *
 * Uses page.route() to intercept Nimbus API requests and return
 * pre-built SSE fixture data, enabling Tier 1 frontend tests
 * without a running backend.
 *
 * Usage:
 *   import { setupSSEMock, loadFixture } from './helpers/sse-mock';
 *
 *   test('chat flow', async ({ page }) => {
 *     const fixture = loadFixture('simple-chat');
 *     await setupSSEMock(page, fixture);
 *     await page.goto('/');
 *     // ... interact with UI
 *   });
 */

import { type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SSEEvent {
  /** Delay in milliseconds before emitting this event */
  delay_ms: number;
  /** SSE event type (e.g. "connected", "message", "tool_call") */
  event: string;
  /** Payload -- will be JSON-serialised into the `data:` field */
  data: unknown;
}

export interface SSEFixture {
  /** Human-readable scenario name */
  scenario: string;
  /** Description of what this fixture tests */
  description: string;
  /** Session object returned by POST /sessions and GET /sessions/:id */
  session: {
    id: string;
    status: string;
    created_at: string;
    memory_type: string;
    planner_type: string;
    message_count: number;
  };
  /** Messages returned by GET /sessions/:id/messages */
  messages_history: Array<{
    role: string;
    content: string;
    timestamp: string;
  }>;
  /** Ordered SSE events for the chat stream */
  sse_events: SSEEvent[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Encode a single SSE frame.
 *
 * Format:
 *   event: {type}\n
 *   data: {json}\n
 *   \n
 */
function encodeSSEFrame(event: string, data: unknown): string {
  const json = JSON.stringify(data);
  return `event: ${event}\ndata: ${json}\n\n`;
}

// ---------------------------------------------------------------------------
// Fixture loader
// ---------------------------------------------------------------------------

/**
 * Load a fixture file from `e2e/fixtures/{name}.sse.json`.
 *
 * The file must conform to the SSEFixture interface.
 */
export function loadFixture(name: string): SSEFixture {
  const fixturesDir = path.resolve(__dirname, '..', 'fixtures');
  const filePath = path.join(fixturesDir, `${name}.sse.json`);

  if (!fs.existsSync(filePath)) {
    throw new Error(
      `SSE fixture not found: ${filePath}\n` +
      `Create it at e2e/fixtures/${name}.sse.json`
    );
  }

  const raw = fs.readFileSync(filePath, 'utf-8');
  return JSON.parse(raw) as SSEFixture;
}

// ---------------------------------------------------------------------------
// Mock models / config / health responses
// ---------------------------------------------------------------------------

const MOCK_MODELS_RESPONSE = {
  models: [
    { id: 'claude-sonnet-4-20250514', object: 'model', created: 1700000000, owned_by: 'anthropic' },
    { id: 'claude-opus-4-20250514', object: 'model', created: 1700000000, owned_by: 'anthropic' },
    { id: 'gpt-4o', object: 'model', created: 1700000000, owned_by: 'openai' },
  ],
};

const MOCK_HEALTH_RESPONSE = { status: 'ok' };

const MOCK_CONFIG_RESPONSE = {
  version: '0.2.0',
  default_memory_type: 'buffer',
  default_planner_type: 'simple',
  max_iterations: 50,
};

// ---------------------------------------------------------------------------
// Route handlers
// ---------------------------------------------------------------------------

/**
 * Set up all API route mocks for a single SSE fixture.
 *
 * Intercepts:
 *   POST   /api/v1/sessions             -> fixture.session
 *   POST   /api/v1/sessions/:id/chat    -> SSE stream from fixture.sse_events
 *   GET    /api/v1/sessions/:id/messages -> fixture.messages_history
 *   GET    /api/v1/sessions/:id         -> fixture.session
 *   POST   /api/v1/sessions/:id/inject  -> 200
 *   POST   /api/v1/sessions/:id/interrupt -> 200
 *   GET    /api/v1/health               -> { status: "ok" }
 *   GET    /api/v1/models               -> mock models list
 *   GET    /api/v1/config               -> mock config
 */
export async function setupSSEMock(page: Page, fixture: SSEFixture): Promise<void> {
  // --- POST /api/v1/sessions (create session) ---
  await page.route('**/api/v1/sessions', async (route) => {
    const method = route.request().method();
    if (method === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(fixture.session),
      });
    } else if (method === 'GET') {
      // GET /api/v1/sessions (list sessions)
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [fixture.session],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      });
    } else {
      await route.continue();
    }
  });

  // --- GET /api/v1/sessions/:id/events (Background SSE stream) ---
  await page.route('**/api/v1/sessions/*/events', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue();
      return;
    }
    // Return a hanging response to satisfy useSSEListener without it entering a failure loop
    await route.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
      },
      // Empty body that never closes, effectively hanging
      body: Buffer.from(': connected\\n\\n', 'utf-8'),
    });
  });

  // --- POST /api/v1/sessions/:id/chat (SSE stream) ---
  await page.route('**/api/v1/sessions/*/chat', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue();
      return;
    }

    // Stream SSE events with real delays
    const chunks: Buffer[] = [];
    for (const evt of fixture.sse_events) {
      if (evt.delay_ms > 0) {
        await sleep(evt.delay_ms);
      }
      const frame = encodeSSEFrame(evt.event, evt.data);
      chunks.push(Buffer.from(frame, 'utf-8'));
    }

    const body = Buffer.concat(chunks);
    await route.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no',
      },
      body,
    });
  });

  // --- GET /api/v1/sessions/:id/messages ---
  await page.route('**/api/v1/sessions/*/messages*', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue();
      return;
    }

    // Convert fixture messages to ServerMessage format
    const items = fixture.messages_history.map((msg, idx) => ({
      id: `msg-${idx}`,
      role: msg.role,
      content: msg.content,
      created_at: msg.timestamp,
      artifacts: [],
    }));

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items }),
    });
  });

  // --- POST /api/v1/sessions/:id/inject ---
  await page.route('**/api/v1/sessions/*/inject', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true }),
    });
  });

  // --- POST /api/v1/sessions/:id/interrupt ---
  await page.route('**/api/v1/sessions/*/interrupt', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        session_id: fixture.session.id,
        interrupted_processes: 1,
      }),
    });
  });

  // --- GET /api/v1/sessions/:id/files ---
  await page.route('**/api/v1/sessions/*/files*', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fulfill({ status: 405, body: 'Method Not Allowed' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  // --- GET /api/v1/sessions/:id (get single session) ---
  // This must come AFTER the more specific /messages, /inject, /interrupt, /files routes
  // because Playwright matches routes in registration order (first match wins).
  await page.route('**/api/v1/sessions/*', async (route) => {
    const url = route.request().url();
    const method = route.request().method();

    // Skip if this is a sub-resource (already handled above)
    if (
      url.includes('/chat') ||
      url.includes('/messages') ||
      url.includes('/inject') ||
      url.includes('/interrupt') ||
      url.includes('/resume') ||
      url.includes('/files')
    ) {
      await route.continue();
      return;
    }

    if (method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(fixture.session),
      });
    } else if (method === 'DELETE') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true }),
      });
    } else {
      await route.continue();
    }
  });

  // --- GET /api/v1/health ---
  await page.route('**/api/v1/health', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_HEALTH_RESPONSE),
    });
  });

  // --- GET /api/v1/models ---
  await page.route('**/api/v1/models', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_MODELS_RESPONSE),
    });
  });

  // --- GET /api/v1/config ---
  await page.route('**/api/v1/config', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_CONFIG_RESPONSE),
    });
  });

  // --- POST /api/v1/logs (sink for frontend logger) ---
  await page.route('**/api/v1/logs', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true }),
    });
  });
}

// ---------------------------------------------------------------------------
// Multi-turn mock
// ---------------------------------------------------------------------------

/**
 * Set up route mocks that support multiple conversation turns.
 *
 * Each call to POST /sessions/:id/chat consumes the next fixture
 * in the array. The session object from the first fixture is used
 * for session-level routes (create, get, etc.).
 *
 * Once all fixtures are consumed, subsequent chat requests receive
 * an empty SSE stream with a single `done` event.
 */
export async function setupMultiTurnMock(
  page: Page,
  fixtures: SSEFixture[],
): Promise<void> {
  if (fixtures.length === 0) {
    throw new Error('setupMultiTurnMock requires at least one fixture');
  }

  // Use the first fixture as the "session" fixture for non-chat routes
  const sessionFixture = fixtures[0];

  // Track which turn we are on
  let turnIndex = 0;

  // Accumulate messages_history across turns
  let cumulativeHistory = [...sessionFixture.messages_history];

  // --- POST /api/v1/sessions (create session) ---
  await page.route('**/api/v1/sessions', async (route) => {
    const method = route.request().method();
    if (method === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(sessionFixture.session),
      });
    } else if (method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [sessionFixture.session],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      });
    } else {
      await route.continue();
    }
  });

  // --- POST /api/v1/sessions/:id/chat (SSE stream, round-robin) ---
  await page.route('**/api/v1/sessions/*/chat', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue();
      return;
    }

    // Pick the fixture for the current turn
    const currentTurn = turnIndex;
    turnIndex++;

    let events: SSEEvent[];
    if (currentTurn < fixtures.length) {
      events = fixtures[currentTurn].sse_events;

      // Accumulate history from this turn (if not the first)
      if (currentTurn > 0) {
        cumulativeHistory = [
          ...cumulativeHistory,
          ...fixtures[currentTurn].messages_history,
        ];
      }
    } else {
      // Exhausted all fixtures -- return a minimal completion
      events = [
        {
          delay_ms: 0,
          event: 'connected',
          data: { session_id: sessionFixture.session.id },
        },
        {
          delay_ms: 10,
          event: 'message',
          data: { content: '(no more fixture data)' },
        },
        {
          delay_ms: 10,
          event: 'done',
          data: { status: 'completed' },
        },
      ];
    }

    // Stream with delays
    const chunks: Buffer[] = [];
    for (const evt of events) {
      if (evt.delay_ms > 0) {
        await sleep(evt.delay_ms);
      }
      const frame = encodeSSEFrame(evt.event, evt.data);
      chunks.push(Buffer.from(frame, 'utf-8'));
    }

    const body = Buffer.concat(chunks);
    await route.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no',
      },
      body,
    });
  });

  // --- GET /api/v1/sessions/:id/messages ---
  await page.route('**/api/v1/sessions/*/messages*', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue();
      return;
    }

    const items = cumulativeHistory.map((msg, idx) => ({
      id: `msg-${idx}`,
      role: msg.role,
      content: msg.content,
      created_at: msg.timestamp,
      artifacts: [],
    }));

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items }),
    });
  });

  // --- POST /api/v1/sessions/:id/inject ---
  await page.route('**/api/v1/sessions/*/inject', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true }),
    });
  });

  // --- POST /api/v1/sessions/:id/interrupt ---
  await page.route('**/api/v1/sessions/*/interrupt', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        session_id: sessionFixture.session.id,
        interrupted_processes: 1,
      }),
    });
  });

  // --- GET /api/v1/sessions/:id/files ---
  await page.route('**/api/v1/sessions/*/files*', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fulfill({ status: 405, body: 'Method Not Allowed' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  // --- GET /api/v1/sessions/:id ---
  await page.route('**/api/v1/sessions/*', async (route) => {
    const url = route.request().url();
    const method = route.request().method();

    if (
      url.includes('/chat') ||
      url.includes('/messages') ||
      url.includes('/inject') ||
      url.includes('/interrupt') ||
      url.includes('/resume') ||
      url.includes('/files')
    ) {
      await route.continue();
      return;
    }

    if (method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(sessionFixture.session),
      });
    } else if (method === 'DELETE') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true }),
      });
    } else {
      await route.continue();
    }
  });

  // --- Static endpoints ---
  await page.route('**/api/v1/health', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_HEALTH_RESPONSE),
    });
  });

  await page.route('**/api/v1/models', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_MODELS_RESPONSE),
    });
  });

  await page.route('**/api/v1/config', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_CONFIG_RESPONSE),
    });
  });

  // --- POST /api/v1/logs (sink for frontend logger) ---
  await page.route('**/api/v1/logs', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true }),
    });
  });
}
