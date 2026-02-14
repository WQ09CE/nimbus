/**
 * Tier 3: Long Context Memory Retention Tests
 *
 * These tests verify that the LLM retains information across multiple
 * conversation turns. They plant unique facts early in the conversation,
 * send several unrelated "filler" turns, and then ask the LLM to recall
 * the planted information.
 *
 * Prerequisites:
 *   1. Start pi-ai server: npx tsx bridge/pi-ai-server.ts
 *   2. Start Nimbus backend with real model:
 *        NIMBUS_MODEL="google-antigravity/gemini-3-flash" \
 *          python -m uvicorn nimbus.server.app:create_app --factory --port 4096
 *   3. Start web-ui: cd web-ui && npm run dev
 *   4. Run: npx playwright test --project=tier3 e2e/tier3/memory-retention.spec.ts
 */

import { test, expect, type Page } from '@playwright/test';
import { NimbusPage } from '../helpers/nimbus-page';

// ---------------------------------------------------------------------------
// Timeout constants -- real LLMs are slower than mocks
// ---------------------------------------------------------------------------

/** Timeout for waiting for real LLM streaming to begin. */
const STREAMING_START_TIMEOUT = 30_000;

/** Timeout for waiting for real LLM streaming to complete. */
const STREAMING_END_TIMEOUT = 60_000;

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

/**
 * Send a message and wait for the complete assistant reply.
 *
 * 1. Sends the user message via nimbus helper.
 * 2. Waits for the user message bubble to appear (by counting existing ones).
 * 3. Waits for streaming start and end.
 * 4. Returns the text of the last assistant message.
 */
async function sendTurnAndWait(
  nimbus: NimbusPage,
  page: Page,
  message: string,
  streamStartTimeout = STREAMING_START_TIMEOUT,
  streamEndTimeout = STREAMING_END_TIMEOUT,
): Promise<string> {
  // Count existing user messages before sending
  const userMessagesBefore = await page.getByTestId('message-user').count();

  await nimbus.sendMessage(message);

  // Wait for our new user message to appear
  await expect(page.getByTestId('message-user').nth(userMessagesBefore)).toBeVisible({
    timeout: 10_000,
  });

  // Wait for streaming lifecycle
  await nimbus.waitForStreamingStart(streamStartTimeout);
  await nimbus.waitForStreamingEnd(streamEndTimeout);

  // Return the last assistant message
  const reply = await nimbus.getLastAssistantMessage();
  return reply;
}

// ---------------------------------------------------------------------------
// Test Suite
// ---------------------------------------------------------------------------

