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

and select model `gpt-5.5` (alias for `pi-codex/gpt-5.5`). nimbus defaults to
`http://localhost:8799/v1`.

## Bind & auth (important)
The sidecar fronts your ChatGPT subscription — never expose it unauthenticated.

- **Host-only (default, secure):** binds `127.0.0.1`, no token needed. Use this
  when running `nimbus serve` on the host.
- **Docker / remote:** the container reaches the host, so the sidecar must bind
  beyond loopback — which REQUIRES a shared secret. Start it with:
  ```bash
  PI_SIDECAR_HOST=0.0.0.0 PI_SIDECAR_TOKEN=<random-secret> PI_SIDECAR_PORT=8799 npm start
  ```
  and set the same secret for nimbus: `NIMBUS_PI_SIDECAR_TOKEN=<random-secret>`
  (compose passes it through). The sidecar refuses to bind non-loopback without
  a token. The token is checked (constant-time) on every request via the
  `Authorization: Bearer` header litellm already sends.

## Notes
- Auth: reads `~/.pi/agent/auth.json` → `openai-codex.access` (Bearer). The
  account id is derived from the JWT by pi-ai. The token is auto-refreshed
  (single-flight) when within 5 min of expiry, using the refresh token in the
  same file, and written back — so the sidecar keeps working across expiries.
- ToS: subscription use in third-party harnesses is publicly supported by
  OpenAI (pi/OpenCode). Keep iteration volume reasonable.
- Endpoints: `POST /v1/chat/completions` (stream + non-stream), `GET /v1/models`.
