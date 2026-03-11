/**
 * Tier 1 E2E Tests: Session Management
 *
 * Tests session persistence on reload and new session creation.
 *
 * Note: Playwright creates a fresh browser context per test, so localStorage
 * is always empty at test start. No explicit clear needed.
 */

import { test, expect } from '@playwright/test';
import { setupSSEMock, loadFixture } from '../helpers/sse-mock';

test.describe('Session Management', () => {

  test('should persist messages on reload', async ({ page }) => {
    // Use simple-chat fixture with messages_history populated for the reload
    const fixture = loadFixture('simple-chat');

    // Patch messages_history so that after reload, GET /messages returns these
    const fixtureWithHistory = {
      ...fixture,
      messages_history: [
        {
          role: 'user',
          content: 'Hello',
          timestamp: '2026-01-01T00:00:00Z',
        },
        {
          role: 'assistant',
          content: "Hello! I'm Nimbus, your AI assistant. How can I help you today?",
          timestamp: '2026-01-01T00:00:01Z',
        },
      ],
    };

    await setupSSEMock(page, fixtureWithHistory);
    await page.goto('/');

    // Wait for session to be created and welcome screen to show
    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    // Send a message to trigger the SSE stream
    const input = page.getByTestId('chat-input');
    await input.fill('Hello');
    await input.press('Enter');

    // Wait for assistant response to complete
    await expect(page.getByTestId('message-assistant')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 10000 });

    // Verify session ID is persisted in sessionStorage (app uses sessionStorage, not localStorage)
    const sessionId = await page.evaluate(() => sessionStorage.getItem('nimbus_session_id'));
    expect(sessionId).toBeTruthy();
    expect(sessionId).toBe('test-session-001');

    // Reload the page.
    // Route mocks persist across reload in Playwright.
    // The app reads nimbus_session_id from localStorage, calls loadSession,
    // which calls getSession + getSessionMessages.
    await page.reload({ waitUntil: 'networkidle' });

    // Wait for the app to fully hydrate and load session data
    // After reload, the app fetches messages from the mock and renders them
    await expect(page.getByTestId('message-user')).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId('message-user')).toContainText('Hello');

    // Welcome screen should NOT be visible (messages exist)
    await expect(page.getByTestId('welcome-screen')).not.toBeVisible();
  });

  test('should create new session on New Chat click', async ({ page }) => {
    const fixture = loadFixture('simple-chat');
    await setupSSEMock(page, fixture);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    // Send a message first so we have content
    const input = page.getByTestId('chat-input');
    await input.fill('Hello');
    await input.press('Enter');

    // Wait for response
    await expect(page.getByTestId('message-assistant')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 10000 });

    // Now click "New Chat" button
    const newChatButton = page.getByTestId('new-chat-button');
    await expect(newChatButton).toBeVisible();
    await newChatButton.click();

    // After clicking new chat, messages should be cleared and welcome screen should return
    // The mock will handle POST /sessions to create a new session
    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    // Previous messages should no longer be visible
    await expect(page.getByTestId('message-user')).not.toBeVisible();
    await expect(page.getByTestId('message-assistant')).not.toBeVisible();
  });

  test('should open session panel', async ({ page }) => {
    const fixture = loadFixture('simple-chat');
    await setupSSEMock(page, fixture);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    // Click the session panel trigger (session ID displayed in header)
    const sessionTrigger = page.getByTestId('session-panel-trigger');
    await expect(sessionTrigger).toBeVisible({ timeout: 5000 });
    await sessionTrigger.click();

    // Session panel should slide in -- use the heading which is unique
    await expect(page.locator('h2', { hasText: 'Sessions' })).toBeVisible({ timeout: 5000 });

    // Verify the session count text is shown
    await expect(page.getByText(/\d+ Conversations/)).toBeVisible();

    // The current session should be listed in the panel
    // The mock returns the session in listSessions (GET /api/v1/sessions)
    // Session name defaults to "Session <id_prefix>" where the id is "test-session-001"
    await expect(page.getByText('Session test-ses')).toBeVisible({ timeout: 5000 });
  });
});
