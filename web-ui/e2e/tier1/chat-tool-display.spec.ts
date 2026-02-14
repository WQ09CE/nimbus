/**
 * Tier 1 E2E Tests: Tool Display
 *
 * Tests tool card rendering for Bash commands and multi-step tool execution.
 */

import { test, expect } from '@playwright/test';
import { setupSSEMock, loadFixture } from '../helpers/sse-mock';

test.describe('Tool Display', () => {

  test('should display Bash tool card with command', async ({ page }) => {
    const fixture = loadFixture('tool-call-bash');
    await setupSSEMock(page, fixture);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    const input = page.getByTestId('chat-input');
    await input.fill('Run echo hello');
    await input.press('Enter');

    // Wait for streaming to complete
    await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 15000 });

    // Assistant message should contain the final text content
    const assistantMsg = page.getByTestId('message-assistant');
    await expect(assistantMsg).toBeVisible({ timeout: 5000 });
    await expect(assistantMsg).toContainText('echo hello');
    await expect(assistantMsg).toContainText('executed successfully');

    // The tool card is rendered directly via AgentProcess (not behind a toggle)
    // because the tool step is a separate message from the text step.
    const toolCards = page.getByTestId('tool-card');
    await expect(toolCards.first()).toBeVisible({ timeout: 5000 });

    // The tool card header should show "Bash" and the command summary "echo hello"
    await expect(toolCards.first()).toContainText('Bash');
    await expect(toolCards.first()).toContainText('echo hello');
  });

  test('should display multi-step tool execution', async ({ page }) => {
    const fixture = loadFixture('multi-step-dag');
    await setupSSEMock(page, fixture);
    await page.goto('/');

    await expect(page.getByTestId('welcome-screen')).toBeVisible({ timeout: 10000 });

    const input = page.getByTestId('chat-input');
    await input.fill('Update the greeting in main.py');
    await input.press('Enter');

    // Wait for streaming to complete
    await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 15000 });

    // The assistant message should contain the final response text
    const assistantMsg = page.getByTestId('message-assistant');
    await expect(assistantMsg).toBeVisible({ timeout: 5000 });
    await expect(assistantMsg).toContainText('hello, nimbus!');

    // There should be tool usage indicated somewhere on the page
    // The multi-step fixture produces 2 tool calls: Read + Edit
    // In ChatList, process steps with tools get rendered via AgentProcess
    // and the final text-only message renders as ChatMessage.
    // Check for tool cards in the AgentProcess section
    const toolCards = page.getByTestId('tool-card');
    // toolCards may need the AgentProcess section to be expanded
    // The tool cards are rendered inside the AgentProcess component
    // which automatically shows tools (no toggle needed for process steps)
    const toolCount = await toolCards.count();
    expect(toolCount).toBeGreaterThanOrEqual(1);

    // Verify Read and Edit tools are present
    // Click to expand if needed (tool cards in AgentProcess are shown by default)
    const allToolText = await page.locator('[data-testid="tool-card"]').allTextContents();
    const combinedToolText = allToolText.join(' ');
    expect(combinedToolText).toContain('Read');
    expect(combinedToolText).toContain('Edit');
  });
});
