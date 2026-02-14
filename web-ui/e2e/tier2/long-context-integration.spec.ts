/**
 * Tier 2: Long Context Integration Tests
 *
 * Full-stack stress tests exercising UI -> Next.js -> Nimbus Server -> AgentOS
 * -> vCPU -> MockLLM -> Tools pipeline under multi-turn and multi-step workloads.
 *
 * Prerequisites (same as integration-chat.spec.ts):
 *   1. Start the Nimbus backend:
 *        NIMBUS_LLM=mock nimbus serve --port 4096
 *
 *   2. Start the Next.js dev server:
 *        cd web-ui && npm run dev
 *
 *   3. Run:
 *        npx playwright test --project=tier2
 *
 * MockLLM Rules:
 *   - /^hello|hi|hey/i       -> text: "Hello! I'm Nimbus mock agent..."
 *   - /echo\s+(.+)/i         -> Bash tool_call: echo <payload>
 *   - /read\s+(.+)/i         -> Read tool_call: read <file_path>
 *   - /count\s+to\s+(\d+)/i  -> multi-step Write tool_calls
 *   - /error/i               -> text: "An error occurred..."
 *   - default                -> text: "I understand. Let me help you with that."
 *
 * Timeout Strategy:
 *   - Per-test: 120s (multi-turn tests need extra time)
 *   - Streaming start: 20s (backend may need warmup)
 *   - Streaming end: 45s (multi-step vCPU loop + tool execution)
 */

import { test, expect } from '@playwright/test';
import { NimbusPage } from '../helpers/nimbus-page';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Timeout for waiting for backend streaming to begin. */
const STREAMING_START_TIMEOUT = 20_000;

/** Timeout for waiting for backend streaming to complete. */
const STREAMING_END_TIMEOUT = 45_000;

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

