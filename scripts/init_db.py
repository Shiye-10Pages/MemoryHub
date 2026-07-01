#!/usr/bin/env python3
"""MemoryHub · Stage 1 — 初始化 SQLite 控制面/索引库。

用法:
    python3 init_db.py [db_path]
默认 db_path = ~/MemoryHub/memory.db。幂等:重复运行安全(全部 IF NOT EXISTS)。
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HUB = os.path.dirname(HERE)
DEFAULT_DB = os.path.join(HUB, "memory.db")
SCHEMA = os.path.join(HERE, "schema.sql")


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    with open(SCHEMA, encoding="utf-8") as f:
        ddl = f.read()

    con = sqlite3.connect(db)
    try:
        con.executescript(ddl)
        con.commit()

        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        print(f"OK  memory.db 就绪: {db}")
        print("    表/索引对象:", ", ".join(tables))

        # FTS5 自检(trigram 需 >=3 字,故用 3 字查询)
        con.execute(
            "INSERT INTO memory_item(id,type,claim,evidence,sources,valid_from) "
            "VALUES('__selftest__','事实','自检测试项','证据自检测试',"
            "'[{\"source\":\"selftest\"}]', date('now'))"
        )
        n = con.execute(
            "SELECT count(*) FROM memory_fts WHERE memory_fts MATCH '自检测'"
        ).fetchone()[0]
        con.execute("DELETE FROM memory_item WHERE id='__selftest__'")
        con.commit()
        print(f"    FTS5 中文自检: {'OK' if n >= 1 else 'FAIL'}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
