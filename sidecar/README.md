# pi-ai sidecar

OpenAI-compatible local proxy that exposes **GPT-5.x on your ChatGPT/Codex
subscription** to nimbus, via the officially-supported [`@earendil-works/pi-ai`](https://github.com/earendil-works/pi)
transport. nimbus keeps its own AgentOS runtime; this is only the model pipe.

```
nimbus DirectAdapter (model pi-codex/gpt-5.5)
  → LiteLLM openai/gpt-5.5, base_url=$NIMBUS_PI_SIDECAR_URL
    → this sidecar (/v1/chat/completions, OpenAI-compatible SSE)
      → pi-ai stream(getModel("openai-codex","gpt-5.5"), {apiKey})
        → ChatGPT subscription → GPT-5.5
```

## Prereqs
- Node 18+
- A pi login on this machine: `~/.pi/agent/auth.json` must contain an
  `openai-codex` OAuth entry (run pi's `/login` once; the file is reused).

## Run
```bash
cd sidecar
npm install
PI_SIDECAR_PORT=8799 npm start
```

Then point nimbus at it (default already matches):
```bash
export NIMBUS_PI_SIDECAR_URL=http://localhost:8799/v1
```
and select model `gpt-5.5` (alias for `pi-codex/gpt-5.5`).

## Notes
- Auth: reads `~/.pi/agent/auth.json` → `openai-codex.access` (Bearer). The
  account id is derived from the JWT by pi-ai. Token currently must be valid;
  refresh-on-expiry is a TODO (refresh token is present in the same file).
- ToS: subscription use in third-party harnesses is publicly supported by
  OpenAI (pi/OpenCode). Keep iteration volume reasonable.
- Endpoints: `POST /v1/chat/completions` (stream + non-stream), `GET /v1/models`.
