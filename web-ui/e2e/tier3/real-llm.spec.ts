/**
 * Tier 3: Real LLM Integration Tests
 *
 * Full end-to-end tests with a REAL LLM backend (e.g., Gemini 3 Flash).
 * These tests verify the complete pipeline works without asserting specific
 * response content, since real LLMs give non-deterministic responses.
 *
 * Prerequisites:
 *   1. Start pi-ai server: npx tsx bridge/pi-ai-server.ts
 *   2. Start Nimbus backend with real model:
 *        NIMBUS_MODEL="google-antigravity/gemini-3-flash" \
 *          python -m uvicorn nimbus.server.app:create_app --factory --port 4096
 *   3. Start web-ui: cd web-ui && npm run dev
 *   4. Run: npx playwright test --project=tier3
 */

import { test, expect } from '@playwright/test';
import { NimbusPage } from '../helpers/nimbus-page';

// ---------------------------------------------------------------------------
// Timeout constants -- real LLMs are slower than mocks
// ---------------------------------------------------------------------------

/** Timeout for waiting for real LLM streaming to begin. */
const STREAMING_START_TIMEOUT = 30_000;

/** Timeout for waiting for real LLM streaming to complete. */
const STREAMING_END_TIMEOUT = 60_000;

// ---------------------------------------------------------------------------
// Test Suite
// ---------------------------------------------------------------------------

test.describe('Tier 3: Real LLM Integration', () => {
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
  // Test 1: Full streaming round-trip
  // =========================================================================

  test('should complete a full streaming round-trip with real LLM', async ({ page }) => {
    // Collect any JS errors during the test
    const jsErrors: string[] = [];
    page.on('pageerror', (error) => jsErrors.push(error.message));

    // Send a simple greeting
    await nimbus.sendMessage('hello');

    // Wait for streaming to start (stop button appears)
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);

    // Wait for streaming to finish (stop button disappears)
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify at least one assistant message element exists
    const assistantMessages = page.getByTestId('message-assistant');
    await expect(assistantMessages.first()).toBeVisible({ timeout: 10_000 });

    // Verify the assistant response is non-empty
    const reply = await nimbus.getLastAssistantMessage();
    expect(reply.length).toBeGreaterThan(0);

    // Verify no JS errors occurred
    expect(jsErrors).toEqual([]);
  });

  // =========================================================================
  // Test 2: Tool execution
  // =========================================================================

  test('should execute tool calls with real LLM', async ({ page }) => {
    test.setTimeout(120_000);

    // Ask the LLM to list files -- it should use Bash or Read tool
    await nimbus.sendMessage(
      'please list the files in the current directory using bash ls command'
    );

    // Wait for streaming lifecycle
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);

    // Wait for at least one tool card to appear
    const toolCard = page.getByTestId('tool-card');
    await expect(toolCard.first()).toBeVisible({ timeout: STREAMING_END_TIMEOUT });

    // Verify at least one tool card is present
    const toolCards = await nimbus.getToolCards();
    expect(toolCards.length).toBeGreaterThanOrEqual(1);

    // Wait for streaming to complete
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify an assistant response exists after tool execution
    const assistantMessages = page.getByTestId('message-assistant');
    await expect(assistantMessages.first()).toBeVisible({ timeout: 10_000 });

    const reply = await nimbus.getLastAssistantMessage();
    expect(reply.length).toBeGreaterThan(0);
  });

  // =========================================================================
  // Test 3: Multi-turn conversation
  // =========================================================================

  test('should handle multi-turn conversation with real LLM', async ({ page }) => {
    test.setTimeout(180_000); // 3 turns with real LLM need extra time

    // --- Turn 1: Greeting ---
    await nimbus.sendMessage('hello');
    await expect(page.getByTestId('message-user').first()).toBeVisible({ timeout: 5_000 });
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify first response exists
    const firstReply = await nimbus.getLastAssistantMessage();
    expect(firstReply.length).toBeGreaterThan(0);

    // --- Turn 2: Simple question ---
    await nimbus.sendMessage('what is 2+2?');
    await expect(page.getByTestId('message-user').nth(1)).toBeVisible({ timeout: 5_000 });
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify second response exists
    const secondReply = await nimbus.getLastAssistantMessage();
    expect(secondReply.length).toBeGreaterThan(0);

    // --- Turn 3: Closing ---
    await nimbus.sendMessage('thanks');
    await expect(page.getByTestId('message-user').nth(2)).toBeVisible({ timeout: 5_000 });
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify we have at least 3 user messages
    const userMessages = await nimbus.getUserMessages();
    expect(userMessages.length).toBeGreaterThanOrEqual(3);

    // Verify at least 1 assistant message is visible
    const assistantMessages = await nimbus.getAssistantMessages();
    expect(assistantMessages.length).toBeGreaterThanOrEqual(1);

    // Verify input is still functional after 3 turns
    await expect(nimbus.chatInput).toBeVisible();
    await expect(nimbus.chatInput).toBeEnabled();
  });

  // =========================================================================
  // Test 4: Session persistence across reload
  // =========================================================================

  test('should persist session across page reload', async ({ page }) => {
    test.setTimeout(120_000);

    // Send a message and wait for the response
    await nimbus.sendMessage('hello');
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify response arrived
    const reply = await nimbus.getLastAssistantMessage();
    expect(reply.length).toBeGreaterThan(0);

    // Save session ID
    const sessionId = await nimbus.getSessionId();
    console.log('Session ID before reload:', sessionId);
    expect(sessionId).not.toBeNull();

    // Wait for backend to persist (SQLite write)
    await page.waitForTimeout(2_000);

    // Reload the page
    await page.reload();

    // Wait for the page to restore the session
    await nimbus.chatInput.waitFor({ state: 'visible', timeout: 10_000 });

    // Wait for message history to load from backend
    const userMessage = page.getByTestId('message-user');
    await expect(userMessage.first()).toBeVisible({ timeout: 15_000 });

    // Verify at least 1 user message was restored
    const userMessages = await nimbus.getUserMessages();
    expect(userMessages.length).toBeGreaterThanOrEqual(1);

    // Verify at least 1 assistant message was restored
    const assistantMessage = page.getByTestId('message-assistant');
    await expect(assistantMessage.first()).toBeVisible({ timeout: 15_000 });

    const assistantMessages = await nimbus.getAssistantMessages();
    expect(assistantMessages.length).toBeGreaterThanOrEqual(1);

    // Each restored assistant message should be non-empty
    for (const msg of assistantMessages) {
      expect(msg.length).toBeGreaterThan(0);
    }
  });

  // =========================================================================
  // Test 5: Streaming interruption
  // =========================================================================

  test('should handle streaming interruption via stop button', async ({ page }) => {
    test.setTimeout(120_000);

    // Send a prompt that will generate a long response
    await nimbus.sendMessage(
      'write a very long essay about artificial intelligence'
    );

    // Wait for streaming to start
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);

    // Give the LLM a moment to start generating tokens
    await page.waitForTimeout(2_000);

    // Click the stop button to interrupt streaming
    await nimbus.clickStop();

    // Verify streaming stopped (stop button hidden)
    await expect(nimbus.stopButton).toBeHidden({ timeout: 10_000 });

    // Verify the chat input is still functional
    await expect(nimbus.chatInput).toBeVisible();
    await expect(nimbus.chatInput).toBeEnabled();

    // Verify we can still type into the input
    await nimbus.chatInput.fill('test input after stop');
    const inputValue = await nimbus.chatInput.inputValue();
    expect(inputValue).toBe('test input after stop');
  });
});
