#!/bin/bash
# 双击打开 MemoryHub 本地记忆面板(首次会自动初始化环境)
# 启动器可放在任意路径(桌面/程序坞等),按以下顺序定位 MemoryHub 安装目录:
#   1) 环境变量 MEMORYHUB_HOME
#   2) 启动器自身所在目录(启动器仍放在仓库内时)
#   3) 记录文件 ~/.memoryhub_home(上次成功定位时写入)
#   4) 默认位置 ~/MemoryHub
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
POINTER="$HOME/.memoryhub_home"

is_hub() { [ -f "$1/scripts/web/server.py" ]; }

HUB=""
for cand in \
  "${MEMORYHUB_HOME:-}" \
  "$SCRIPT_DIR" \
  "$([ -f "$POINTER" ] && cat "$POINTER")" \
  "$HOME/MemoryHub"; do
  if [ -n "$cand" ] && is_hub "$cand"; then HUB="$cand"; break; fi
done

if [ -z "$HUB" ]; then
  echo "找不到 MemoryHub 安装目录。"
  echo "解决办法(任选其一):"
  echo "  · 把启动器放回 MemoryHub 目录内再双击"
  echo "  · 运行: echo /你的/MemoryHub路径 > ~/.memoryhub_home"
  echo "  · 或设置环境变量 MEMORYHUB_HOME 后重试"
  read -r
  exit 1
fi

# 记住这次定位到的目录,方便下次从任意路径启动
printf '%s\n' "$HUB" > "$POINTER" 2>/dev/null || true

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
