/**
 * Tier 1 E2E Tests: Chat Basic
 *
 * Tests core chat functionality with mocked SSE backend.
 * Covers: welcome screen, send message, empty message guard, keyboard shortcuts.
 */

import { test, expect } from '@playwright/test';
import { setupSSEMock, loadFixture } from '../helpers/sse-mock';

test.describe('Chat Basic', () => {
  // Note: Playwright creates a fresh browser context per test,
  // so localStorage is always empty. No explicit clear needed.

  test('should show welcome screen on load', async ({ page }) => {
    const fixture = loadFixture('simple-chat');
    await setupSSEMock(page, fixture);
    await page.goto('/');

    // Wait for session to be created and welcome screen to appear
    const welcome = page.getByTestId('welcome-screen');
    await expect(welcome).toBeVisible({ timeout: 10000 });

    // Verify key UI elements on the welcome screen
    await expect(page.getByText('Nimbus Agent')).toBeVisible();
    await expect(page.getByText('File Operations')).toBeVisible();
    await expect(page.getByText('Code Execution')).toBeVisible();

    // Chat input should be present and ready
    const input = page.getByTestId('chat-input');
    await expect(input).toBeVisible();

    // Send button should be visible but disabled (no content yet)
    const sendButton = page.getByTestId('send-button');
    await expect(sendButton).toBeVisible();
    await expect(sendButton).toBeDisabled();
  });

  test('should send message and get text response', async ({ page }) => {
    const fixture = loadFixture('simple-chat');
    await setupSSEMock(page, fixture);
    await page.goto('/');

    // Wait for the welcome screen to confirm the page is ready
    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    // Type a message
    const input = page.getByTestId('chat-input');
    await input.fill('Hello');

    // Send button should now be enabled
    const sendButton = page.getByTestId('send-button');
    await expect(sendButton).toBeEnabled();

    // Submit via Enter key
    await input.press('Enter');

    // User message should appear
    const userMsg = page.getByTestId('message-user');
    await expect(userMsg).toBeVisible({ timeout: 5000 });
    await expect(userMsg).toContainText('Hello');

    // Welcome screen should disappear once messages exist
    await expect(page.getByTestId('welcome-screen')).not.toBeVisible();

    // Wait for streaming to complete -- assistant message should appear
    const assistantMsg = page.getByTestId('message-assistant');
    await expect(assistantMsg).toBeVisible({ timeout: 10000 });

    // Verify the assembled content from the SSE fixture
    // The fixture streams: "Hello! " + "I'm Nimbus, " + "your AI assistant. " + "How can I " + "help you today?"
    await expect(assistantMsg).toContainText("Hello!");
    await expect(assistantMsg).toContainText("Nimbus");
    await expect(assistantMsg).toContainText("help you today?");
  });

  test('should not send empty message', async ({ page }) => {
    const fixture = loadFixture('simple-chat');
    await setupSSEMock(page, fixture);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    const input = page.getByTestId('chat-input');
    const sendButton = page.getByTestId('send-button');

    // Initially empty -- button should be disabled
    await expect(sendButton).toBeDisabled();

    // Try pressing Enter on an empty input -- no message should appear
    await input.press('Enter');
    await expect(page.getByTestId('message-user')).not.toBeVisible();

    // Type spaces only -- should still not be sendable
    await input.fill('   ');
    await expect(sendButton).toBeDisabled();

    // Welcome screen should still be visible (no message was sent)
    await expect(page.getByTestId('welcome-screen')).toBeVisible();
  });

  test('should display keyboard shortcuts in input area', async ({ page }) => {
    const fixture = loadFixture('simple-chat');
    await setupSSEMock(page, fixture);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    // Focus the input to reveal hints
    const input = page.getByTestId('chat-input');
    await input.focus();

    // The keyboard hints become visible on focus (opacity transition)
    // Verify they exist in the DOM
    await expect(page.getByText('Send', { exact: true })).toBeVisible({ timeout: 3000 });
    await expect(page.getByText('Line', { exact: true })).toBeVisible({ timeout: 3000 });
  });
});
