/**
 * Pi-AI HTTP Server
 * 
 * 把 pi-ai 包装成独立的 HTTP 服务，提供统一的 LLM API
 * 
 * 启动: npx tsx bridge/pi-ai-server.ts
 * 
 * API:
 *   POST /v1/chat/completions  - OpenAI 兼容格式
 *   POST /v1/stream            - SSE 流式 API
 *   GET  /v1/models            - 列出可用模型
 *   GET  /health               - 健康检查
 */

import { createServer, IncomingMessage, ServerResponse } from "http";
import {
  getModel,
  stream,
  complete,
  getModels,
  getProviders,
  getOAuthApiKey,
  type Context,
  type Model,
} from "@mariozechner/pi-ai";
import * as fs from "fs";
import * as path from "path";
import * as os from "os";

const PORT = process.env.PI_AI_PORT || 3031;
const AUTH_PATH = path.join(os.homedir(), ".pi", "agent", "auth.json");

// ============================================================================
// Auth Management
// ============================================================================

function loadAuth(): Record<string, any> {
  try {
    return JSON.parse(fs.readFileSync(AUTH_PATH, "utf-8"));
  } catch {
    return {};
  }
}

function saveAuth(auth: Record<string, any>) {
  fs.writeFileSync(AUTH_PATH, JSON.stringify(auth, null, 2));
}

// OAuth providers that need token refresh
const OAUTH_PROVIDERS = ["anthropic", "openai-codex", "github-copilot", "google-gemini-cli", "google-antigravity"];

async function getApiKey(provider: string): Promise<string | null> {
  const auth = loadAuth();

  // Check if it's an OAuth provider
  if (OAUTH_PROVIDERS.includes(provider)) {
    const result = await getOAuthApiKey(provider as any, auth);
    if (result) {
      // Save refreshed credentials
      auth[provider] = { type: "oauth", ...result.newCredentials };
      saveAuth(auth);
      return result.apiKey;
    }
    return null;
  }

  // Check for API key in auth.json
  if (auth[provider]?.apiKey) {
    return auth[provider].apiKey;
  }

  // Check environment variables
  const envKey = process.env[`${provider.toUpperCase().replace(/-/g, "_")}_API_KEY`];
  return envKey || null;
}

// ============================================================================
// Types
// ============================================================================

interface ChatRequest {
  model?: string;           // "anthropic/claude-sonnet-4-20250514" or just model id
  provider?: string;        // "anthropic"
  messages: Array<{
    role: "system" | "user" | "assistant" | "tool";
    content: string | Array<{ type: string; text?: string;[key: string]: any }>;
    tool_call_id?: string;
    name?: string;
  }>;
  tools?: Array<{
    type: "function";
    function: {
      name: string;
      description: string;
      parameters: any;
    };
  }>;
  stream?: boolean;
  max_tokens?: number;
  temperature?: number;
}

// ============================================================================
// Helpers
// ============================================================================

function parseModelString(modelStr: string): { provider: string; modelId: string } | null {
  // Format: "provider/model" or just "model"
  if (modelStr.includes("/")) {
    const [provider, modelId] = modelStr.split("/", 2);
    return { provider, modelId };
  }

  // Try to find model in all providers
  for (const p of getProviders()) {
    const providerId = typeof p === 'string' ? p : (p as any).id;
    if (!providerId) continue;
    try {
      const models = getModels(providerId);
      const found = models.find(m => m.id === modelStr);
      if (found) {
        return { provider: providerId, modelId: modelStr };
      }
    } catch (e) {
      // Skip providers that fail
    }
  }

  return null;
}

function convertToContext(req: ChatRequest): Context {
  const context: Context = {
    messages: [],
  };

  // Extract system prompt
  const systemMsg = req.messages.find(m => m.role === "system");
  if (systemMsg && typeof systemMsg.content === "string") {
    context.systemPrompt = systemMsg.content;
  }

  // Convert messages
  for (const msg of req.messages) {
    if (msg.role === "system") continue;

    if (msg.role === "user") {
      context.messages.push({
        role: "user",
        content: typeof msg.content === "string" ? msg.content : msg.content,
        timestamp: Date.now(),
      });
    } else if (msg.role === "assistant") {
      context.messages.push({
        role: "assistant",
        content: typeof msg.content === "string"
          ? [{ type: "text", text: msg.content }]
          : msg.content.map(c => {
            if (c.type === "text") return { type: "text" as const, text: c.text || "" };
            if (c.type === "tool_use" || c.type === "toolCall") {
              return {
                type: "toolCall" as const,
                id: c.id,
                name: c.name,
                arguments: c.input || c.arguments || {},
              };
            }
            return c;
          }),
        model: req.model || "",
        provider: req.provider || "",
        api: "messages",
        usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
        stopReason: "stop",
        timestamp: Date.now(),
      });
    } else if (msg.role === "tool") {
      context.messages.push({
        role: "toolResult",
        toolCallId: msg.tool_call_id || "",
        toolName: msg.name || "",
        content: [{ type: "text", text: typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content) }],
        isError: false,
        timestamp: Date.now(),
      });
    }
  }

  // Convert tools
  if (req.tools && req.tools.length > 0) {
    context.tools = req.tools.map(t => ({
      name: t.function.name,
      description: t.function.description,
      parameters: t.function.parameters,
    }));
  }

  return context;
}

function formatSSE(event: string, data: any): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

// ============================================================================
// Handlers
// ============================================================================

