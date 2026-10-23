#!/usr/bin/env bash
# Levanta el frontend (Vite) desde cualquier ubicación.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ROOT/frontend"
npm run dev
