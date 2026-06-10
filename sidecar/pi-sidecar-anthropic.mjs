// OpenAI-compatible sidecar over pi-ai with pi-style "provider/model" routing.
// Request models route by prefix — "anthropic/claude-sonnet-4-6" and
// "openai-codex/gpt-5.5" select provider + model exactly like pi; bare
// "claude-*" names imply anthropic; anything else falls back to
// PI_SIDECAR_MODEL. Built for hosts where only some provider backends are
// reachable (the 41 dev box whitelists api.anthropic.com but firewalls the
// ChatGPT/OpenAI domains — codex routes work when this runs on the Mac).
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import crypto from "node:crypto";
import { getModel, stream, complete } from "@earendil-works/pi-ai";
// package "exports" hides the oauth subpaths; load them by file path.
import { refreshAnthropicToken } from "./node_modules/@earendil-works/pi-ai/dist/utils/oauth/anthropic.js";

const PORT = Number(process.env.PI_SIDECAR_PORT || 8798);
const HOST = process.env.PI_SIDECAR_HOST || "127.0.0.1";
const TOKEN = process.env.PI_SIDECAR_TOKEN || "";
// Fallback for model names without a recognizable provider (e.g. "gpt-5.4"
// coming from generate's gateway-era defaults). pi "provider/model" format.
const FALLBACK = process.env.PI_SIDECAR_MODEL || "anthropic/claude-sonnet-4-6";
if (HOST !== "127.0.0.1" && HOST !== "localhost" && !TOKEN) {
  console.error("[pi-sidecar] REFUSING to bind " + HOST + " without PI_SIDECAR_TOKEN.");
  process.exit(1);
}
const AUTH_PATH = os.homedir() + "/.pi/agent/auth.json";
const REFRESH_BUFFER_MS = 5 * 60 * 1000;

function authOk(req) {
  if (!TOKEN) return true;
  const got = (req.headers["authorization"] || "").replace(/^Bearer\s+/i, "");
  const a = crypto.createHash("sha256").update(got).digest();
  const b = crypto.createHash("sha256").update(TOKEN).digest();
  return crypto.timingSafeEqual(a, b);
}

function readAuth() { return JSON.parse(fs.readFileSync(AUTH_PATH, "utf8")); }

function writeAuthEntry(provider, entry) {
  let full = {};
  try { full = readAuth(); } catch {}
  full[provider] = entry;
  fs.writeFileSync(AUTH_PATH, JSON.stringify(full, null, 2));
}

// --- pi-style provider/model resolution ---
function resolveTarget(requested) {
  let name = (requested || "").trim() || FALLBACK;
  if (!name.includes("/") && !name.toLowerCase().startsWith("claude")) name = FALLBACK;
  if (!name.includes("/")) name = "anthropic/" + name; // bare claude-*
  const slash = name.indexOf("/");
  const provider = name.slice(0, slash);
  const modelId = name.slice(slash + 1);
  if (provider !== "anthropic" && provider !== "openai-codex") {
    throw new Error(`unsupported provider '${provider}' (use anthropic/... or openai-codex/...)`);
  }
  return { provider, modelId };
}

// --- per-provider OAuth: read + auto-refresh ---
const CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token";
const CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";
const refreshing = {}; // provider -> in-flight promise

async function refreshCodex(entry) {
  const body = new URLSearchParams({ grant_type: "refresh_token", client_id: CODEX_CLIENT_ID, refresh_token: entry.refresh });
  const r = await fetch(CODEX_TOKEN_URL, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
  if (!r.ok) throw new Error(`codex token refresh failed: HTTP ${r.status} ${(await r.text()).slice(0, 200)}`);
  const j = await r.json();
  const next = { ...entry, access: j.access_token, refresh: j.refresh_token || entry.refresh, expires: Date.now() + j.expires_in * 1000 };
  writeAuthEntry("openai-codex", next);
  console.log("[pi-sidecar] refreshed codex token, expiry " + new Date(next.expires).toISOString());
  return next.access;
}

async function refreshAnthropic(entry) {
  const creds = await refreshAnthropicToken(entry.refresh);
  const next = { type: "oauth", access: creds.access, refresh: creds.refresh || entry.refresh, expires: creds.expires };
  writeAuthEntry("anthropic", next);
  console.log("[pi-sidecar] refreshed anthropic token, expiry " + new Date(next.expires).toISOString());
  return next.access;
}

async function getAccessToken(provider) {
  const entry = readAuth()[provider];
  if (!entry || !entry.access) throw new Error(`no ${provider} oauth entry in ${AUTH_PATH}`);
  if (entry.expires && entry.expires - Date.now() > REFRESH_BUFFER_MS) return entry.access;
  if (!entry.refresh) throw new Error(`${provider} token expired and no refresh token — re-run pi /login`);
  if (!refreshing[provider]) {
    const doIt = provider === "anthropic" ? refreshAnthropic : refreshCodex;
    refreshing[provider] = doIt(entry).finally(() => { refreshing[provider] = null; });
  }
  return await refreshing[provider];
}

const textOfParts = (parts) =>
  (parts || []).map((p) => (typeof p === "string" ? p : p.type === "text" ? p.text : "")).join("");
const zeroUsage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };

