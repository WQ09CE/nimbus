# Docker Deployment

Nimbus can run as one application container that contains both the AgentOS API and
the Next.js web UI. The provided Compose file adds an Ollama service and pulls
the default model before the app starts.

## Start

```bash
docker compose up --build
```

If Docker Hub is slow from the current network, override the Node base image
without changing the Dockerfile:

```bash
NODE_IMAGE=mirror.gcr.io/library/node:20-bookworm-slim docker compose up --build
```

The same pattern is available for the Ollama runtime base image:

```bash
OLLAMA_BASE_IMAGE=mirror.gcr.io/library/ubuntu:24.04 docker compose up --build
```

If local Ollama already owns port `11434`, keep the Compose Ollama service on a
different host port:

```bash
OLLAMA_PORT=11435 docker compose up --build
```

Open the UI at:

```text
http://localhost:3000/nimbus-666
```

The default access token is `nimbus-666`; override it with
`NIMBUS_ACCESS_TOKEN`.

## Defaults

```text
NIMBUS_MODEL=ollama/gemma4:26b
OLLAMA_MODEL=gemma4:26b
OLLAMA_IMAGE=nimbus-ollama:local
OLLAMA_VERSION=
OLLAMA_BASE_IMAGE=ubuntu:24.04
OLLAMA_GPU_DEVICE=1
OLLAMA_BASE_URL=http://ollama:11434
NIMBUS_API_URL=http://127.0.0.1:4096
```

`NIMBUS_MODEL` is the Nimbus/LiteLLM model id. `OLLAMA_MODEL` is the model tag
that `ollama pull` downloads into the Ollama volume. The Linux Docker default
uses the official `gemma4:26b` Ollama tag. `OLLAMA_GPU_DEVICE` selects the
NVIDIA GPU exposed to the Ollama container.

The 12B Gemma4 tags require a current Ollama runtime. Compose builds the Ollama
service from `docker/ollama/Dockerfile` using the official Ollama installer so
it does not depend on a stale local `ollama/ollama:latest` image. Set
`OLLAMA_VERSION` to pin a specific runtime version.

## Useful Commands

```bash
docker compose ps
docker compose logs -f nimbus
docker compose logs -f ollama
docker compose down
```

## Local Backend Edits Without Rebuild

For Python backend-only changes, run Compose with the dev override once:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

The override bind-mounts `./src` into `/app/src`, so later backend source
edits only need a service restart:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml restart nimbus
```

The shorthand is:

```bash
make docker-dev-restart
```

Frontend changes still need a Next.js rebuild, because the Docker image runs
the production `npm run start` server from the built `.next` output.

Persisted data lives in Docker volumes:

```text
nimbus-data  -> /app/.nimbus
nimbus-logs  -> /app/.logs
ollama-data  -> /root/.ollama
```
