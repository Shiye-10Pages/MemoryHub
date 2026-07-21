#!/usr/bin/env python3
"""MemoryHub · migrate v7 — 把存量 memory_item 的野生类型归一到契约 9 类。

背景:gate 早期不校验 type,LLM 自造了 行动/风险/定价/角色定位… 等 20+ 野生类型(约 128 条),
让"契约=宪法"在存量里被架空、面板按类型筛选失真。本脚本用 gate.normalize_type 统一归一,
只改 type 字段,不动 claim/evidence/status/valid_until。幂等(已是 9 类的跳过)。

用法: python3 scripts/migrate_memory_v7.py [--db <memory.db>] [--dry-run]
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import normalize_type, TYPES  # noqa: E402  单一事实源,与写库闸同口径

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    dry = "--dry-run" in sys.argv
    db = sys.argv[sys.argv.index("--db") + 1] if "--db" in sys.argv else os.path.join(HUB, "memory.db")
    con = sqlite3.connect(db, timeout=30.0)
    rows = con.execute("SELECT id, type FROM memory_item").fetchall()
    changes = {}
    for mid, t in rows:
        if t in TYPES:
            continue
        nt = normalize_type(t)
        changes.setdefault((t, nt), 0)
        changes[(t, nt)] += 1
        if not dry:
            con.execute("UPDATE memory_item SET type=? WHERE id=?", (nt, mid))
    if not dry:
        con.commit()
    con.close()
    total = sum(changes.values())
    tag = "[dry-run] " if dry else ""
    print(f"{tag}野生类型条目: {total};归一映射:")
    for (t, nt), n in sorted(changes.items(), key=lambda kv: -kv[1]):
        print(f"  {tag}{n:4d}  {t} → {nt}")
    print(f"{tag}完成。" + ("(未写库,去掉 --dry-run 生效)" if dry else ""))


if __name__ == "__main__":
    main()
