import { test, expect } from '@playwright/test';
test('troubleshoot cross origin', async ({ page }) => {
  page.on('console', msg => console.log(`[Browser] ${msg.text()}`));
  await page.goto('http://127.0.0.1:3000/');
  await page.waitForSelector('textarea[data-testid="chat-input"]');
  await page.locator('textarea[data-testid="chat-input"]').fill('hello');
  await page.locator('textarea[data-testid="chat-input"]').press('Enter');
  await page.waitForTimeout(5000);
});
