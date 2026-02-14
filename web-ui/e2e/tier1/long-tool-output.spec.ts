/**
 * Tier 1 E2E Tests: Large Tool Output Display
 *
 * Tests truncation and folding of massive tool outputs.
 */

import { test, expect } from '@playwright/test';
import { setupSSEMock } from '../helpers/sse-mock';

test.describe('Large Tool Output Display', () => {

  test('should fold large tool outputs and show truncation warning', async ({ page }) => {
    // Generate massive content
    const massiveContent = "A".repeat(600) + "\n\n⚠️ [Output Truncated]";
    
    // Custom fixture for large output
    const fixture = [
      {
        event: "connected",
        data: { session_id: "test-large-io" }
      },
      {
        event: "message_start",
        data: { role: "assistant" }
      },
      {
        event: "tool_call",
        data: {
          id: "call_1",
          tool: "Bash",
          args: { command: "cat huge.log" }
        }
      },
      {
        event: "tool_result",
        data: {
          id: "call_1",
          tool: "Bash",
          status: "OK",
          output: massiveContent
        }
      },
      {
        event: "message",
        data: { content: "Here is the (truncated) log file." }
      },
      {
        event: "dag_complete",
        data: { status: "OK" }
      }
    ];

    await setupSSEMock(page, fixture);
    await page.goto('/');

    // Trigger flow
    const input = page.getByTestId('chat-input');
    await input.fill('Read huge log');
    await input.press('Enter');

    // Wait for tool card
    const toolCard = page.getByTestId('tool-card').first();
    await expect(toolCard).toBeVisible({ timeout: 10000 });
    
    // Click to expand tool card
    await toolCard.click();

    // 1. Check for collapse behavior
    const outputSection = toolCard.locator('.text-green-300\\/90'); // DataDisplay string class
    await expect(outputSection).toBeVisible();
    
    // Should show "Show all" button
    const showMoreBtn = toolCard.getByRole('button', { name: /Show all/ });
    await expect(showMoreBtn).toBeVisible();
    
    // Verify content is truncated in view (preview length < 600)
    const previewText = await outputSection.textContent();
    expect(previewText?.length).toBeLessThan(600);
    expect(previewText).toContain('...');

    // 2. Check for System Truncation Warning
    // The "⚠️ Partially Truncated by System" badge should be visible when collapsed
    // because our content contains the truncate marker
    const warningBadge = toolCard.getByText('⚠️ Partially Truncated by System');
    await expect(warningBadge).toBeVisible();

    // 3. Expand content
    await showMoreBtn.click();
    
    // Button should change to "Collapse"
    await expect(toolCard.getByRole('button', { name: 'Collapse' })).toBeVisible();
    
    // Warning badge should disappear when expanded
    await expect(warningBadge).not.toBeVisible();
    
    // Content should be full length
    const expandedText = await outputSection.textContent();
    // Use loose check because of whitespace/quotes added by DataDisplay
    expect(expandedText?.length).toBeGreaterThan(600); 
    expect(expandedText).toContain('⚠️ [Output Truncated]');
  });

});
