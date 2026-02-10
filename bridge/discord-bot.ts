#!/usr/bin/env npx tsx
/**
 * Nimbus Discord Bot
 * 
 * 连接 Discord 与 Nimbus API
 * 使用 discord.js + Nimbus Chat API
 */

import { Client, GatewayIntentBits, Message, Events } from 'discord.js';
import fetch from 'node-fetch';

// ============================================================================
// 配置
// ============================================================================

const DISCORD_TOKEN = process.env.DISCORD_BOT_TOKEN || '';
const NIMBUS_API = process.env.NIMBUS_API_URL || 'http://localhost:4096';
const BOT_PREFIX = process.env.BOT_PREFIX || '!nimbus';

if (!DISCORD_TOKEN) {
  console.error('❌ DISCORD_BOT_TOKEN not set');
  process.exit(1);
}

// ============================================================================
// Discord Client
// ============================================================================

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.DirectMessages,
  ],
});

// ============================================================================
// Session 管理
// ============================================================================

// 每个 Discord 频道/用户对应一个 Nimbus session
const sessions = new Map<string, string>(); // channelId -> sessionId

async function getOrCreateSession(channelId: string): Promise<string> {
  if (sessions.has(channelId)) {
    return sessions.get(channelId)!;
  }

  // 创建新 session
  const res = await fetch(`${NIMBUS_API}/v2/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      mode: 'chat',
      context: {
        platform: 'discord',
        channel_id: channelId,
      },
    }),
  });

  if (!res.ok) {
    throw new Error(`Failed to create session: ${res.statusText}`);
  }

  const data = await res.json();
  const sessionId = data.session_id;
  sessions.set(channelId, sessionId);

  console.log(`✅ Created session ${sessionId} for channel ${channelId}`);
  return sessionId;
}

// ============================================================================
// Nimbus API 交互
// ============================================================================

async function sendToNimbus(sessionId: string, message: string): Promise<string> {
  const res = await fetch(`${NIMBUS_API}/v2/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
    },
    body: JSON.stringify({ message }),
  });

  if (!res.ok) {
    throw new Error(`Nimbus API error: ${res.statusText}`);
  }

  // 解析 SSE 流
  let fullResponse = '';
  const body = res.body;
  if (!body) throw new Error('No response body');

  // 简单的 SSE 解析
  let buffer = '';
  for await (const chunk of body) {
    buffer += chunk.toString();
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6));
          if (data.type === 'message' && data.content) {
            fullResponse += data.content;
          } else if (data.type === 'done') {
            return fullResponse || '(No response)';
          }
        } catch (e) {
          // Skip invalid JSON
        }
      }
    }
  }

  return fullResponse || '(Empty response)';
}

// ============================================================================
// Discord 事件处理
// ============================================================================

client.on(Events.ClientReady, () => {
  console.log(`🤖 Discord bot logged in as ${client.user?.tag}`);
  console.log(`📡 Connected to Nimbus at ${NIMBUS_API}`);
  console.log(`💬 Command prefix: ${BOT_PREFIX}`);
});

client.on(Events.MessageCreate, async (message: Message) => {
  // 忽略自己的消息
  if (message.author.bot) return;

  // 检查命令前缀
  if (!message.content.startsWith(BOT_PREFIX)) return;

  const userMessage = message.content.slice(BOT_PREFIX.length).trim();
  if (!userMessage) {
    await message.reply('用法: `!nimbus <your question>`');
    return;
  }

  try {
    // 显示 typing indicator
    await message.channel.sendTyping();

    // 获取或创建 session
    const sessionId = await getOrCreateSession(message.channelId);

    // 发送到 Nimbus
    const response = await sendToNimbus(sessionId, userMessage);

    // 分块发送（Discord 限制 2000 字符）
    const chunks = splitMessage(response, 2000);
    for (const chunk of chunks) {
      await message.reply(chunk);
    }
  } catch (error) {
    console.error('❌ Error handling message:', error);
    await message.reply('抱歉，处理消息时出错了。');
  }
});

// ============================================================================
// 工具函数
// ============================================================================

function splitMessage(text: string, maxLen: number): string[] {
  if (text.length <= maxLen) return [text];

  const chunks: string[] = [];
  let current = '';

  for (const line of text.split('\n')) {
    if (current.length + line.length + 1 > maxLen) {
      chunks.push(current);
      current = line;
    } else {
      current += (current ? '\n' : '') + line;
    }
  }

  if (current) chunks.push(current);
  return chunks;
}

// ============================================================================
// 启动
// ============================================================================

client.login(DISCORD_TOKEN);

// 优雅退出
process.on('SIGINT', () => {
  console.log('\n👋 Shutting down...');
  client.destroy();
  process.exit(0);
});
