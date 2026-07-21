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
mkdir -p "$HUB/logs" "$HUB/staging"
LOG="$HUB/logs/nightly-$(date +%Y%m%d-%H%M%S).log"
FAILS=0
LAST_FAIL=""

# 跨进程管线互斥(与面板导入/同步共用 mkdir 原子锁;mac 无 flock 命令)。拿不到锁则本次跳过。
LOCKDIR="$HUB/staging/pipeline.lock.d"
LOCK_MTIME="$(stat -f %m "$LOCKDIR" 2>/dev/null || stat -c %Y "$LOCKDIR" 2>/dev/null || echo 0)"
if [ -d "$LOCKDIR" ] && [ "$(( $(date +%s) - LOCK_MTIME ))" -gt 7200 ]; then
  rmdir "$LOCKDIR" 2>/dev/null   # 陈旧锁(>2h,进程被杀没清理)自愈
fi
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  echo "$(date) 另一采集/同步在跑,本次夜跑跳过" >> "$LOG"
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

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
