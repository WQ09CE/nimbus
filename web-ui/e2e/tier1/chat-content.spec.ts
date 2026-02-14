/**
 * Tier 1 E2E Tests: Chat Content
 *
 * Tests Markdown rendering and error display.
 */

import { test, expect } from '@playwright/test';
import { setupSSEMock, loadFixture } from '../helpers/sse-mock';

test.describe('Chat Content', () => {
  test('should render markdown correctly', async ({ page }) => {
    const fixture = loadFixture('markdown-response');
    await setupSSEMock(page, fixture);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    const input = page.getByTestId('chat-input');
    await input.fill('How do I set up Python?');
    await input.press('Enter');

    // Wait for streaming to complete
    await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 15000 });

    const assistantMsg = page.getByTestId('message-assistant');
    await expect(assistantMsg).toBeVisible({ timeout: 5000 });

    // The markdown fixture contains:
    // - h2: "Getting Started with Python"
    // - h3: "1. Create a virtual environment", "2. Install dependencies", "3. Project structure"
    // - code blocks (bash, python)
    // - bullet list (src/, tests/, docs/)
    // - inline code: `pip install -r requirements.txt`
    // - blockquote: "> **Note**: Make sure you have Python 3.10+ installed."

    // Verify headings are rendered
    await expect(assistantMsg.locator('h2')).toBeVisible();
    await expect(assistantMsg.getByText('Getting Started')).toBeVisible();

    // Verify code blocks are rendered (the markdown renderer wraps them in styled divs)
    // bash code block with "python -m venv .venv"
    await expect(assistantMsg.getByText('python -m venv .venv')).toBeVisible();

    // python code block with "Hello, world!"
    await expect(assistantMsg.getByText('Hello, world!')).toBeVisible();

    // Verify list items are present
    await expect(assistantMsg.getByText('Source code')).toBeVisible();
    await expect(assistantMsg.getByText('Unit tests')).toBeVisible();

    // Verify inline code is rendered
    await expect(assistantMsg.getByText('pip install -r requirements.txt')).toBeVisible();

    // Verify blockquote content
    await expect(assistantMsg.getByText('Python 3.10+')).toBeVisible();
  });

  test('should display error banner on error', async ({ page }) => {
    const fixture = loadFixture('error-response');
    await setupSSEMock(page, fixture);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    const input = page.getByTestId('chat-input');
    await input.fill('Do something');
    await input.press('Enter');

    // The error fixture sends a connected event, then an error event.
    // The chat store throws on error events, which sets the error state.
    // The error banner should appear in the UI.
    const errorBanner = page.getByTestId('error-banner');
    await expect(errorBanner).toBeVisible({ timeout: 10000 });

    // Verify it contains the error text from the fixture
    await expect(errorBanner).toContainText('Error');

    // The dismiss button should be present
    const dismissButton = errorBanner.getByText('Dismiss');
    await expect(dismissButton).toBeVisible();

    // Click dismiss to clear the error
    await dismissButton.click();
    await expect(errorBanner).not.toBeVisible({ timeout: 3000 });
  });
});
