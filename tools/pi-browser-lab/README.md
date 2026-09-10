# Pi browser computer use — local development only

A project-local Pi 0.85.1 extension backed by Playwright 1.63.0 and system Chromium.
The native `browser_lab` tool returns **real image content**, not just screenshot paths.
No Pi fork, external browser service, or second API-key model loop is needed.

## Start

```bash
cd ~/Projects/nimbus-telegram/tools/pi-browser-lab
npm ci --ignore-scripts --no-audit --no-fund
npm test

cd ../..
# Explicitly authorize just the actual development origin. No default origins.
PI_BROWSER_ORIGINS=http://127.0.0.1:3000 \
PI_BROWSER_IMAGE_DETAIL=original \
pi --no-extensions -e ./tools/pi-browser-lab/index.ts
```

Alternatively start Pi normally in this trusted worktree: `.pi/extensions/browser-lab.ts`
auto-loads the same extension. Do not use both paths together. Project trust is still
Pi's normal trust decision; no global settings or trust entries were changed.
For other projects, use the absolute `-e` path. Set origins **before** starting Pi.

- `open(url)` / `observe` → 1440×900 screenshot, unique observation ID.
- `click`, `type`, `key`, `scroll` require that exact latest observation ID.
- Actions are serialized; a second mutation using the old observation is rejected.
- Images and coordinates are CSS pixels, DPR=1; below Pi's 2000-pixel resize limit.
- Every mutation returns another screenshot. Observation IDs expire after 60 seconds.
- `close` is terminal. `/new` or restarting Pi creates a fresh tool instance.
- Session shutdown/reload/switch closes the owned browser. Tree navigation closes it
  rather than pretending browser state rewinds; start a new session afterward.
- Deadline: 25 seconds per action, 200 operations per session. No browser starts at
  extension-import time. Cancellation closes the browser, including startup races.
- Evidence goes to `.artifacts/browser/run-*`, or `PI_BROWSER_OUTPUT`; directory
  permissions 0700, files 0600. Screenshots can contain application data. Delete
  unwanted owned run directories manually; there is no automatic retention yet.
- `PI_BROWSER_IMAGE_DETAIL=original` only changes this tool's images on
  `openai-codex/gpt-6-astra`. Default remains Pi's normal `auto` for other models.

## Tested

```bash
# Uses the current Pi Codex subscription; synthetic localhost UI, no accounts.
node tools/pi-browser-lab/smoke.mjs
```

The model sees only this tool. It reads a runtime-random canvas challenge from pixels,
clicks/types/verifies, and reads a random receipt. The host checks the actual UI outcome.
A native Pi + Astra run passed on 2026-09-10; details are in `chat-lab/ACCEPTANCE.md`.

## Deliberate boundaries

This is **developer UI verification**, not the Telegram worker sandbox.

- Only explicitly listed loopback **IP** HTTP(S) origins; no hostname/wildcard, private
  LAN target, credentials in URL, CDP attach, personal browser or stored profile.
- No eval/DOM extraction, uploads, downloads, popup windows, permission grants or
  WebSocket. Redirects are rejected in this first release. Playwright's `continue()`
  can follow redirects without re-running the first URL check, so requests use a
  single-hop fetch with `maxRedirects:0` before fulfilling the response.
- No clipboard, camera, microphone or real desktop input.
- Browser environment does not inherit bot/DB/model secrets; HOME points at its own
  private run directory. Chromium's normal sandbox is enabled.
- Routing is **not an OS/network sandbox**. Browser background traffic, WebRTC and
  hostile browser exploits are not covered. Do not load hostile pages or deploy this
  as an internet-facing bot capability. WebRTC/audio/video need a different acceptance
  setup; do not claim such flows passed using this restricted browser.
- An observation ID prevents accidental reuse, not arbitrary UI animation after capture.
  Re-observe when state is uncertain, and verify outcomes instead of assuming a click worked.
- No arbitrary persistent JS runtime yet. This thin screenshot/control adapter is the
  tested first increment; extend toward code execution only if real workflows need it.
