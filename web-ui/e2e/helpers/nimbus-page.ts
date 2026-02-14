/**
 * Page Object Model for the Nimbus Chat UI.
 *
 * Provides a clean, reusable API for Playwright E2E tests to interact
 * with the Nimbus web interface. All element locators are based on
 * `data-testid` attributes defined in the source components.
 *
 * Usage:
 *   import { NimbusPage } from './helpers/nimbus-page';
 *
 *   test('example', async ({ page }) => {
 *     const nimbus = new NimbusPage(page);
 *     await nimbus.goto();
 *     await nimbus.sendMessage('Hello');
 *     const reply = await nimbus.getLastAssistantMessage();
 *   });
 */

import { type Page, type Locator, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

export interface ToolCardInfo {
  name: string;
  visible: boolean;
}

// ---------------------------------------------------------------------------
// Default timeouts
// ---------------------------------------------------------------------------

const DEFAULT_STREAMING_START_TIMEOUT = 5_000;
const DEFAULT_STREAMING_END_TIMEOUT = 30_000;

// ---------------------------------------------------------------------------
// NimbusPage
// ---------------------------------------------------------------------------

export class NimbusPage {
  readonly page: Page;

  // --- Locators (based on data-testid) ---
  readonly chatInput: Locator;
  readonly sendButton: Locator;
  readonly stopButton: Locator;
  readonly workingIndicator: Locator;
  readonly welcomeScreen: Locator;
  readonly errorBanner: Locator;
  readonly newChatButton: Locator;
  readonly sessionPanelTrigger: Locator;

  constructor(page: Page) {
    this.page = page;

    this.chatInput = page.getByTestId('chat-input');
    this.sendButton = page.getByTestId('send-button');
    this.stopButton = page.getByTestId('stop-button');
    this.workingIndicator = page.getByTestId('working-indicator');
    this.welcomeScreen = page.getByTestId('welcome-screen');
    this.errorBanner = page.getByTestId('error-banner');
    this.newChatButton = page.getByTestId('new-chat-button');
    this.sessionPanelTrigger = page.getByTestId('session-panel-trigger');
  }

  // =========================================================================
  // Navigation
  // =========================================================================

  /** Navigate to the app root and wait for the chat input to be ready. */
  async goto(): Promise<void> {
    await this.page.goto('/');
    await this.chatInput.waitFor({ state: 'visible' });
  }

  // =========================================================================
  // Chat Actions
  // =========================================================================

  /** Type a message into the chat input and press Enter to send it. */
  async sendMessage(text: string): Promise<void> {
    await this.chatInput.fill(text);
    await this.chatInput.press('Enter');
  }

  /** Type a message into the chat input without sending it. */
  async typeMessage(text: string): Promise<void> {
    await this.chatInput.fill(text);
  }

  // =========================================================================
  // Streaming
  // =========================================================================

  /** Wait for streaming to start (stop button becomes visible). */
  async waitForStreamingStart(timeout = DEFAULT_STREAMING_START_TIMEOUT): Promise<void> {
    await this.stopButton.waitFor({ state: 'visible', timeout });
  }

  /** Wait for streaming to end (stop button disappears). */
  async waitForStreamingEnd(timeout = DEFAULT_STREAMING_END_TIMEOUT): Promise<void> {
    await this.stopButton.waitFor({ state: 'hidden', timeout });
  }

  /** Check whether the UI is currently in streaming state. */
  async isStreaming(): Promise<boolean> {
    return this.stopButton.isVisible();
  }

  /** Click the stop button to interrupt the current stream. */
  async clickStop(): Promise<void> {
    await this.stopButton.click();
  }

  // =========================================================================
  // Message Reading
  // =========================================================================

  /** Get the text content of the last assistant message. */
  async getLastAssistantMessage(): Promise<string> {
    const messages = this.page.getByTestId('message-assistant');
    const last = messages.last();
    await last.waitFor({ state: 'visible' });
    return (await last.textContent()) ?? '';
  }

  /** Get the text content of all user messages. */
  async getUserMessages(): Promise<string[]> {
    const elements = this.page.getByTestId('message-user');
    const count = await elements.count();
    const texts: string[] = [];
    for (let i = 0; i < count; i++) {
      texts.push((await elements.nth(i).textContent()) ?? '');
    }
    return texts;
  }

  /** Get the text content of all assistant messages. */
  async getAssistantMessages(): Promise<string[]> {
    const elements = this.page.getByTestId('message-assistant');
    const count = await elements.count();
    const texts: string[] = [];
    for (let i = 0; i < count; i++) {
      texts.push((await elements.nth(i).textContent()) ?? '');
    }
    return texts;
  }

  /** Get all messages in order with their roles. */
  async getAllMessages(): Promise<ChatMessage[]> {
    // Gather all message elements by selecting both testid values
    const allElements = this.page.locator(
      '[data-testid="message-user"], [data-testid="message-assistant"]',
    );
    const count = await allElements.count();
    const result: ChatMessage[] = [];

    for (let i = 0; i < count; i++) {
      const el = allElements.nth(i);
      const testId = await el.getAttribute('data-testid');
      const role = testId === 'message-user' ? 'user' : 'assistant';
      const text = (await el.textContent()) ?? '';
      result.push({ role, text });
    }

    return result;
  }

  // =========================================================================
  // Tool Cards
  // =========================================================================

  /** Get info about all visible tool cards. */
  async getToolCards(): Promise<ToolCardInfo[]> {
    const cards = this.page.getByTestId('tool-card');
    const count = await cards.count();
    const result: ToolCardInfo[] = [];
    for (let i = 0; i < count; i++) {
      const card = cards.nth(i);
      const text = (await card.textContent()) ?? '';
      // The tool name is inside a <span> with font-mono class in the card header.
      // We extract the first recognisable word from the text content.
      const name = text.split('/')[0]?.trim() ?? text.trim();
      result.push({
        name,
        visible: await card.isVisible(),
      });
    }
    return result;
  }

  /** Find a specific tool card by its tool name. */
  async getToolCardByName(name: string): Promise<Locator> {
    return this.page.getByTestId('tool-card').filter({ hasText: name });
  }

  // =========================================================================
  // Session
  // =========================================================================

  /** Read the current session ID from localStorage. */
  async getSessionId(): Promise<string | null> {
    return this.page.evaluate(() => localStorage.getItem('nimbus_session_id'));
  }

  /** Open the session history panel. */
  async openSessionPanel(): Promise<void> {
    await this.sessionPanelTrigger.click();
  }

  /** Click the "New Chat" button to start a fresh session. */
  async clickNewChat(): Promise<void> {
    await this.newChatButton.click();
  }

  // =========================================================================
  // Injection
  // =========================================================================

  /**
   * Inject a message while streaming is in progress.
   * Functionally identical to sendMessage -- the backend handles
   * in-flight injection via the same input mechanism.
   */
  async injectMessage(text: string): Promise<void> {
    await this.sendMessage(text);
  }

  // =========================================================================
  // Working Indicator
  // =========================================================================

  /** Get the text displayed in the working indicator, or null if hidden. */
  async getWorkingIndicatorText(): Promise<string | null> {
    const visible = await this.workingIndicator.isVisible();
    if (!visible) return null;
    return (await this.workingIndicator.textContent()) ?? null;
  }

  // =========================================================================
  // Assertions (convenience)
  // =========================================================================

  /** Assert that the welcome screen is visible. */
  async expectWelcomeScreen(): Promise<void> {
    await expect(this.welcomeScreen).toBeVisible();
  }

  /** Assert that the welcome screen is NOT visible. */
  async expectNoWelcomeScreen(): Promise<void> {
    await expect(this.welcomeScreen).not.toBeVisible();
  }

  /** Assert that the error banner is visible, optionally containing a message. */
  async expectError(message?: string): Promise<void> {
    await expect(this.errorBanner).toBeVisible();
    if (message) {
      await expect(this.errorBanner).toContainText(message);
    }
  }

  /** Assert the total number of messages (user + assistant) matches count. */
  async expectMessageCount(count: number): Promise<void> {
    const allMessages = this.page.locator(
      '[data-testid="message-user"], [data-testid="message-assistant"]',
    );
    await expect(allMessages).toHaveCount(count);
  }
}
