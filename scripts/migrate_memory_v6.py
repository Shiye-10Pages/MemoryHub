#!/usr/bin/env python3
"""为 memory_item 增加 context(情境锚)列。幂等,可重复执行。"""
import os
import sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory.db")
con = sqlite3.connect(DB)
cols = [r[1] for r in con.execute("PRAGMA table_info(memory_item)")]
if "context" not in cols:
    con.execute("ALTER TABLE memory_item ADD COLUMN context TEXT")
    con.commit()
    print("已加列 memory_item.context")
else:
    print("context 列已存在,跳过")
con.close()
