#!/usr/bin/env bash
# Levanta el stack de Docker (Postgres, Redis, Backend, Prometheus, Grafana).
# Resuelve las rutas a partir de la ubicación de ESTE script, así que funciona
# sin importar dónde esté el proyecto o desde qué carpeta se ejecute.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Raíz del proyecto: $ROOT"
# Las rutas relativas del compose se resuelven respecto a la carpeta del propio
# archivo compose (infra/docker).
docker compose -f "$ROOT/infra/docker/docker-compose.yml" up -d
