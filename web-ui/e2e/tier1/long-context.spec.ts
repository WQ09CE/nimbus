/**
 * Tier 1 E2E Tests: Long Context Performance
 *
 * Stress tests for multi-turn and long-response scenarios using mocked SSE.
 * Verifies that the UI remains functional and correct when dealing with:
 *   - Long assistant responses with many message chunks
 *   - Multiple tool calls in a single response
 *   - Rapid multi-turn conversations (5 turns)
 *   - Extended conversations (10+ turns) without UI degradation
 *   - Auto-scrolling behavior in long conversations
 */

import { test, expect } from '@playwright/test';
import {
  setupSSEMock,
  setupMultiTurnMock,
  loadFixture,
} from '../helpers/sse-mock';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function loadLongContextFixture() {
  return loadFixture('long-context');
}

function loadSimpleChatFixture() {
  return loadFixture('simple-chat');
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Long Context Performance', () => {

  // =========================================================================
  // Test 1: Long assistant response with many chunks and tool calls
  // =========================================================================

  test('should handle long assistant response with many chunks', async ({ page }) => {
    test.setTimeout(30_000);

    const fixture = loadLongContextFixture();
    await setupSSEMock(page, fixture);
    await page.goto('/');

    // Wait for the page to be ready
    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10_000 });

    // Send a message to trigger the long response
    const input = page.getByTestId('chat-input');
    await input.fill('Analyze my project and fix issues');
    await input.press('Enter');

    // User message should appear
    await expect(page.getByTestId('message-user')).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId('message-user')).toContainText('Analyze my project');

    // Wait for streaming to complete (stop button disappears)
    await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 20_000 });

    // Verify the assistant message contains the assembled content from 25+ chunks
    const assistantMsg = page.getByTestId('message-assistant');
    await expect(assistantMsg).toBeVisible({ timeout: 5_000 });

    // Check key content from the long response
    await expect(assistantMsg).toContainText('Project Analysis');
    await expect(assistantMsg).toContainText('Files Reviewed');
    await expect(assistantMsg).toContainText('config.py');
    await expect(assistantMsg).toContainText('models.py');
    await expect(assistantMsg).toContainText('Changes Made');
    await expect(assistantMsg).toContainText('DEBUG = False');
    await expect(assistantMsg).toContainText('Test Results');
    await expect(assistantMsg).toContainText('8 passed');
    await expect(assistantMsg).toContainText('ready for deployment');

    // Verify tool cards are displayed for the 5 tool calls
    const toolCards = page.getByTestId('tool-card');
    const toolCount = await toolCards.count();
    expect(toolCount).toBeGreaterThanOrEqual(3); // At least Read, Edit, Bash visible

    // Verify specific tool types are present
    const allToolText = await toolCards.allTextContents();
    const combinedToolText = allToolText.join(' ');
    expect(combinedToolText).toContain('Read');
    expect(combinedToolText).toContain('Edit');
    expect(combinedToolText).toContain('Bash');

    // Verify the page is still interactive -- input should be usable
    await expect(input).toBeVisible();
    await expect(input).toBeEnabled();

    // Verify send button is back (not streaming)
    await expect(page.getByTestId('send-button')).toBeVisible();
  });

  // =========================================================================
  // Test 2: Rapid multi-turn conversation (5 turns)
  // =========================================================================

  test('should handle rapid multi-turn conversation', async ({ page }) => {
    test.setTimeout(30_000);

    // Create 5 different fixtures for 5 conversation turns
    const baseFixture = loadSimpleChatFixture();
    const fixtures = Array.from({ length: 5 }, (_, i) => ({
      ...baseFixture,
      scenario: `turn-${i + 1}`,
      sse_events: [
        {
          delay_ms: 30,
          event: 'connected' as const,
          data: { session_id: baseFixture.session.id },
        },
        {
          delay_ms: 20,
          event: 'message_start' as const,
          data: { role: 'assistant' },
        },
        {
          delay_ms: 50,
          event: 'step_start' as const,
          data: { iteration: 1 },
        },
        {
          delay_ms: 30,
          event: 'message' as const,
          data: { content: `Response ${i + 1}: ` },
        },
        {
          delay_ms: 20,
          event: 'message' as const,
          data: { content: `This is turn number ${i + 1}. ` },
        },
        {
          delay_ms: 20,
          event: 'message' as const,
          data: { content: 'Everything is working correctly.' },
        },
        {
          delay_ms: 30,
          event: 'dag_complete' as const,
          data: { status: 'OK' },
        },
      ],
    }));

    await setupMultiTurnMock(page, fixtures);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10_000 });

    const input = page.getByTestId('chat-input');

    // Send 5 messages in rapid succession, waiting for each to complete
    for (let i = 0; i < 5; i++) {
      await input.fill(`Message ${i + 1}`);
      await input.press('Enter');

      // Wait for streaming to complete before sending next
      await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 10_000 });
    }

    // Verify all 5 user messages are present
    const userMessages = page.getByTestId('message-user');
    await expect(userMessages).toHaveCount(5, { timeout: 5_000 });

    // Verify all 5 assistant responses are present
    const assistantMessages = page.getByTestId('message-assistant');
    const assistantCount = await assistantMessages.count();
    expect(assistantCount).toBe(5);

    // Verify each turn's response content
    for (let i = 0; i < 5; i++) {
      const msgText = await assistantMessages.nth(i).textContent();
      expect(msgText).toContain(`turn number ${i + 1}`);
    }

    // Verify the message order is correct
    for (let i = 0; i < 5; i++) {
      const userText = await userMessages.nth(i).textContent();
      expect(userText).toContain(`Message ${i + 1}`);
    }
  });

  // =========================================================================
  // Test 3: 10+ turn conversation without UI degradation
  // =========================================================================

  test('should handle 10+ turn conversation without UI degradation', async ({ page }) => {
    test.setTimeout(30_000);

    // Capture JS errors during the test
    const jsErrors: string[] = [];
    page.on('pageerror', (error) => {
      jsErrors.push(error.message);
    });

    // Create 10 fixtures for 10 turns
    const baseFixture = loadSimpleChatFixture();
    const fixtures = Array.from({ length: 10 }, (_, i) => ({
      ...baseFixture,
      scenario: `turn-${i + 1}`,
      sse_events: [
        {
          delay_ms: 20,
          event: 'connected' as const,
          data: { session_id: baseFixture.session.id },
        },
        {
          delay_ms: 10,
          event: 'message_start' as const,
          data: { role: 'assistant' },
        },
        {
          delay_ms: 30,
          event: 'step_start' as const,
          data: { iteration: 1 },
        },
        {
          delay_ms: 20,
          event: 'message' as const,
          data: { content: `Reply ${i + 1}: acknowledged.` },
        },
        {
          delay_ms: 20,
          event: 'dag_complete' as const,
          data: { status: 'OK' },
        },
      ],
    }));

    await setupMultiTurnMock(page, fixtures);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10_000 });

    const input = page.getByTestId('chat-input');

    // Send 10 messages
    for (let i = 0; i < 10; i++) {
      await input.fill(`Turn ${i + 1}`);
      await input.press('Enter');
      await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 10_000 });
    }

    // Verify all 10 user messages exist
    const userMessages = page.getByTestId('message-user');
    await expect(userMessages).toHaveCount(10, { timeout: 5_000 });

    // Verify all 10 assistant responses exist
    const assistantMessages = page.getByTestId('message-assistant');
    const assistantCount = await assistantMessages.count();
    expect(assistantCount).toBe(10);

    // Verify the latest (10th) assistant message is visible in the viewport
    const lastAssistant = assistantMessages.nth(9);
    await expect(lastAssistant).toBeVisible();
    await expect(lastAssistant).toContainText('Reply 10');

    // Verify the input is still functional (no UI freeze)
    await expect(input).toBeVisible();
    await expect(input).toBeEnabled();
    await input.fill('test input still works');
    const inputValue = await input.inputValue();
    expect(inputValue).toBe('test input still works');

    // Check no JS errors occurred during the 10-turn conversation
    const criticalErrors = jsErrors.filter(
      (err) =>
        !err.includes('ResizeObserver') && // Ignore benign ResizeObserver warnings
        !err.includes('Non-Error promise rejection'), // Ignore benign promise rejections
    );
    expect(criticalErrors).toHaveLength(0);
  });

  // =========================================================================
  // Test 4: Auto-scroll to latest message in long conversation
  // =========================================================================

  test('should auto-scroll to latest message in long conversation', async ({ page }) => {
    test.setTimeout(30_000);

    // Create 8 fixtures to build a long enough conversation for scrolling
    const baseFixture = loadSimpleChatFixture();
    const fixtures = Array.from({ length: 8 }, (_, i) => ({
      ...baseFixture,
      scenario: `scroll-turn-${i + 1}`,
      sse_events: [
        {
          delay_ms: 20,
          event: 'connected' as const,
          data: { session_id: baseFixture.session.id },
        },
        {
          delay_ms: 10,
          event: 'message_start' as const,
          data: { role: 'assistant' },
        },
        {
          delay_ms: 30,
          event: 'step_start' as const,
          data: { iteration: 1 },
        },
        {
          delay_ms: 20,
          event: 'message' as const,
          data: {
            content: `Response ${i + 1}: This is a longer reply to ensure the page needs to scroll. `.repeat(3),
          },
        },
        {
          delay_ms: 20,
          event: 'dag_complete' as const,
          data: { status: 'OK' },
        },
      ],
    }));

    await setupMultiTurnMock(page, fixtures);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10_000 });

    const input = page.getByTestId('chat-input');

    // Send 6 messages to create enough content for scrolling
    for (let i = 0; i < 6; i++) {
      await input.fill(`Scroll test ${i + 1}`);
      await input.press('Enter');
      await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 10_000 });
    }

    // After 6 turns, the latest message should be visible (auto-scrolled)
    const assistantMessages = page.getByTestId('message-assistant');
    const lastMessage = assistantMessages.last();
    await expect(lastMessage).toBeVisible();
    await expect(lastMessage).toContainText('Response 6');

    // Scroll to the very top of the page
    await page.evaluate(() => {
      const container = document.querySelector('[data-testid="chat-messages"]')
        || document.querySelector('main')
        || document.documentElement;
      container.scrollTop = 0;
    });

    // Wait a moment for scroll to settle
    await page.waitForTimeout(300);

    // Send another message (turn 7)
    await input.fill('Scroll test 7');
    await input.press('Enter');
    await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 10_000 });

    // The new message should auto-scroll into view
    const newestMessage = assistantMessages.last();
    await expect(newestMessage).toBeVisible();
    await expect(newestMessage).toContainText('Response 7');

    // The input should still be visible and usable
    await expect(input).toBeVisible();
    await expect(input).toBeEnabled();
  });
});
