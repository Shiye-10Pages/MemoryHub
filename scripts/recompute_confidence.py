#!/usr/bin/env python3
"""MemoryHub · Phase① 收尾 — 回填存量 confidence 为【质量分】(去时效衰减)

背景:旧 gate.confidence = sr×em×ev×cross×freshness,freshness 随对话年龄指数衰减。
这让一年前的好洞见 confidence 被压到很低(甚至 <0.12 被跳过)——违背"最大保留"北极星,
也让体检/cleanup 把"老"误判成"低质"而批量休眠。gate.py 已去掉 freshness;本脚本把
【存量】memory_item 的 confidence 一并重算为质量分,使体检/cleanup 只针对真低质。

质量分 = sr × em × ev × cross(与新 gate.confidence 同口径,不含时效):
  sr=0.7, em=0.8, ev=1.0(存量无逐条 sr/em,用默认近似——见计划诚实边界)
  cross = 1 + min(0.25, 0.05×(来源数−1))   多源印证加成
  人工已确认(status='已应用')额外 ×1.25(与 review_queue.approve 同)
只改 confidence,不动 status / valid_until / revision。幂等。

用法: python3 recompute_confidence.py [--dry-run]
"""
import json
import os
import sqlite3
import sys

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")

SR_DEFAULT = 0.7
EM_DEFAULT = 0.8
HUMAN_BONUS = 1.25          # 与 review_queue.approve 一致
HUMAN_STATUSES = {"已应用", "已确认"}   # 兼容规整前后命名


def uniq_sources(sources):
    seen, n = set(), 0
    for s in sources:
        if not isinstance(s, dict):
            n += 1
            continue
        k = (s.get("source"), s.get("conv_id"))
        if k not in seen:
            seen.add(k)
            n += 1
    return max(1, n)


def quality(merged, status):
    cross = 1.0 + min(0.25, 0.05 * (merged - 1))
    q = SR_DEFAULT * EM_DEFAULT * 1.0 * cross
    if status in HUMAN_STATUSES:
        q *= HUMAN_BONUS
    return round(max(0.0, min(1.0, q)), 3)


def main():
    dry = "--dry-run" in sys.argv
    con = sqlite3.connect(DB, timeout=30.0)
    rows = con.execute("SELECT id, status, sources, confidence FROM memory_item").fetchall()
    changed = 0
    lo_before = lo_after = 0
    for mid, status, sources, oldc in rows:
        try:
            srcs = json.loads(sources or "[]")
        except Exception:
            srcs = []
        newc = quality(uniq_sources(srcs), status)
        oldc = oldc if oldc is not None else 0.0
        if oldc < 0.45:
            lo_before += 1
        if newc < 0.45:
            lo_after += 1
        if abs(newc - oldc) > 1e-6:
            changed += 1
            if not dry:
                con.execute("UPDATE memory_item SET confidence=? WHERE id=?", (newc, mid))
    if not dry:
        con.commit()
    con.close()
    tag = "[dry-run] " if dry else ""
    print(f"{tag}扫描 {len(rows)} 条;confidence 变化 {changed} 条。")
    print(f"{tag}conf<0.45(体检低置信):{lo_before} → {lo_after}")


if __name__ == "__main__":
    main()
