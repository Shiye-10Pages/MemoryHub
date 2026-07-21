#!/usr/bin/env python3
"""换 provider 后重嵌:把与当前 provider 维度不一致的记忆向量重新嵌入并替换。

背景(审查 P1-4):recall 用 `WHERE me.dim = 查询向量维度` 过滤,换 provider(如 dashscope 1024
→ openai 1536)后,旧维向量全部对不上 → 旧记忆从语义召回里静默消失。本脚本按当前 provider
重嵌,INSERT OR REPLACE 覆盖旧向量,使它们重新可召回。

默认只重嵌【维度不匹配 / 缺向量】的条(省 token);--all 全量刷新。会调嵌入 API。
用法: python3 scripts/reembed.py [--db <memory.db>] [--batch 50] [--all] [--dry-run]
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from embed import embed_texts, pack_embedding, current_dim, current_model  # noqa: E402

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HUB, "memory.db"))
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--all", action="store_true", help="全量重嵌(默认只重嵌维度不匹配的)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    target = current_dim()
    con = sqlite3.connect(args.db, timeout=30.0)
    if args.all:
        rows = con.execute("SELECT id, claim FROM memory_item").fetchall()
        scope = "全量"
    else:
        rows = con.execute(
            "SELECT mi.id, mi.claim FROM memory_item mi "
            "LEFT JOIN memory_embedding me ON me.memory_item_id = mi.id "
            "WHERE me.dim IS NULL OR me.dim != ?", (target,)).fetchall()
        scope = "维度不匹配/缺失"
    print(f"目标维度 {target}(模型 {current_model()});需重嵌 {len(rows)} 条 [{scope}]")
    if args.dry_run:
        print("[dry-run] 未写库。去掉 --dry-run 生效。")
        con.close()
        return
    if not rows:
        print("无需重嵌。")
        con.close()
        return

    done = 0
    for i in range(0, len(rows), args.batch):
        chunk = rows[i:i + args.batch]
        vecs = embed_texts([(c[1] or "") for c in chunk])
        for (mid, _), vec in zip(chunk, vecs):
            con.execute("INSERT OR REPLACE INTO memory_embedding(memory_item_id,model,dim,vec) "
                        "VALUES(?,?,?,?)", (mid,) + pack_embedding(vec))
        con.commit()
        done += len(chunk)
        print(f"  {done}/{len(rows)}")
    con.close()
    print(f"完成:{done} 条已按 {target} 维重嵌,旧记忆恢复语义可召回。")
    print("提示:素材库(material.db)如也换了 provider,需另行重嵌(暂未覆盖)。")


if __name__ == "__main__":
    main()
