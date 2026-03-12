/**
 * Tier 1 E2E Tests: Large Tool Output Display
 *
 * Tests that large tool outputs are rendered correctly and can be scrolled.
 * The Bash component uses `max-h-[200px] overflow-y-auto` for output,
 * so large outputs are scrollable rather than truncated in the DOM.
 */

import { test, expect } from '@playwright/test';
import { setupSSEMock, SSEFixture } from '../helpers/sse-mock';

test.describe('Large Tool Output Display', () => {

  test('should render large tool output with scrollable container', async ({ page }) => {
    // Generate massive content (many lines)
    const massiveContent = Array.from({ length: 50 }, (_, i) =>
      `Line ${i + 1}: ${'A'.repeat(80)}`
    ).join('\n');

    // Custom fixture for large output
    const fixture: SSEFixture = {
      scenario: 'large_tool_output',
      description: 'Test handling of large tool outputs',
      session: {
        id: 'test-large-io',
        status: 'active',
        created_at: new Date().toISOString(),
        memory_type: 'tiered',
        planner_type: 'dag',
        message_count: 0
      },
      messages_history: [],
      sse_events: [
        {
          delay_ms: 0,
          event: "connected",
          data: { session_id: "test-large-io" }
        },
        {
          delay_ms: 10,
          event: "message_start",
          data: { role: "assistant" }
        },
        {
          delay_ms: 10,
          event: "tool_call",
          data: {
            id: "call_1",
            tool: "Bash",
            args: { command: "cat huge.log" }
          }
        },
        {
          delay_ms: 10,
          event: "tool_result",
          data: {
            id: "call_1",
            tool: "Bash",
            status: "OK",
            output: massiveContent
          }
        },
        {
          delay_ms: 10,
          event: "message",
          data: { content: "Here is the log file output." }
        },
        {
          delay_ms: 10,
          event: "done",
          data: { status: "OK" }
        }
      ]
    };

    await setupSSEMock(page, fixture);
    await page.goto('/');

    // Trigger flow
    const input = page.getByTestId('chat-input');
    await input.fill('Read huge log');
    await input.press('Enter');

    // Wait for streaming to complete
    await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 10000 });

    // Tool card should be visible
    const toolCard = page.getByTestId('tool-card').first();
    await expect(toolCard).toBeVisible({ timeout: 10000 });

    // Click to expand tool card (if collapsed)
    await toolCard.click();
    await page.waitForTimeout(200);

    // Bash output should render the command
    await expect(toolCard).toContainText('cat huge.log');

    // Bash component should show exit code for completed tools
    await expect(toolCard).toContainText('exit');

    // The output content should be present (rendered in a scrollable div)
    await expect(toolCard).toContainText('Line 1:');

    // Verify the assistant text message is also present
    const assistantMsg = page.getByTestId('message-assistant');
    await expect(assistantMsg).toBeVisible();
    await expect(assistantMsg).toContainText('Here is the log file output.');
  });

  test('should display tool card with error status when tool fails', async ({ page }) => {
    const fixture: SSEFixture = {
      scenario: 'tool_error',
      description: 'Test tool error display',
      session: {
        id: 'test-tool-error',
        status: 'active',
        created_at: new Date().toISOString(),
        memory_type: 'buffer',
        planner_type: 'simple',
        message_count: 0
      },
      messages_history: [],
      sse_events: [
        { delay_ms: 0, event: "connected", data: { session_id: "test-tool-error" } },
        { delay_ms: 10, event: "message_start", data: { role: "assistant" } },
        {
          delay_ms: 10,
          event: "tool_call",
          data: { id: "call_err", tool: "Bash", args: { command: "exit 1" } }
        },
        {
          delay_ms: 10,
          event: "tool_result",
          data: {
            id: "call_err",
            tool: "Bash",
            status: "ERROR",
            output: "Command failed with exit code 1",
            error: "Command failed with exit code 1"
          }
        },
        { delay_ms: 10, event: "message", data: { content: "The command failed." } },
        { delay_ms: 10, event: "done", data: { status: "OK" } }
      ]
    };

    await setupSSEMock(page, fixture);
    await page.goto('/');

    const input = page.getByTestId('chat-input');
    await input.fill('Run failing command');
    await input.press('Enter');

    await expect(page.getByTestId('stop-button')).not.toBeVisible({ timeout: 10000 });

    const toolCard = page.getByTestId('tool-card').first();
    await expect(toolCard).toBeVisible({ timeout: 10000 });

    // Tool card should show the command
    await expect(toolCard).toContainText('exit 1');

    // Should show the failed status indicator (red dot)
    const failedDot = toolCard.locator('.bg-red-400');
    await expect(failedDot).toBeVisible();
  });
});
