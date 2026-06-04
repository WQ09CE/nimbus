#!/usr/bin/env bash
set -euo pipefail

export NIMBUS_HOST="${NIMBUS_HOST:-0.0.0.0}"
export NIMBUS_PORT="${NIMBUS_PORT:-4096}"
export WEBUI_PORT="${WEBUI_PORT:-3000}"
export NIMBUS_DB="${NIMBUS_DB:-/app/.nimbus/nimbus.db}"
export NIMBUS_MODEL="${NIMBUS_MODEL:-ollama/gemma4:26b}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://ollama:11434}"
export NIMBUS_API_URL="${NIMBUS_API_URL:-http://127.0.0.1:${NIMBUS_PORT}}"
export HOSTNAME="${HOSTNAME:-0.0.0.0}"
export PORT="${PORT:-${WEBUI_PORT}}"

mkdir -p "$(dirname "${NIMBUS_DB}")" /app/.logs

backend_pid=""
frontend_pid=""

cleanup() {
    if [ -n "${frontend_pid}" ] && kill -0 "${frontend_pid}" 2>/dev/null; then
        kill -TERM "${frontend_pid}" 2>/dev/null || true
    fi
    if [ -n "${backend_pid}" ] && kill -0 "${backend_pid}" 2>/dev/null; then
        kill -TERM "${backend_pid}" 2>/dev/null || true
    fi
}

trap cleanup EXIT INT TERM

nimbus serve \
    --host "${NIMBUS_HOST}" \
    --port "${NIMBUS_PORT}" \
    --db "${NIMBUS_DB}" \
    --quiet &
backend_pid=$!

for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${NIMBUS_PORT}/api/v1/health" >/dev/null; then
        break
    fi
    if ! kill -0 "${backend_pid}" 2>/dev/null; then
        wait "${backend_pid}"
    fi
    sleep 1
done

if ! curl -fsS "http://127.0.0.1:${NIMBUS_PORT}/api/v1/health" >/dev/null; then
    echo "Nimbus API did not become healthy on port ${NIMBUS_PORT}" >&2
    exit 1
fi

cd /app/web-ui
npm run start -- -H 0.0.0.0 -p "${WEBUI_PORT}" &
frontend_pid=$!

wait -n "${backend_pid}" "${frontend_pid}"
