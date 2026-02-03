import { test, expect } from '@playwright/test';

test.describe('Nimbus Chat', () => {
  test.beforeEach(async ({ page }) => {
    // Clear storage to start fresh
    await page.addInitScript(() => {
      localStorage.clear();
    });
    await page.goto('/');
  });

  test('should create new session and send message', async ({ page }) => {
    // Check title or header
    await expect(page.getByRole('heading', { name: 'Nimbus' })).toBeVisible();

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
    // Wait for any non-empty assistant message
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
    const input = page.locator('textarea');
    
    // Send message
    await input.fill('Persist me');
    await input.press('Enter');
    
    // Wait for streaming to finish (Stop button disappears)
    // This ensures backend has finished processing and saving
    await expect(page.getByTitle('Stop generation (Esc)')).toBeVisible({ timeout: 5000 }); // Wait for start
    await expect(page.getByTitle('Stop generation (Esc)')).not.toBeVisible({ timeout: 30000 }); // Wait for end
    
    // Reload
    await page.reload();
    
    // Check history (wait for loading)
    await expect(page.getByText('Persist me')).toBeVisible({ timeout: 10000 });
  });
});