test.describe('Tier 2: Long Context Integration', () => {
  let nimbus: NimbusPage;

  test.beforeEach(async ({ page }) => {
    // Forward browser console to test output for debugging
    page.on('console', (msg) => console.log(`[Browser] ${msg.text()}`));

    // Clear localStorage to ensure each test starts with a fresh session
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());

    nimbus = new NimbusPage(page);
    await nimbus.goto();

    // Wait for the welcome screen to appear, which confirms that
    // createNewSession() has completed (isLoading becomes false).
    await nimbus.expectWelcomeScreen();
  });

  // =========================================================================
  // Test 1: 5-turn conversation through real backend
  // =========================================================================

  test('should handle 5-turn conversation through real backend', async ({ page }) => {
    test.setTimeout(120_000);

    // Capture JS errors during the test
    const jsErrors: string[] = [];
    page.on('pageerror', (error) => {
      jsErrors.push(error.message);
    });

    // Turn 1: greeting (MockLLM rule 1)
    await nimbus.sendMessage('hello');
    await expect(page.getByTestId('message-user').nth(0)).toBeVisible({ timeout: 5000 });
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    const reply1 = await nimbus.getLastAssistantMessage();
    expect(reply1).toContain('Nimbus mock agent');

    // Turn 2: echo command (MockLLM rule 2 -> Bash tool_call)
    await nimbus.sendMessage('echo test1');
    await expect(page.getByTestId('message-user').nth(1)).toBeVisible({ timeout: 5000 });
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify tool card appeared for the echo command
    const toolCards2 = await nimbus.getToolCards();
    const hasBash = toolCards2.some(
      (card) => card.name.toLowerCase().includes('bash'),
    );
    expect(hasBash).toBe(true);

    // Turn 3: echo another value
    await nimbus.sendMessage('echo test2');
    await expect(page.getByTestId('message-user').nth(2)).toBeVisible({ timeout: 5000 });
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Turn 4: echo a third value
    await nimbus.sendMessage('echo test3');
    await expect(page.getByTestId('message-user').nth(3)).toBeVisible({ timeout: 5000 });
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Turn 5: another greeting to verify session context works
    await nimbus.sendMessage('hello again');
    await expect(page.getByTestId('message-user').nth(4)).toBeVisible({ timeout: 5000 });
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify all 5 turns produced messages
    const allMessages = await nimbus.getAllMessages();
    const userMessages = allMessages.filter((m) => m.role === 'user');
    const assistantMessages = allMessages.filter((m) => m.role === 'assistant');

    expect(userMessages.length).toBeGreaterThanOrEqual(5);
    expect(assistantMessages.length).toBeGreaterThanOrEqual(5);

    // Verify user messages are in the correct order
    expect(userMessages[0].text).toContain('hello');
    expect(userMessages[1].text).toContain('echo test1');
    expect(userMessages[2].text).toContain('echo test2');
    expect(userMessages[3].text).toContain('echo test3');
    expect(userMessages[4].text).toContain('hello again');

    // Verify input is still functional
    await expect(nimbus.chatInput).toBeVisible();
    await expect(nimbus.chatInput).toBeEnabled();

    // Check no critical JS errors
    const criticalErrors = jsErrors.filter(
      (err) =>
        !err.includes('ResizeObserver') &&
        !err.includes('Non-Error promise rejection'),
    );
    expect(criticalErrors).toHaveLength(0);
  });

  // =========================================================================
  // Test 2: count-to-5 multi-step task
  // =========================================================================

  test('should handle count-to-5 multi-step task', async ({ page }) => {
    test.setTimeout(120_000);

    // "count to 5" triggers MockLLM rule 4:
    // The vCPU loops multiple iterations, each time calling Write("count.txt", "Count is N")
    // until the count reaches 5, then returns a completion message.
    await nimbus.sendMessage('count to 5');

    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);

    // Wait for tool cards to appear (Write tool calls for counting)
    const toolCard = page.getByTestId('tool-card');
    await expect(toolCard.first()).toBeVisible({ timeout: STREAMING_END_TIMEOUT });

    // Wait for streaming to fully complete
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify multiple tool cards appeared (one per Write call)
    const toolCards = await nimbus.getToolCards();
    // count-to-5 should produce 5 Write tool calls
    expect(toolCards.length).toBeGreaterThanOrEqual(3);

    // Verify the final completion message
    const reply = await nimbus.getLastAssistantMessage();
    expect(reply.toLowerCase()).toContain('count');

    // Verify input is still usable after the multi-step task
    await expect(nimbus.chatInput).toBeVisible();
    await expect(nimbus.chatInput).toBeEnabled();
  });

  // =========================================================================
  // Test 3: Session state persistence across interactions and reload
  // =========================================================================

  test('should maintain session state across many interactions', async ({ page }) => {
    test.setTimeout(120_000);

    // --- Phase 1: Send 3 messages ---
    await nimbus.sendMessage('hello');
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    await nimbus.sendMessage('echo persistence test');
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    await nimbus.sendMessage('hello once more');
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify 3 user messages exist before reload
    const preReloadMessages = await nimbus.getUserMessages();
    expect(preReloadMessages.length).toBe(3);

    // Save session ID
    const sessionId = await nimbus.getSessionId();
    expect(sessionId).not.toBeNull();
    console.log('Session ID before reload:', sessionId);

    // Wait for backend to persist (SQLite write)
    await page.waitForTimeout(2000);

    // --- Phase 2: Reload and verify history ---
    await page.reload();

    // Wait for the page to restore the session
    await nimbus.chatInput.waitFor({ state: 'visible', timeout: 10_000 });

    // Wait for message history to load from backend
    const userMessage = page.getByTestId('message-user');
    await expect(userMessage.first()).toBeVisible({ timeout: 15_000 });

    // Verify the history was restored
    const restoredUserMessages = await nimbus.getUserMessages();
    expect(restoredUserMessages.length).toBeGreaterThanOrEqual(3);

    // Verify specific messages are in history
    const hasHello = restoredUserMessages.some((text) =>
      text.toLowerCase().includes('hello'),
    );
    expect(hasHello).toBe(true);

    const hasEcho = restoredUserMessages.some((text) =>
      text.toLowerCase().includes('echo persistence'),
    );
    expect(hasEcho).toBe(true);

    // --- Phase 3: Continue the conversation after reload ---
    await nimbus.sendMessage('hello after reload');
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify both old and new messages coexist
    const allUserMessages = await nimbus.getUserMessages();
    expect(allUserMessages.length).toBeGreaterThanOrEqual(4);

    const hasAfterReload = allUserMessages.some((text) =>
      text.toLowerCase().includes('after reload'),
    );
    expect(hasAfterReload).toBe(true);

    // Verify assistant responded to the post-reload message
    const lastReply = await nimbus.getLastAssistantMessage();
    expect(lastReply).toContain('Nimbus mock agent');
  });
});