async function handleComplete(req: ChatRequest, res: ServerResponse) {
  try {
    // Parse model
    const modelInfo = req.model
      ? parseModelString(req.model)
      : { provider: req.provider || "anthropic", modelId: "claude-sonnet-4-20250514" };

    if (!modelInfo) {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: `Unknown model: ${req.model}` }));
      return;
    }

    const model = getModel(modelInfo.provider as any, modelInfo.modelId as any);
    if (!model) {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: `Model not found: ${modelInfo.provider}/${modelInfo.modelId}` }));
      return;
    }

    // Get API key
    const apiKey = await getApiKey(modelInfo.provider);
    if (!apiKey) {
      res.writeHead(401, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        error: `No API key for provider: ${modelInfo.provider}. Run: npx @mariozechner/pi-ai login ${modelInfo.provider}`
      }));
      return;
    }

    const context = convertToContext(req);

    if (req.stream) {
      // Streaming response
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
      });

      const s = stream(model, context, { apiKey });
      let responseId = `chatcmpl-${Date.now()}`;

      for await (const event of s) {
        switch (event.type) {
          case "text_delta":
            res.write(formatSSE("delta", {
              id: responseId,
              object: "chat.completion.chunk",
              choices: [{
                index: 0,
                delta: { content: event.delta },
                finish_reason: null,
              }],
            }));
            break;

          case "toolcall_end":
            res.write(formatSSE("tool_call", {
              id: responseId,
              tool_call: {
                id: event.toolCall.id,
                type: "function",
                function: {
                  name: event.toolCall.name,
                  arguments: JSON.stringify(event.toolCall.arguments),
                },
              },
            }));
            break;

          case "done":
            res.write(formatSSE("done", {
              id: responseId,
              finish_reason: event.reason,
            }));
            break;

          case "error":
            res.write(formatSSE("error", { error: event.error }));
            break;
        }
      }

      const result = await s.result();
      res.write(formatSSE("result", {
        usage: result.usage,
        model: result.model,
      }));

      res.end();
    } else {
      // Non-streaming response
      const result = await complete(model, context, { apiKey });

      // Debug log: show what pi-ai returned
      console.log("[pi-ai-server] Raw result from pi-ai:");
      console.log("  stopReason:", result.stopReason);
      console.log("  content types:", result.content.map(c => c.type));
      console.log("  content preview:", JSON.stringify(result.content.slice(0, 2)).substring(0, 500));

      // Convert to OpenAI format
      const response = {
        id: `chatcmpl-${Date.now()}`,
        object: "chat.completion",
        created: Math.floor(Date.now() / 1000),
        model: result.model,
        choices: [{
          index: 0,
          message: {
            role: "assistant",
            content: result.content
              .filter(c => c.type === "text")
              .map(c => (c as any).text)
              .join(""),
            tool_calls: result.content
              .filter(c => c.type === "toolCall")
              .map(c => ({
                id: (c as any).id,
                type: "function",
                function: {
                  name: (c as any).name,
                  arguments: JSON.stringify((c as any).arguments),
                },
              })),
          },
          finish_reason: result.stopReason,
        }],
        usage: {
          prompt_tokens: result.usage.input,
          completion_tokens: result.usage.output,
          total_tokens: result.usage.input + result.usage.output,
        },
      };

      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(response));
    }
  } catch (error: any) {
    console.error("[pi-ai-server] Error:", error);
    res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: error.message }));
  }
}

async function handleModels(res: ServerResponse) {
  const models: Array<{ id: string; provider: string; name: string }> = [];

  const providers = getProviders();
  for (const p of providers) {
    // Handle both string IDs and object providers (for forward compatibility/different versions)
    const providerId = typeof p === 'string' ? p : (p as any).id;
    if (!providerId) continue;

    // Only show models for providers with configured API keys
    const apiKey = await getApiKey(providerId);
    if (!apiKey) {
      continue;
    }

    try {
      const providerModels = getModels(providerId);
      for (const model of providerModels) {
        models.push({
          id: `${providerId}/${model.id}`,
          provider: providerId,
          name: model.name || model.id,
        });
      }
    } catch (e) {
      console.error(`[pi-ai-server] Failed to get models for ${providerId}:`, e);
    }
  }

  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ data: models }));
}

async function handleHealth(res: ServerResponse) {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ status: "ok", timestamp: Date.now() }));
}

// ============================================================================
// Server
// ============================================================================

async function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", chunk => body += chunk);
    req.on("end", () => resolve(body));
    req.on("error", reject);
  });
}

const server = createServer(async (req, res) => {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");

  if (req.method === "OPTIONS") {
    res.writeHead(204);
    res.end();
    return;
  }

  const url = req.url || "/";

  try {
    // Health check
    if (url === "/health" && req.method === "GET") {
      await handleHealth(res);
      return;
    }

    // List models
    if (url === "/v1/models" && req.method === "GET") {
      await handleModels(res);
      return;
    }

    // Chat completions (OpenAI compatible)
    if ((url === "/v1/chat/completions" || url === "/v1/complete" || url === "/v1/stream") && req.method === "POST") {
      const body = await readBody(req);
      const data: ChatRequest = JSON.parse(body);

      // /v1/stream forces streaming
      if (url === "/v1/stream") {
        data.stream = true;
      }

      await handleComplete(data, res);
      return;
    }

    // 404
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "Not found" }));
  } catch (error: any) {
    console.error("[pi-ai-server] Error:", error);
    res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: error.message }));
  }
});

server.listen(PORT, () => {
  console.log(`[pi-ai-server] Pi-AI HTTP Server started on port ${PORT}`);
  console.log(`[pi-ai-server] Auth file: ${AUTH_PATH}`);
  console.log(`[pi-ai-server] Endpoints:`);
  console.log(`  POST /v1/chat/completions  - OpenAI compatible`);
  console.log(`  POST /v1/stream            - SSE streaming`);
  console.log(`  GET  /v1/models            - List models`);
  console.log(`  GET  /health               - Health check`);
});
