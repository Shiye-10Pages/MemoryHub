#!/bin/bash
# 双击打开 MemoryHub 本地记忆面板
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HUB="${MEMORYHUB_HOME:-$SCRIPT_DIR}"
PY="${MEMORYHUB_PYTHON:-$HUB/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi
mkdir -p "$HUB/logs"
if ! curl -s -o /dev/null http://127.0.0.1:7788/api/stats 2>/dev/null; then
  cd "$HUB" && nohup "$PY" scripts/web/server.py > logs/web.log 2>&1 &
  sleep 2
fi
open http://127.0.0.1:7788
