/**
 * Tier 2: Full-Stack Integration Tests
 *
 * These tests exercise the complete UI -> Next.js -> Nimbus Server -> AgentOS
 * -> vCPU -> MockLLM -> Tools pipeline. No page.route() interception is used;
 * all HTTP requests reach the real Nimbus backend running with NIMBUS_LLM=mock.
 *
 * Prerequisites:
 *   1. Start the Nimbus backend:
 *        NIMBUS_LLM=mock nimbus serve --port 4096
 *      or equivalently:
 *        NIMBUS_LLM=mock python -m uvicorn nimbus.server.app:create_app \
 *          --factory --host 127.0.0.1 --port 4096
 *
 *   2. Start the Next.js dev server (handled by playwright.config.ts webServer):
 *        cd web-ui && npm run dev
 *
 *   3. Run:
 *        npx playwright test --project=tier2
 *      or:
 *        npm run test:e2e:integration
 *
 * MockLLM Rules (see src/nimbus/testing/mock_llm.py):
 *   - /^hello|hi|hey/i       -> text: "Hello! I'm Nimbus mock agent. How can I help?"
 *   - /echo\s+(.+)/i         -> Bash tool_call: echo <payload>
 *   - /read\s+(.+)/i         -> Read tool_call: read <file_path>
 *   - /count\s+to\s+(\d+)/i  -> multi-step Write tool_calls
 *   - /error/i               -> text: "An error occurred while processing..."
 *   - default                -> text: "I understand. Let me help you with that."
 *
 * Timeout Strategy:
 *   - Project-level timeout: 60s (set in playwright.config.ts tier2 project)
 *   - Per-test overrides via test.setTimeout() where needed
 *   - Streaming start: 15s (backend may take time to warm up on first request)
 *   - Streaming end: 30s (tool execution + vCPU loop)
 */

import { test, expect } from '@playwright/test';
import { NimbusPage } from '../helpers/nimbus-page';

// ---------------------------------------------------------------------------
// Shared constants
// ---------------------------------------------------------------------------

/** Timeout for waiting for backend streaming to begin. */
const STREAMING_START_TIMEOUT = 15_000;

/** Timeout for waiting for backend streaming to complete. */
const STREAMING_END_TIMEOUT = 30_000;

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

