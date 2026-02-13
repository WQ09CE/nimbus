import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI
    ? [['json', { outputFile: 'test-results/results.json' }], ['html']]
    : 'html',
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'tier1',
      testDir: './e2e/tier1',
      use: { ...devices['Desktop Chrome'] },
      timeout: 15000,
    },
    {
      name: 'tier2-setup',
      testDir: './e2e/tier2',
      testMatch: 'backend-health.setup.ts',
      timeout: 60000,
    },
    {
      name: 'tier2',
      testDir: './e2e/tier2',
      testMatch: '*.spec.ts',
      use: { ...devices['Desktop Chrome'] },
      timeout: 60000,
      dependencies: ['tier2-setup'],
    },
    {
      name: 'tier3',
      testDir: './e2e/tier3',
      testMatch: '*.spec.ts',
      use: { ...devices['Desktop Chrome'] },
      timeout: 120_000,
      dependencies: ['tier2-setup'],
    },
    {
      name: 'legacy',
      testDir: './e2e',
      testMatch: 'chat.spec.ts',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
});
