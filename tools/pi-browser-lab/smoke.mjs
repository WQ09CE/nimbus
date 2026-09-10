import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, join } from 'node:path';
import assert from 'node:assert/strict';
import { makeFixture } from './fixture.mjs';
const root = fileURLToPath(new URL('../../', import.meta.url));
await mkdir(join(root, '.artifacts'), { recursive: true, mode: 0o700 });
const output = await mkdtemp(join(root, '.artifacts/browser-model-'));
let oracle;
const server = createServer((req, res) => {
  if (req.method === 'POST' && req.url === '/passed') {
    let body = ''; req.on('data', c => { body += c; if (body.length > 2048) req.destroy(); });
    req.on('end', () => { try { oracle = JSON.parse(body); res.end('ok'); } catch { res.writeHead(400).end(); } });
    return;
  }
  let html = makeFixture().replace("connect-src 'none'", "connect-src 'self'");
  html += `<script>const watcher=setInterval(()=>{if(window.__labResult.success){clearInterval(watcher);fetch('/passed',{method:'POST',body:JSON.stringify(window.__labResult)})}},50)</script>`;
  res.setHeader('Content-Type', 'text/html'); res.end(html);
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const origin = `http://127.0.0.1:${server.address().port}`;
const child = spawn(process.env.PI_BIN ?? 'pi', [
  '--no-extensions', '--no-skills', '--no-prompt-templates', '--no-context-files', '--no-approve',
  '--no-builtin-tools', '--tools', 'browser_lab', '-e', resolve(root, 'tools/pi-browser-lab/index.ts'),
  '--provider', 'openai-codex', '--model', 'gpt-6-astra', '--thinking', 'low', '--no-session', '-p',
  `Use only browser_lab. Open ${origin}, complete the visual validation by finding the button, reading the random challenge, typing it and verifying. Use screenshots and current observation IDs. Return ONLY the exact final PASS receipt. Do not close the browser tool until you have read the receipt.`,
], { cwd: root, env: { ...process.env, PI_BROWSER_ORIGINS: origin, PI_BROWSER_OUTPUT: output,
  PI_BROWSER_IMAGE_DETAIL: 'original', PI_SKIP_VERSION_CHECK: '1', PI_TELEMETRY: '0' }, stdio: ['pipe', 'pipe', 'pipe'] });
child.stdin.end();
let stdout = '', stderr = '';
child.stdout.on('data', c => stdout += c); child.stderr.on('data', c => stderr += c);
let killTimer;
const deadline = setTimeout(() => {
  child.kill('SIGTERM');
  killTimer = setTimeout(() => child.kill('SIGKILL'), 10000);
}, 180000);
try {
  const code = await new Promise((r, j) => { child.on('error', j); child.on('close', r); });
  await writeFile(join(output, 'stdout.txt'), stdout); await writeFile(join(output, 'stderr.txt'), stderr);
  const result = { passed: code === 0 && oracle?.success === true && oracle?.receipt === stdout.trim() && oracle?.trustedClicks >= 2,
    code, answer: stdout.trim(), oracle, kind: 'native Pi browser_lab + Astra visual smoke, synthetic localhost fixture' };
  await writeFile(join(output, 'verification.json'), JSON.stringify(result, null, 2));
  console.log(output); console.log(JSON.stringify(result, null, 2)); assert.equal(result.passed, true);
} finally { clearTimeout(deadline); clearTimeout(killTimer); child.kill('SIGTERM'); server.closeAllConnections(); await new Promise(r => server.close(r)); }
