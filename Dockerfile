ARG NODE_IMAGE=node:20-bookworm-slim
FROM ${NODE_IMAGE}

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:/usr/local/bin:${PATH}" \
    NEXT_TELEMETRY_DISABLED=1 \
    NIMBUS_HOST=0.0.0.0 \
    NIMBUS_PORT=4096 \
    WEBUI_PORT=3000 \
    NIMBUS_MODEL=ollama/gemma4:26b \
    OLLAMA_BASE_URL=http://ollama:11434

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        dumb-init \
        python3 \
        python3-dev \
        python3-venv \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /uvx /usr/local/bin/

COPY web-ui/package.json web-ui/package-lock.json ./web-ui/
RUN cd web-ui && npm ci --include=dev

COPY web-ui ./web-ui
RUN cd web-ui && npm run build && npm prune --omit=dev

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --python python3 --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --python python3

ENV NODE_ENV=production

COPY docker/start.sh ./docker/start.sh
RUN chmod +x ./docker/start.sh \
    && mkdir -p /app/.nimbus /app/.logs

EXPOSE 3000 4096

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://127.0.0.1:${WEBUI_PORT}/healthz >/dev/null || exit 1

ENTRYPOINT ["dumb-init", "--"]
CMD ["./docker/start.sh"]
