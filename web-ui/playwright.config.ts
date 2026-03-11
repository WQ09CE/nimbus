import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E Configuration
 *
 * Port layout:
 *   :4096 — nimbus backend (API server)
 *   :3000 — web-ui dev server (normal development)
 *   :3002 — web-ui dev server (E2E tests, isolated)
 *
 * The Nimbus web-ui has a security middleware that requires all requests
 * to be prefixed with /{NIMBUS_ACCESS_TOKEN}. For E2E tests we set
 * NIMBUS_ACCESS_TOKEN=e2e-test and inject the auth cookie via storageState.
 *
 * Tier 1 tests use route-level SSE mocking (no real backend needed).
 * Tier 2/3 tests require a running nimbus backend on :4096.
 */

const E2E_TOKEN = 'e2e-test';
const E2E_PORT = process.env.CI ? 3000 : 3002;
const BASE_URL = `http://localhost:${E2E_PORT}`;

export default defineConfig({
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI
    ? [['json', { outputFile: 'test-results/results.json' }], ['html']]
    : 'list',
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    // Set auth cookie so middleware allows API/SSE requests
    storageState: {
      cookies: [{
        name: 'nimbus_auth',
        value: E2E_TOKEN,
        domain: 'localhost',
        path: '/',
        httpOnly: true,
        sameSite: 'Strict' as const,
        secure: false,
        expires: -1,
      }],
      origins: [],
    },
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
    command: `NIMBUS_ACCESS_TOKEN=${E2E_TOKEN} PORT=${E2E_PORT} npm run dev`,
    url: `${BASE_URL}/${E2E_TOKEN}`,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
