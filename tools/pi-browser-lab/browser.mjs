import { chromium } from 'playwright-core';
import { mkdir, mkdtemp, writeFile, appendFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

export function parseOrigins(value = '') {
  return new Set(value.split(',').map(x => x.trim()).filter(Boolean).map(value => {
    const u = new URL(value);
    if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password ||
        !['127.0.0.1', '[::1]'].includes(u.hostname) || u.pathname !== '/' || u.search || u.hash) {
      throw new Error('Only exact loopback-IP HTTP(S) origins are supported; no credentials, paths, or wildcard');
    }
    return u.origin;
  }));
}

// Trusted LOCAL development sites only. Request routing is defense-in-depth,
// not an OS/network sandbox and not suitable for Telegram/untrusted remote code.
export class BrowserLab {
  constructor({ origins = new Set(), output = '.artifacts/browser', executable = '/usr/bin/chromium' } = {}) {
    this.origins = origins;
    this.output = resolve(output);
    this.executable = executable;
    this.queue = Promise.resolve();
    this.observation = null;
    this.sequence = 0;
    this.closed = false;
  }
  allowed(value) {
    try {
      const u = new URL(value);
      return !u.username && !u.password && ['http:', 'https:'].includes(u.protocol) && this.origins.has(u.origin);
    } catch { return false; }
  }
  async start() {
    await mkdir(this.output, { recursive: true, mode: 0o700 });
    this.runDir = await mkdtemp(join(this.output, 'run-'));
    this.browser = await chromium.launch({ executablePath: this.executable, headless: true,
      chromiumSandbox: true, timeout: 15000,
      env: { PATH: process.env.PATH ?? '/usr/bin', HOME: this.runDir, LANG: 'C.UTF-8' } });
    const context = await this.browser.newContext({ viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 1, serviceWorkers: 'block', acceptDownloads: false, permissions: [] });
    await context.route('**/*', async route => {
      if (!this.allowed(route.request().url())) return route.abort();
      // continue() can follow redirects without invoking the route again.
      // Fetch exactly one hop; this local-only MVP deliberately rejects redirects.
      try {
        const response = await route.fetch({ maxRedirects: 0, timeout: 10000 });
        if (response.status() >= 300 && response.status() < 400) return route.abort();
        await route.fulfill({ response });
      } catch { await route.abort().catch(() => {}); }
    });
    await context.routeWebSocket('**/*', ws => ws.close());
    this.page = await context.newPage();
    this.page.setDefaultTimeout(5000);
    context.on('page', p => { if (p !== this.page) void p.close().catch(() => {}); });
    this.page.on('dialog', d => void d.dismiss().catch(() => {}));
    this.page.on('download', d => void d.cancel().catch(() => {}));
  }
  async close() {
    this.closed = true;
    this.observation = null;
    // Also check after launch: cancellation may arrive while start() is pending.
    await this.browser?.close();
  }
  execute(params, signal) {
    const work = this.queue.then(async () => {
      signal?.throwIfAborted();
      if (params.action === 'close') { await this.close(); return { closed: true }; }
      if (this.closed) throw new Error('Browser closed; start a new Pi session to reset');
      if (!['open', 'observe', 'click', 'type', 'key', 'scroll'].includes(params.action)) throw new Error('Unknown action');
      if (params.action === 'open' && !this.allowed(params.url)) throw new Error('Origin not authorized');
      if (!['open', 'observe'].includes(params.action)) {
        if (!this.observation || params.observation !== this.observation || Date.now() - this.observedAt > 60000) {
          throw new Error('Missing/stale observation. Observe again before acting.');
        }
      }
      const onAbort = () => void this.close().catch(() => {});
      const timer = setTimeout(onAbort, 25000);
      signal?.addEventListener('abort', onAbort, { once: true });
      try {
        if (!this.browser) {
          try { await this.start(); }
          catch (error) { await this.close().catch(() => {}); throw error; }
        }
        if (this.closed || signal?.aborted) { await this.close(); throw new Error('Browser cancelled'); }
        if (++this.sequence > 200) throw new Error('Session action budget exhausted');
        this.observation = null; // Even errors invalidate coordinates from the last image.
        const { page } = this;
        const integer = (v, min, max) => Number.isInteger(v) && v >= min && v <= max;
        if (params.action === 'open') await page.goto(params.url, { waitUntil: 'domcontentloaded', timeout: 12000 });
        if (!this.allowed(page.url()) && page.url() !== 'about:blank') throw new Error('Page left approved origin');
        if (params.action === 'click') {
          if (!integer(params.x, 0, 1439) || !integer(params.y, 0, 899)) throw new Error('Coordinates outside viewport');
          await page.mouse.click(params.x, params.y);
        }
        if (params.action === 'type') {
          if (typeof params.text !== 'string' || params.text.length > 4000) throw new Error('Invalid text');
          await page.keyboard.insertText(params.text);
        }
        if (params.action === 'key') {
          if (!['Enter', 'Tab', 'Shift+Tab', 'Backspace', 'Escape', 'Control+A', 'ArrowDown', 'ArrowUp'].includes(params.key)) throw new Error('Unsupported key');
          await page.keyboard.press(params.key);
        }
        if (params.action === 'scroll') {
          if (!integer(params.dy, -900, 900)) throw new Error('Invalid scroll delta');
          await page.mouse.wheel(0, params.dy);
        }
        if (!this.allowed(page.url()) && page.url() !== 'about:blank') throw new Error('Page left approved origin');
        const png = await page.screenshot({ type: 'png', scale: 'css', timeout: 5000 });
        signal?.throwIfAborted();
        if (this.closed) throw new Error('Browser cancelled');
        this.observation = randomUUID();
        this.observedAt = Date.now();
        const screenshot = join(this.runDir, `${String(this.sequence).padStart(4, '0')}.png`);
        await writeFile(screenshot, png, { mode: 0o600 });
        const result = { observation: this.observation, sequence: this.sequence, width: 1440, height: 900, screenshot };
        // Never log typed text, query strings, browser console, or page bodies.
        await appendFile(join(this.runDir, 'actions.jsonl'), JSON.stringify({ ...result, action: params.action }) + '\n', { mode: 0o600 });
        return { ...result, png };
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', onAbort);
      }
    });
    this.queue = work.catch(() => {});
    return work;
  }
}
