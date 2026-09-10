import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { BrowserLab, parseOrigins } from './browser.mjs';

test('origin config fails closed', () => {
  for (const u of ['https://example.com', 'http://localhost:80', 'file:///etc/passwd', 'http://127.0.0.1/foo', 'http://x:y@127.0.0.1', 'http://127.0.0.1?secret']) assert.throws(() => parseOrigins(u));
  assert.equal(parseOrigins().size, 0);
});
test('partial initialization rolls back owned browser and fails closed', async () => {
  const lab = new BrowserLab();
  let closed = false;
  lab.start = async () => {
    lab.browser = { close: async () => { closed = true; } };
    throw new Error('fixture page initialization failure');
  };
  await assert.rejects(lab.execute({ action: 'observe' }), /initialization failure/);
  assert.equal(closed, true);
  await assert.rejects(lab.execute({ action: 'observe' }), /closed/);
});

test('real browser: screenshots, serialized observations, denial, input, cancellation', async () => {
  let deniedHits = 0;
  const denied = createServer((q, s) => { deniedHits++; s.end('wrong origin'); });
  await new Promise(r => denied.listen(0, '127.0.0.1', r));
  const deniedUrl = `http://127.0.0.1:${denied.address().port}`;
  const server = createServer((q, s) => {
    if (q.url === '/redirect') { s.writeHead(302, { Location: deniedUrl }); s.end(); return; }
    s.setHeader('Content-Type', 'text/html');
    s.end(`<input style="position:absolute;left:20px;top:20px;width:300px;height:40px"><img src="${deniedUrl}/image"><h1>Browser fixture</h1>`);
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const lab = new BrowserLab({ origins: parseOrigins(origin), output: await mkdtemp(join(tmpdir(), 'nimbus-browser-test-')) });
  try {
    await assert.rejects(lab.execute({ action: 'open', url: deniedUrl }), /authorized/);
    const first = await lab.execute({ action: 'open', url: origin });
    assert.equal(first.png.readUInt32BE(16), 1440); assert.equal(first.png.readUInt32BE(20), 900);
    const actions = await Promise.allSettled([1, 2].map(() => lab.execute({ action: 'click', x: 40, y: 40, observation: first.observation })));
    assert.equal(actions[0].status, 'fulfilled'); assert.equal(actions[1].status, 'rejected');
    const typed = await lab.execute({ action: 'type', text: 'Nimbus', observation: actions[0].value.observation });
    assert.equal(await lab.page.locator('input').inputValue(), 'Nimbus'); // white-box assertion, not a model test
    await assert.rejects(lab.execute({ action: 'click', x: -1, y: 20, observation: typed.observation }), /Coordinates/);
    await assert.rejects(lab.execute({ action: 'open', url: `${origin}/redirect` }));
    assert.equal(deniedHits, 0);
    const controller = new AbortController(); controller.abort();
    await assert.rejects(lab.execute({ action: 'observe' }, controller.signal));
    await lab.close(); await lab.close();
    await assert.rejects(lab.execute({ action: 'observe' }), /closed/);
  } finally { await lab.close(); await Promise.all([new Promise(r => server.close(r)), new Promise(r => denied.close(r))]); }
});
