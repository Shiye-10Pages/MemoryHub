#!/bin/bash
# 双击打开 MemoryHub 本地记忆面板(首次会自动初始化环境)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HUB="${MEMORYHUB_HOME:-$SCRIPT_DIR}"
cd "$HUB" || exit 1

# 首次:没有 .venv 就自动跑 setup(建虚拟环境 + 装依赖 + 建库)
if [ ! -x "$HUB/.venv/bin/python" ] && [ -z "$MEMORYHUB_PYTHON" ]; then
  echo "首次启动:正在初始化环境(建虚拟环境 + 装依赖),稍等…"
  bash scripts/setup.sh || { echo "初始化失败,请在终端手动运行 scripts/setup.sh"; read -r; exit 1; }
fi

PY="${MEMORYHUB_PYTHON:-$HUB/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
mkdir -p "$HUB/logs"

# 没起服务就起
if ! curl -s -o /dev/null http://127.0.0.1:7788/api/stats 2>/dev/null; then
  nohup "$PY" scripts/web/server.py > logs/web.log 2>&1 &
fi

# 轮询就绪再开浏览器(最多约 20 秒),避免打开太早白屏
for _ in $(seq 1 40); do
  curl -s -o /dev/null http://127.0.0.1:7788/api/stats 2>/dev/null && break
  sleep 0.5
done
open http://127.0.0.1:7788