test.describe('Tier 2: Full-Stack Integration', () => {
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
  // Test 1: Full chat round-trip
  // =========================================================================

  test('should complete full chat round-trip', async ({ page }) => {
    // Send a greeting -- MockLLM rule 1 matches /^hello/i
    await nimbus.sendMessage('hello');

    // Wait for streaming to start (stop button appears)
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);

    // Wait for streaming to finish (stop button disappears)
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify assistant response contains the mock greeting text
    const reply = await nimbus.getLastAssistantMessage();
    expect(reply).toContain('Nimbus mock agent');
  });

  // =========================================================================
  // Test 2: Bash tool execution
  // =========================================================================

  test('should execute Bash tool and display result', async ({ page }) => {
    // Send "echo hello world" -- MockLLM rule 2 matches /echo\s+(.+)/i
    // MockLLM returns a Bash tool_call with command "echo hello world"
    await nimbus.sendMessage('echo hello world');

    // Wait for streaming lifecycle
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);

    // Wait for tool card to appear (Bash tool execution)
    const toolCard = page.getByTestId('tool-card');
    await expect(toolCard.first()).toBeVisible({ timeout: STREAMING_END_TIMEOUT });

    // Verify the tool card mentions "Bash"
    const toolCards = await nimbus.getToolCards();
    const hasBashTool = toolCards.some(
      (card) => card.name.toLowerCase().includes('bash')
    );
    expect(hasBashTool).toBe(true);

    // Wait for streaming to complete
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify the companion text "I'll echo that for you." is visible somewhere.
    // The MockLLM echo rule returns both content and tool_calls; the content
    // is rendered inline (not necessarily as a separate message-assistant element).
    await expect(page.getByText("I'll echo that for you.").first()).toBeVisible();

    // Verify the tool card shows the echo command
    await expect(toolCard.first()).toContainText('echo hello world');
  });

  // =========================================================================
  // Test 3: Multi-turn conversation
  // =========================================================================

  test('should handle multi-turn conversation', async ({ page }) => {
    // --- Turn 1: Greeting ---
    await nimbus.sendMessage('hello');
    // Wait for the first user message to be rendered before proceeding
    await expect(page.getByTestId('message-user').first()).toBeVisible({ timeout: 5000 });
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify first response
    const firstReply = await nimbus.getLastAssistantMessage();
    expect(firstReply).toContain('Nimbus mock agent');

    // --- Turn 2: Default rule ---
    await nimbus.sendMessage('what is the weather');
    // Wait for the second user message to be rendered
    await expect(page.getByTestId('message-user').nth(1)).toBeVisible({ timeout: 5000 });
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify second response (default rule)
    const allMessages = await nimbus.getAllMessages();
    const userMessages = allMessages.filter((m) => m.role === 'user');
    const assistantMessages = allMessages.filter((m) => m.role === 'assistant');

    // Should have at least 2 user messages and 2 assistant messages
    expect(userMessages.length).toBeGreaterThanOrEqual(2);
    expect(assistantMessages.length).toBeGreaterThanOrEqual(2);
  });

  // =========================================================================
  // Test 4: History persistence on reload
  // =========================================================================

  test('should persist history on reload', async ({ page }) => {
    test.setTimeout(90_000); // Extended timeout for full flow + reload

    // --- Send a message and wait for complete response ---
    await nimbus.sendMessage('hello');
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify response arrived
    const reply = await nimbus.getLastAssistantMessage();
    expect(reply).toContain('Nimbus mock agent');

    // Get session ID from localStorage (the frontend stores it)
    const sessionId = await nimbus.getSessionId();
    console.log('Session ID before reload:', sessionId);
    expect(sessionId).not.toBeNull();

    // Wait a moment for backend to persist (SQLite write)
    await page.waitForTimeout(2000);

    // --- Reload the page ---
    await page.reload();

    // Wait for the page to restore the session
    await nimbus.chatInput.waitFor({ state: 'visible', timeout: 10_000 });

    // Wait for message history to load from backend
    // The frontend should fetch /api/v1/sessions/:id/messages on load
    // and render the previous conversation
    const userMessage = page.getByTestId('message-user');
    await expect(userMessage.first()).toBeVisible({ timeout: 15_000 });

    // Verify the user message "hello" is still visible
    const userMessages = await nimbus.getUserMessages();
    const hasHello = userMessages.some((text) =>
      text.toLowerCase().includes('hello')
    );
    expect(hasHello).toBe(true);

    // Verify the assistant response is still visible
    const assistantMessages = await nimbus.getAssistantMessages();
    expect(assistantMessages.length).toBeGreaterThanOrEqual(1);
    const hasNimbusReply = assistantMessages.some((text) =>
      text.includes('Nimbus mock agent')
    );
    expect(hasNimbusReply).toBe(true);
  });

  // =========================================================================
  // Test 5: Error scenario
  // =========================================================================

  test('should handle error scenario', async ({ page }) => {
    // Send "trigger error" -- MockLLM rule 5 matches /error/i
    // MockLLM returns a text response (NOT a server-side error), containing
    // an error-related message.
    await nimbus.sendMessage('trigger error');

    // Wait for streaming lifecycle
    await nimbus.waitForStreamingStart(STREAMING_START_TIMEOUT);
    await nimbus.waitForStreamingEnd(STREAMING_END_TIMEOUT);

    // Verify the response contains error-related text
    // MockLLM returns: "An error occurred while processing your request.
    //                   Please check the input and try again."
    const reply = await nimbus.getLastAssistantMessage();
    expect(reply.toLowerCase()).toContain('error');
  });
});
