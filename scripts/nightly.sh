#!/bin/bash
# MemoryHub · Step 8 · 夜间增量管线(由 launchd 调度,每晚自动跑)
# 采集(Claude Code 对话)→ 来源闸内增量提纯 → 四道保真闸 → Obsidian 卡片+commit
# 纯本地;全程日志写 logs/。手动跑: bash ~/MemoryHub/scripts/nightly.sh
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HUB="${MEMORYHUB_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PY="${MEMORYHUB_PYTHON:-$HUB/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi
mkdir -p "$HUB/logs"
LOG="$HUB/logs/nightly-$(date +%Y%m%d-%H%M%S).log"
FAILS=0
LAST_FAIL=""

# 记录每步退出码;非零则计数 + 记住最后失败步(不 set -e:后续步仍尝试,但失败会被上报)
run() { echo ">>> $*"; "$@"; local rc=$?; echo "<<< exit=$rc";
        [ "$rc" -ne 0 ] && { FAILS=$((FAILS+1)); LAST_FAIL="$* (exit=$rc)"; }; return "$rc"; }

{
  echo "========== MemoryHub 夜间管线开始 $(date) =========="
  cd "$HUB" || { echo "cd $HUB 失败"; exit 1; }
  run "$PY" scripts/ingest.py             # 增量采集 Claude Code 对话(跳已采)
  run "$PY" scripts/distill.py            # 来源闸内提纯,跳已提纯会话
  run "$PY" scripts/gate.py --near 0.88   # 四道保真闸 + 写库(只嵌入新候选)
  run "$PY" scripts/project.py --commit   # 生成/更新卡片 + git 提交
  echo "========== 完成 $(date);失败步数=$FAILS =========="
} >> "$LOG" 2>&1

# 有失败 → 写面板可读的 last_failure.txt(仪表盘「夜间任务」展示);全成功则清掉旧标记
if [ "$FAILS" -ne 0 ]; then
  printf '%s\n' "$(date '+%Y-%m-%d %H:%M') 夜跑 $FAILS 步失败,最后:$LAST_FAIL;详见 $(basename "$LOG")" > "$HUB/logs/last_failure.txt"
else
  rm -f "$HUB/logs/last_failure.txt" 2>/dev/null || true
fi
