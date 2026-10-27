# =============================================================================
# AI Trading Assistant — production image (API + built web UI on one port)
#
#   docker build -t ai-trading-assistant .
#
# The UI is compiled in a Node stage and served by FastAPI from
# FRONTEND_DIST_PATH, so users only open http://<host>:8000 (or the HTTPS
# domain behind Caddy). Secrets (.env) and the encrypted vault are mounted at
# runtime and never baked into the image (see .dockerignore).
# =============================================================================

# ---------------------------------------------------------------------------
# 1. Web UI
# ---------------------------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# 2. Backend runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="AI Trading Assistant"
LABEL org.opencontainers.image.description="Autonomous trading assistant: FastAPI backend + web UI"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIST_PATH=/app/frontend_dist \
    VAULT_STORAGE_PATH=/app/vault/.vault_store.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first (cached until pyproject changes). The editable install
# keeps the code at /app/src, which runtime paths such as /app/vault rely on.
COPY backend/pyproject.toml backend/README.md ./
RUN mkdir -p src && touch src/__init__.py \
    && pip install --upgrade pip hatchling \
    && pip install -e .

COPY backend/src/ ./src/
COPY backend/alembic.ini ./alembic.ini
COPY --from=frontend /build/dist/ ./frontend_dist/
COPY infra/docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh \
    && groupadd -r trading && useradd -r -g trading -d /app trading \
    && mkdir -p /app/vault \
    && chown -R trading:trading /app
USER trading

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
