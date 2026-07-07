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

# pip 安装带重试 + 备用源兜底：镜像源瞬时抽风(如 numpy "from versions: none")时自愈
PIP_MIRRORS=(
  "https://pypi.tuna.tsinghua.edu.cn/simple"
  "https://mirrors.aliyun.com/pypi/simple"
  "https://pypi.org/simple"
)
pip_install() {  # 参数即 pip install 的参数(如 --upgrade pip / -r requirements.txt)
  local i url
  for i in "${!PIP_MIRRORS[@]}"; do
    url="${PIP_MIRRORS[$i]}"
    echo "→ pip 源 $((i + 1))/${#PIP_MIRRORS[@]}: $url"
    if python -m pip install --retries 5 --timeout 30 -i "$url" "$@"; then
      return 0
    fi
    echo "⚠ 源失败，换下一个: $url" >&2
  done
  echo "✗ 所有镜像源均失败: pip install $*" >&2
  return 1
}

pip_install --upgrade pip
pip_install -r requirements.txt

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
