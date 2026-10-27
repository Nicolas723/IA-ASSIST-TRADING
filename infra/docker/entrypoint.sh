#!/bin/sh
# Production entrypoint: apply database migrations, then serve API + UI.
set -eu

echo "[entrypoint] applying database migrations"
alembic upgrade head

# One worker on purpose: the autonomous engine, the live-trading guard and the
# heartbeat are in-process singletons; several workers would run them twice.
# --proxy-headers lets FastAPI see https:// behind Caddy (needed by the
# same-origin check of the cross-site guard).
echo "[entrypoint] starting server"
exec uvicorn src.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips="*"
