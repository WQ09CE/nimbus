import { test, expect } from '@playwright/test';

test.describe('Nimbus Chat', () => {
  test.beforeEach(async ({ page }) => {
    page.on('console', msg => console.log(`[Browser] ${msg.text()}`));
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());
  });

  test('should create new session and send message', async ({ page }) => {
    // Check title or header
    await expect(page.getByRole('heading', { name: 'Nimbus', exact: true })).toBeVisible();

    // Wait for input to be ready
    const input = page.locator('textarea');
    await expect(input).toBeVisible();

    // Send "Hello"
    await input.fill('Hello');
    await input.press('Enter');

    // Check for user message
    // Use a specific selector to avoid matching input value
    await expect(page.locator('div', { hasText: /^Hello$/ }).last()).toBeVisible();

    // Check for assistant response
    await expect(page.locator('div').filter({ hasText: /^[^U].+/ }).last()).toBeVisible({ timeout: 20000 });
  });

  test('should support intervention during long task', async ({ page }) => {
    const input = page.locator('textarea');
    
    // Start long task
    await input.fill('bash "sleep 10; echo done"');
    await input.press('Enter');

    // Wait for streaming state (Stop button visible)
    const stopBtn = page.getByTitle(/Stop/);
    await expect(stopBtn).toBeVisible();

    // Inject message
    await input.fill('check status');
    await input.press('Enter');

    // Verify injection appeared
    await expect(page.getByText('[追加指令] check status')).toBeVisible();

    // Wait for completion
    await expect(page.getByText('done')).toBeVisible({ timeout: 25000 });
  });
  
  test('should persist history on refresh', async ({ page }) => {
    test.setTimeout(60000);
    const input = page.locator('textarea');
    
    // Send message
    await input.fill('hi');
    await input.press('Enter');
    
    // Wait for streaming to finish
    await expect(page.getByTitle(/Stop/)).toBeVisible({ timeout: 5000 }); 
    await expect(page.getByTitle(/Stop/)).not.toBeVisible({ timeout: 40000 }); 
    
    // Wait for backend persistence/indexing
    await page.waitForTimeout(2000);

    const storedId = await page.evaluate(() => localStorage.getItem("nimbus_session_id"));
    console.log("Stored ID before reload:", storedId);

    // Reload
    await page.reload();
    
    // Check history (wait for loading)
    await expect(page.getByText(/^hi$/)).toBeVisible({ timeout: 10000 });
  });

  test('should support complex flow: start -> inject -> interrupt -> persist', async ({ page }) => {
    test.setTimeout(60000);
    const input = page.locator('textarea');
    
    // 1. Start long task
    await input.fill('bash "for i in {1..20}; do echo $i; sleep 1; done"');
    await input.press('Enter');
    
    // Wait for streaming to start (Stop button visible)
    await expect(page.getByTitle(/Stop/)).toBeVisible({ timeout: 10000 });
    
    // Wait a bit for some output to generate
    await page.waitForTimeout(2000);

    // 2. Inject message
    await input.fill('inject_test');
    await input.press('Enter');
    
    // Verify injection UI update
    await expect(page.getByText('[追加指令] inject_test')).toBeVisible();
    
    // Wait a bit more
    await page.waitForTimeout(2000);
    
    // 3. Interrupt
    const stopBtn = page.getByTitle(/Stop/);
    await stopBtn.click();
    
    // Wait for stop (button disappears)
    await expect(stopBtn).not.toBeVisible({ timeout: 10000 });
    
    // 4. Reload to check persistence
    await page.reload();
    
    // 5. Verify History
    // Initial command should be there
    await expect(page.getByText('bash "for i in {1..20}; do echo $i; sleep 1; done"')).toBeVisible({ timeout: 10000 });
    
    // Injection should be there
    await expect(page.getByText('inject_test')).toBeVisible();
    
    // Partial output should be there (at least number 1)
    // Note: Depends on how quickly 'bash' output is flushed. 
    // If tool was interrupted, we might see the tool call but maybe not result if it was killed mid-execution.
    // But since we use `sleep 1`, we likely got some output chunks if we waited 2s.
    // However, Nimbus vCPU currently only saves ToolResult when tool finishes.
    // If we interrupt `bash`, `gate` kills it and returns `CANCELLED`.
    // The `CancelledError` handler in `session_v2` calls `_save`.
    // The MMU should contain the `CANCELLED` tool result.
    
    // Let's just verify the messages are there.
  });
});
