ARG OLLAMA_BASE_IMAGE=ubuntu:24.04
FROM ${OLLAMA_BASE_IMAGE}

ARG OLLAMA_VERSION=

ENV OLLAMA_HOST=0.0.0.0:11434

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        zstd \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    if [ -n "${OLLAMA_VERSION}" ]; then \
        curl -fsSL https://ollama.com/install.sh | OLLAMA_NO_START=1 OLLAMA_VERSION="${OLLAMA_VERSION}" sh; \
    else \
        curl -fsSL https://ollama.com/install.sh | OLLAMA_NO_START=1 sh; \
    fi; \
    ollama --version

EXPOSE 11434
VOLUME ["/root/.ollama"]

ENTRYPOINT ["ollama"]
CMD ["serve"]
