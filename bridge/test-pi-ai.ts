import { getModel, complete, getOAuthApiKey, type Context } from '@mariozechner/pi-ai';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

// 从 ~/.pi/agent/auth.json 读取认证信息
function loadAuth(): Record<string, any> {
  const authPath = path.join(os.homedir(), '.pi', 'agent', 'auth.json');
  try {
    return JSON.parse(fs.readFileSync(authPath, 'utf-8'));
  } catch {
    return {};
  }
}

function saveAuth(auth: Record<string, any>) {
  const authPath = path.join(os.homedir(), '.pi', 'agent', 'auth.json');
  fs.writeFileSync(authPath, JSON.stringify(auth, null, 2));
}

async function main() {
  // 测试获取模型
  const model = getModel('anthropic', 'claude-sonnet-4-20250514');
  console.log('Model:', model ? model.name : 'NOT FOUND');

  if (!model) return;

  // 加载认证
  const auth = loadAuth();
  console.log('Auth providers:', Object.keys(auth));

  // 获取 API key (自动刷新 token)
  const result = await getOAuthApiKey('anthropic', auth);
  if (!result) {
    console.log('Error: Not logged in to Anthropic');
    console.log('Run: npx @mariozechner/pi-ai login anthropic');
    return;
  }

  console.log('Got API key, expires:', new Date(result.newCredentials.expires).toLocaleString());

  // 保存刷新后的凭证
  auth['anthropic'] = { type: 'oauth', ...result.newCredentials };
  saveAuth(auth);

  // 测试调用
  const context: Context = {
    messages: [{ role: 'user', content: 'Say hi in 3 words', timestamp: Date.now() }]
  };

  console.log('Calling Anthropic...');
  try {
    const response = await complete(model, context, { apiKey: result.apiKey });
    console.log('Result:', response.content);
    console.log('Usage:', response.usage);
  } catch (e: any) {
    console.log('Error:', e.message);
  }
}

main();
