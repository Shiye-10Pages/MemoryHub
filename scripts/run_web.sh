#!/usr/bin/env bash
set -euo pipefail

HUB="$(cd "$(dirname "$0")/.." && pwd)"
PY="${MEMORYHUB_PYTHON:-$HUB/.venv/bin/python}"

if [ ! -x "$PY" ]; then
  PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ]; then
  echo "scripts/run_web.sh: python3 not found" >&2
  exit 1
fi

cd "$HUB"
exec "$PY" scripts/web/server.py
