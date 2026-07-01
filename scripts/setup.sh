#!/usr/bin/env bash
set -euo pipefail

HUB="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"

if [ -z "$PYTHON_BIN" ]; then
  echo "scripts/setup.sh: python3 not found" >&2
  exit 1
fi

cd "$HUB"
"$PYTHON_BIN" -m venv .venv
. "$HUB/.venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p logs imports staging raw/claude-code raw/claude-memory raw/claude-web raw/chatgpt
touch logs/.gitkeep imports/.gitkeep staging/.gitkeep raw/.gitkeep \
  raw/claude-code/.gitkeep raw/claude-memory/.gitkeep raw/claude-web/.gitkeep \
  raw/chatgpt/.gitkeep

python scripts/init_db.py
mkdir -p vault/cards

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已创建 .env，请填入 ALIBABA_KEY 等密钥后再运行提纯/语义召回。"
fi

echo "MemoryHub 初始化完成。启动：scripts/run_web.sh"