test.describe('Tier 3: Memory Retention', () => {
  let nimbus: NimbusPage;

  test.beforeEach(async ({ page }) => {
    // Forward browser console to test output for debugging
    page.on('console', (msg) => console.log(`[Browser] ${msg.text()}`));

    // Clear localStorage to ensure each test starts with a fresh session
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());

    nimbus = new NimbusPage(page);
    await nimbus.goto();

    // Wait for the welcome screen to appear, which confirms that
    // createNewSession() has completed (isLoading becomes false).
    await nimbus.expectWelcomeScreen();
  });

  // =========================================================================
  // Test 1: Short-term memory (5 turns)
  // =========================================================================

  test('should recall a secret code after 5 turns', async ({ page }) => {
    test.setTimeout(120_000);

    // --- Turn 1: Plant the secret ---
    const turn1 = await sendTurnAndWait(
      nimbus,
      page,
      'Remember this secret code: ALPHA-7742-OMEGA. I will ask you about it later.',
    );
    console.log('Turn 1 response:', turn1.substring(0, 100));
    expect(turn1.length).toBeGreaterThan(0);

    // --- Turns 2-4: Unrelated filler ---
    const fillerQuestions = [
      'What is 2+2?',
      'Name 3 colors.',
      'What is the capital of France?',
    ];

    for (let i = 0; i < fillerQuestions.length; i++) {
      const reply = await sendTurnAndWait(nimbus, page, fillerQuestions[i]);
      console.log(`Turn ${i + 2} response:`, reply.substring(0, 100));
      expect(reply.length).toBeGreaterThan(0);
    }

    // --- Turn 5: Ask for recall ---
    const recallReply = await sendTurnAndWait(
      nimbus,
      page,
      'What was the secret code I told you at the beginning?',
    );
    console.log('Turn 5 (recall) response:', recallReply.substring(0, 200));

    // Assert: response contains the secret code
    expect(recallReply.toUpperCase()).toContain('ALPHA-7742-OMEGA');
  });

  // =========================================================================
  // Test 2: Medium-term memory (10 turns)
  // =========================================================================

  test('should recall 3 items after 10 turns', async ({ page }) => {
    test.slow(); // Marks the test as expected to be slow (3x default timeout)
    test.setTimeout(300_000); // 5 minutes

    // --- Turn 1: Plant the items ---
    const turn1 = await sendTurnAndWait(
      nimbus,
      page,
      'Remember these 3 items: a red bicycle, a blue umbrella, and a green parrot. I will quiz you later.',
    );
    console.log('Turn 1 response:', turn1.substring(0, 100));
    expect(turn1.length).toBeGreaterThan(0);

    // --- Turns 2-9: Diverse filler ---
    const fillerQuestions = [
      'What is 15 * 3?',
      'Name the planets in our solar system.',
      'Write a python function that adds two numbers.',
      'If all dogs are animals and Rex is a dog, what can you conclude?',
      'What is the speed of light in meters per second?',
      'Explain what photosynthesis is in one sentence.',
      'What year did World War II end?',
      'Name 5 programming languages.',
    ];

    for (let i = 0; i < fillerQuestions.length; i++) {
      const reply = await sendTurnAndWait(nimbus, page, fillerQuestions[i]);
      console.log(`Turn ${i + 2} response:`, reply.substring(0, 100));
      expect(reply.length).toBeGreaterThan(0);
    }

    // --- Turn 10: Ask for recall ---
    const recallReply = await sendTurnAndWait(
      nimbus,
      page,
      'What were the 3 items I asked you to remember at the start of our conversation?',
    );
    console.log('Turn 10 (recall) response:', recallReply.substring(0, 300));

    // Assert: response contains at least 2 of the 3 planted items
    const replyLower = recallReply.toLowerCase();
    const matches = [
      replyLower.includes('bicycle'),
      replyLower.includes('umbrella'),
      replyLower.includes('parrot'),
    ].filter(Boolean).length;

    console.log(`Recalled ${matches}/3 items: bicycle=${replyLower.includes('bicycle')}, umbrella=${replyLower.includes('umbrella')}, parrot=${replyLower.includes('parrot')}`);
    expect(matches).toBeGreaterThanOrEqual(2);
  });

  // =========================================================================
  // Test 3: Cross-topic recall with distraction (7 turns)
  // =========================================================================

  test('should recall personal facts despite distracting topics', async ({ page }) => {
    test.setTimeout(180_000); // 3 minutes

    // --- Turn 1: Plant personal facts ---
    const turn1 = await sendTurnAndWait(
      nimbus,
      page,
      'My favorite number is 42 and my pet\'s name is Luna. Remember these.',
    );
    console.log('Turn 1 response:', turn1.substring(0, 100));
    expect(turn1.length).toBeGreaterThan(0);

    // --- Turns 2-5: Distractors involving numbers and pet names ---
    const distractors = [
      'What is 17 + 25?',
      'Tell me about the Apollo 11 mission.',
      'What are common cat names?',
      'Calculate 8 * 7.',
    ];

    for (let i = 0; i < distractors.length; i++) {
      const reply = await sendTurnAndWait(nimbus, page, distractors[i]);
      console.log(`Turn ${i + 2} response:`, reply.substring(0, 100));
      expect(reply.length).toBeGreaterThan(0);
    }

    // --- Turn 6: Ask for favorite number ---
    const numberReply = await sendTurnAndWait(
      nimbus,
      page,
      'What is my favorite number?',
    );
    console.log('Turn 6 (number recall) response:', numberReply.substring(0, 200));
    expect(numberReply).toContain('42');

    // --- Turn 7: Ask for pet name ---
    const petReply = await sendTurnAndWait(
      nimbus,
      page,
      'What is my pet\'s name?',
    );
    console.log('Turn 7 (pet recall) response:', petReply.substring(0, 200));
    expect(petReply.toLowerCase()).toContain('luna');
  });

  // =========================================================================
  // Test 4: Memory retention after compaction trigger
  // =========================================================================

  test('should recall a secret after compaction is triggered by tool-heavy turn', async ({ page }) => {
    test.setTimeout(180_000); // 3 minutes -- compaction adds LLM summarization overhead

    // --- Turn 1: Plant a secret fact ---
    const turn1 = await sendTurnAndWait(
      nimbus,
      page,
      'Remember: my password is BRAVO-9955-DELTA. I will ask you about it later.',
    );
    console.log('Turn 1 response:', turn1.substring(0, 100));
    expect(turn1.length).toBeGreaterThan(0);

    // --- Turn 2: Tool-heavy task that forces 3+ iterations → triggers compaction ---
    // With NIMBUS_MAX_ITERATIONS=3, reading multiple files causes
    // Read → Read → summarize = 3+ iterations, which triggers compaction.
    // The LLM should survive compaction and still respond.
    console.log('Turn 2: sending tool-heavy task to trigger compaction...');
    const turn2 = await sendTurnAndWait(
      nimbus,
      page,
      'Please read these files one by one and summarize each: /etc/shells, /etc/hosts',
      STREAMING_START_TIMEOUT,
      90_000, // longer streaming end timeout for tool-heavy turn with compaction
    );
    console.log('Turn 2 (tool-heavy) response:', turn2.substring(0, 300));
    expect(turn2.length).toBeGreaterThan(0); // confirms the tool task completed after compaction

    // --- Turn 3: Ask for the secret planted before compaction ---
    const recallReply = await sendTurnAndWait(
      nimbus,
      page,
      'What was the password I told you at the beginning?',
    );
    console.log('Turn 3 (recall after compaction) response:', recallReply.substring(0, 200));

    // Assert: response contains the secret password, proving memory survived compaction
    expect(recallReply.toUpperCase()).toContain('BRAVO-9955-DELTA');
  });
});
