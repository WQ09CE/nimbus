/**
 * Tier 2 Setup: Nimbus Backend Health Check
 *
 * This Playwright setup file verifies that the Nimbus backend is running
 * and healthy before any tier2 integration tests execute.
 *
 * It runs as a project dependency (tier2-setup) configured in
 * playwright.config.ts, so tier2 tests will not start until this passes.
 *
 * Start the backend before running tier2 tests:
 *
 *   NIMBUS_LLM=mock nimbus serve --port 4096
 */

import { test as setup, expect } from '@playwright/test';

const BACKEND_URL = process.env.NIMBUS_BACKEND_URL || 'http://localhost:4096';
const HEALTH_ENDPOINT = `${BACKEND_URL}/api/v1/health`;
const MAX_RETRIES = 10;
const RETRY_INTERVAL_MS = 2000;

setup('verify nimbus backend is running', async () => {
  setup.setTimeout(30_000);

  let lastError: string = '';

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 3000);

      const response = await fetch(HEALTH_ENDPOINT, {
        signal: controller.signal,
      });
      clearTimeout(timeout);

      if (response.ok) {
        const body = await response.json();
        console.log(
          `[tier2-setup] Backend healthy (attempt ${attempt}/${MAX_RETRIES}):`,
          JSON.stringify(body),
        );
        return; // Success -- tier2 tests can proceed
      }

      lastError = `HTTP ${response.status}`;
    } catch (err: unknown) {
      lastError = err instanceof Error ? err.message : String(err);
    }

    console.log(
      `[tier2-setup] Backend not ready (attempt ${attempt}/${MAX_RETRIES}): ${lastError}`,
    );

    if (attempt < MAX_RETRIES) {
      await new Promise((resolve) => setTimeout(resolve, RETRY_INTERVAL_MS));
    }
  }

  // All retries exhausted -- fail with clear instructions
  throw new Error(
    `\n\n` +
    `  Nimbus backend is not running at ${BACKEND_URL}\n` +
    `  Last error: ${lastError}\n\n` +
    `  Tier 2 tests require a real Nimbus backend with MockLLM.\n` +
    `  Please start it before running tests:\n\n` +
    `    NIMBUS_LLM=mock nimbus serve --port 4096\n\n` +
    `  Then run:\n\n` +
    `    npm run test:e2e:integration\n`,
  );
});
