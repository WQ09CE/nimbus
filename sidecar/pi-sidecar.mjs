// Minimal OpenAI-compatible sidecar over pi-ai → GPT-5.5 on the ChatGPT/Codex
// subscription. nimbus points its LiteLLM channel (base_url) here.
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import { getModel, stream, complete } from "@earendil-works/pi-ai";

const PORT = Number(process.env.PI_SIDECAR_PORT || 8799);
const AUTH_PATH = os.homedir() + "/.pi/agent/auth.json";

function loadToken() {
  const auth = JSON.parse(fs.readFileSync(AUTH_PATH, "utf8"));
  const cx = auth["openai-codex"];
  if (!cx || !cx.access) throw new Error("no openai-codex token in " + AUTH_PATH);
  if (cx.expires && cx.expires < Date.now()) throw new Error("openai-codex token expired — re-run pi login");
  return cx.access;
}

const modelId = (m) => { let id = m || "gpt-5.5"; if (id.includes("/")) id = id.split("/").pop(); return id; };
const textOfParts = (parts) =>
  (parts || []).map((p) => (typeof p === "string" ? p : p.type === "text" ? p.text : "")).join("");
const zeroUsage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };

function toContext(body) {
  let systemPrompt = "";
  const messages = [];
  for (const m of body.messages || []) {
    if (m.role === "system") { systemPrompt += (systemPrompt ? "\n" : "") + (m.content || ""); continue; }
    if (m.role === "user") {
      messages.push({ role: "user", content: typeof m.content === "string" ? m.content : textOfParts(m.content), timestamp: Date.now() });
    } else if (m.role === "assistant") {
      const content = [];
      if (m.content) content.push({ type: "text", text: m.content });
      for (const tc of m.tool_calls || []) {
        let args = {}; try { args = JSON.parse(tc.function?.arguments || "{}"); } catch {}
        content.push({ type: "toolCall", id: tc.id, name: tc.function?.name, arguments: args });
      }
      messages.push({ role: "assistant", content, api: "openai-codex-responses", provider: "openai-codex",
        model: modelId(body.model), usage: zeroUsage, stopReason: "stop", timestamp: Date.now() });
    } else if (m.role === "tool") {
      messages.push({ role: "toolResult", toolCallId: m.tool_call_id, toolName: m.name || "",
        content: [{ type: "text", text: typeof m.content === "string" ? m.content : JSON.stringify(m.content) }],
        isError: false, timestamp: Date.now() });
    }
  }
  const tools = (body.tools || []).map((t) => ({
    name: t.function.name, description: t.function.description || "",
    parameters: t.function.parameters || { type: "object", properties: {} },
  }));
  return { systemPrompt: systemPrompt || undefined, messages, tools: tools.length ? tools : undefined };
}

function assistantToOpenAI(r) {
  const msg = { role: "assistant", content: null }; const tcs = []; let text = "";
  for (const p of r.content) {
    if (p.type === "text") text += p.text;
    else if (p.type === "toolCall") tcs.push({ id: p.id, type: "function", function: { name: p.name, arguments: JSON.stringify(p.arguments) } });
  }
  if (text) msg.content = text; if (tcs.length) msg.tool_calls = tcs; return msg;
}

const server = http.createServer(async (req, res) => {
  if (req.method === "GET" && req.url.startsWith("/v1/models")) {
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ object: "list", data: ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"].map((id) => ({ id, object: "model" })) }));
  }
  if (req.method !== "POST" || !req.url.startsWith("/v1/chat/completions")) { res.writeHead(404); return res.end("not found"); }

  let raw = ""; for await (const c of req) raw += c;
  let body; try { body = JSON.parse(raw); } catch { res.writeHead(400); return res.end("bad json"); }

  let token, model, ctx;
  try { token = loadToken(); model = getModel("openai-codex", modelId(body.model)); ctx = toContext(body); }
  catch (e) { res.writeHead(500, { "Content-Type": "application/json" }); return res.end(JSON.stringify({ error: { message: e.message } })); }

  const opts = { apiKey: token, reasoningEffort: "none" };
  if (typeof body.temperature === "number") opts.temperature = body.temperature;
  const created = Math.floor(Date.now() / 1000), id = "chatcmpl-pi-" + created;

  if (body.stream === false) {
    try {
      const r = await complete(model, ctx, opts); const msg = assistantToOpenAI(r);
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ id, object: "chat.completion", created, model: r.model,
        choices: [{ index: 0, message: msg, finish_reason: msg.tool_calls ? "tool_calls" : "stop" }],
        usage: r.usage ? { prompt_tokens: r.usage.input, completion_tokens: r.usage.output, total_tokens: r.usage.totalTokens } : undefined }));
    } catch (e) { res.writeHead(500, { "Content-Type": "application/json" }); return res.end(JSON.stringify({ error: { message: e.message } })); }
  }

  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive" });
  const send = (delta, finish = null) => res.write("data: " + JSON.stringify({ id, object: "chat.completion.chunk", created, model: modelId(body.model), choices: [{ index: 0, delta, finish_reason: finish }] }) + "\n\n");
  let toolIdx = 0;
  try {
    for await (const ev of stream(model, ctx, opts)) {
      if (ev.type === "text_delta") send({ content: ev.delta });
      else if (ev.type === "toolcall_end") {
        const tc = ev.toolCall;
        send({ tool_calls: [{ index: toolIdx++, id: tc.id, type: "function", function: { name: tc.name, arguments: JSON.stringify(tc.arguments) } }] });
      }
    }
    send({}, toolIdx > 0 ? "tool_calls" : "stop");
    res.write("data: [DONE]\n\n");
  } catch (e) { send({ content: "\n[sidecar error: " + e.message + "]" }, "stop"); res.write("data: [DONE]\n\n"); }
  res.end();
});

// Bind all interfaces so a container can reach it via host.docker.internal.
server.listen(PORT, "0.0.0.0", () => console.log(`pi-sidecar on http://0.0.0.0:${PORT}/v1 (gpt-5.5 via openai-codex)`));
