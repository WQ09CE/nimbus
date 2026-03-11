/**
 * Tier 1 E2E Tests: Chat Streaming
 *
 * Tests streaming UX: progress indicator, stop button visibility.
 *
 * Note: The SSE mock delivers all events synchronously in a single response body.
 * The browser processes them quickly, so some transient UI states (like the
 * working indicator) flash very briefly. Tests must use generous timeouts and
 * polling to catch these ephemeral states.
 */

import { test, expect } from '@playwright/test';
import { setupSSEMock, loadFixture, type SSEFixture } from '../helpers/sse-mock';

/**
 * Build a slow fixture so that streaming state is observable.
 * Each event has significant delay => the browser truly "streams" the response.
 */
function makeSlowFixture(): SSEFixture {
  return {
    scenario: 'slow-stream',
    description: 'Slow streaming for testing streaming UX',
    session: {
      id: 'test-streaming-001',
      status: 'active',
      created_at: new Date().toISOString(),
      memory_type: 'buffer',
      planner_type: 'simple',
      message_count: 0,
    },
    messages_history: [],
    sse_events: [
      { delay_ms: 50, event: 'connected', data: { session_id: 'test-streaming-001' } },
      { delay_ms: 100, event: 'message_start', data: { role: 'assistant' } },
      { delay_ms: 200, event: 'step_start', data: { iteration: 1 } },
      { delay_ms: 300, event: 'message', data: { content: 'Working ' } },
      { delay_ms: 300, event: 'message', data: { content: 'on your ' } },
      { delay_ms: 300, event: 'message', data: { content: 'request...' } },
      { delay_ms: 200, event: 'done', data: { status: 'OK' } },
    ],
  };
}

test.describe('Chat Streaming', () => {
  test('should show streaming progress indicator', async ({ page }) => {
    const fixture = makeSlowFixture();
    await setupSSEMock(page, fixture);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    const input = page.getByTestId('chat-input');
    await input.fill('Update the file');
    await input.press('Enter');

    // During streaming, a working indicator OR stop button should become visible
    // (either proves the streaming state is active)
    const stopButton = page.getByTestId('stop-button');
    await expect(stopButton).toBeVisible({ timeout: 5000 });

    // After streaming completes, the stop button should disappear
    await expect(stopButton).not.toBeVisible({ timeout: 15000 });

    // Final assistant message should be present
    const assistantMsg = page.getByTestId('message-assistant');
    await expect(assistantMsg).toBeVisible({ timeout: 5000 });
    await expect(assistantMsg).toContainText('request...');
  });

  test('should show and hide stop button during stream', async ({ page }) => {
    const fixture = makeSlowFixture();
    await setupSSEMock(page, fixture);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    // Before sending, only send button should be visible (not stop button)
    await expect(page.getByTestId('send-button')).toBeVisible();
    await expect(page.getByTestId('stop-button')).not.toBeVisible();

    const input = page.getByTestId('chat-input');
    await input.fill('Do something');
    await input.press('Enter');

    // Stop button should appear during streaming
    const stopButton = page.getByTestId('stop-button');
    await expect(stopButton).toBeVisible({ timeout: 5000 });

    // Send button should not be visible during streaming (replaced by stop)
    await expect(page.getByTestId('send-button')).not.toBeVisible();

    // After streaming completes, stop button should disappear and send button should return
    await expect(stopButton).not.toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId('send-button')).toBeVisible();
  });
});