function toContext(body, target) {
  let systemPrompt = "";
  const messages = [];
  const api = target.provider === "anthropic" ? "anthropic-messages" : "openai-codex-responses";
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
      messages.push({ role: "assistant", content, api, provider: target.provider,
        model: target.modelId, usage: zeroUsage, stopReason: "stop", timestamp: Date.now() });
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
  if (!authOk(req)) { res.writeHead(401, { "Content-Type": "application/json" }); return res.end(JSON.stringify({ error: { message: "unauthorized" } })); }
  if (req.method === "GET" && req.url.startsWith("/v1/models")) {
    res.writeHead(200, { "Content-Type": "application/json" });
    const ids = [FALLBACK, "anthropic/claude-opus-4-8", "anthropic/claude-sonnet-4-6",
      "anthropic/claude-haiku-4-5-20251001", "openai-codex/gpt-5.5", "openai-codex/gpt-5.4"];
    return res.end(JSON.stringify({ object: "list", data: [...new Set(ids)].map((id) => ({ id, object: "model" })) }));
  }
  if (req.method !== "POST" || !req.url.startsWith("/v1/chat/completions")) { res.writeHead(404); return res.end("not found"); }

  let raw = ""; for await (const c of req) raw += c;
  let body; try { body = JSON.parse(raw); } catch { res.writeHead(400); return res.end("bad json"); }

  let target, token, model, ctx;
  try {
    target = resolveTarget(body.model);
    token = await getAccessToken(target.provider);
    model = getModel(target.provider, target.modelId);
    ctx = toContext(body, target);
  } catch (e) { res.writeHead(500, { "Content-Type": "application/json" }); return res.end(JSON.stringify({ error: { message: e.message } })); }

  const opts = { apiKey: token, reasoningEffort: process.env.PI_SIDECAR_REASONING || "medium" };
  // codex reasoning models reject `temperature` — the request comes back
  // empty (verified 2026-06-10). Anthropic accepts it.
  if (typeof body.temperature === "number" && target.provider !== "openai-codex") {
    opts.temperature = body.temperature;
  }
  if (typeof body.max_tokens === "number") opts.maxTokens = body.max_tokens;
  const created = Math.floor(Date.now() / 1000), id = "chatcmpl-pi-" + created;

  if (body.stream !== true) {
    try {
      const r = await complete(model, ctx, opts);
      if (r.stopReason === "error") {
        const diag = (r.diagnostics || []).map((d) => d.error?.message || d.type).join("; ");
        res.writeHead(502, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ error: { message: "provider error: " + (diag || "unknown") } }));
      }
      const msg = assistantToOpenAI(r);
      res.writeHead(200, { "Content-Type": "application/json" });
      // Echo the RESOLVED provider/model, not the requested name — callers'
      // telemetry must show what actually ran (fallback routing is invisible
      // otherwise; bitten 2026-06-10 when bare "gpt-5.5" silently ran sonnet).
      return res.end(JSON.stringify({ id, object: "chat.completion", created, model: `${target.provider}/${target.modelId}`,
        choices: [{ index: 0, message: msg, finish_reason: msg.tool_calls ? "tool_calls" : "stop" }],
        usage: r.usage ? { prompt_tokens: r.usage.input, completion_tokens: r.usage.output, total_tokens: r.usage.totalTokens } : undefined }));
    } catch (e) { res.writeHead(500, { "Content-Type": "application/json" }); return res.end(JSON.stringify({ error: { message: e.message } })); }
  }

  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive" });
  const send = (delta, finish = null) => res.write("data: " + JSON.stringify({ id, object: "chat.completion.chunk", created, model: `${target.provider}/${target.modelId}`, choices: [{ index: 0, delta, finish_reason: finish }] }) + "\n\n");
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

server.listen(PORT, HOST, () => console.log(`pi-sidecar (provider/model routing) on http://${HOST}:${PORT}/v1, fallback ${FALLBACK}${TOKEN ? " [auth on]" : ""}`));
