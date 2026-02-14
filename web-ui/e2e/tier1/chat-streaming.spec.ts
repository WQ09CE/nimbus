/**
 * Tier 1 E2E Tests: Chat Streaming
 *
 * Tests streaming UX: progress indicator, stop button visibility.
 */

import { test, expect } from '@playwright/test';
import { setupSSEMock, loadFixture } from '../helpers/sse-mock';

test.describe('Chat Streaming', () => {
  test('should show streaming progress indicator', async ({ page }) => {
    // Use multi-step fixture which has longer delays so we can observe streaming state
    const fixture = loadFixture('multi-step-dag');
    await setupSSEMock(page, fixture);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    const input = page.getByTestId('chat-input');
    await input.fill('Update the file');
    await input.press('Enter');

    // During streaming, a working indicator should become visible
    const workingIndicator = page.getByTestId('working-indicator');
    await expect(workingIndicator).toBeVisible({ timeout: 5000 });

    // After streaming completes (dag_complete), the working indicator should disappear
    await expect(workingIndicator).not.toBeVisible({ timeout: 15000 });

    // Final assistant message should be present
    const assistantMsg = page.getByTestId('message-assistant');
    await expect(assistantMsg).toBeVisible({ timeout: 5000 });
    await expect(assistantMsg).toContainText('hello, nimbus!');
  });

  test('should show and hide stop button during stream', async ({ page }) => {
    // Use multi-step fixture to have enough time to observe the stop button
    const fixture = loadFixture('multi-step-dag');
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
