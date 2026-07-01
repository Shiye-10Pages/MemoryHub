#!/usr/bin/env python3
"""存量记忆补情境锚(context)。按对话(canonical_document)生成、同对话共享一条锚,
比逐条省一个量级。幂等 + 断点续跑:只补 context IS NULL 的条目,中断可重跑。

用法:
    python3 backfill_context.py [--limit N] [--model qwen3-max]
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from distill import call_qwen  # noqa: E402  复用 LLM 调用

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")

SYS = """你是记忆情境标注器。下面是一段对话全文,以及从中提炼出的若干结论(claims)。
请用【一句中文、≤30字】概括:这些结论是在【什么情境/围绕什么主题】下得出的。
只从原文归纳,不杜撰、不展开;只输出这一句话,不要引号、不要解释。"""


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def conv_ids_of(sources_json):
    out = []
    for s in json.loads(sources_json or "[]"):
        if s.get("conv_id"):
            out.append(s["conv_id"])
    return out


def main():
    limit = arg("--limit")
    model = arg("--model", "qwen3-max")
    con = sqlite3.connect(DB, timeout=30.0)
    docs = con.execute("SELECT conv_id, text FROM canonical_document "
                       "WHERE conv_id IS NOT NULL AND text IS NOT NULL").fetchall()
    done = 0
    for conv_id, text in docs:
        if limit and done >= int(limit):
            break
        rows = con.execute(
            "SELECT id, claim FROM memory_item "
            "WHERE context IS NULL AND sources LIKE ?", (f'%\"conv_id\":\"{conv_id}\"%',)).fetchall()
        rows = [r for r in rows if conv_id in conv_ids_of(
            con.execute("SELECT sources FROM memory_item WHERE id=?", (r[0],)).fetchone()[0])]
        if not rows:
            continue                                   # 该对话名下无待补 → 跳过(断点续跑)
        claims = "\n".join(f"- {c}" for _, c in rows[:40])
        prompt = SYS + "\n\n--- 对话全文 ---\n" + (text or "")[:12000] + "\n\n--- 结论 ---\n" + claims
        try:
            ctx = (call_qwen(model, prompt) or "").strip().strip('"').strip("「」").splitlines()[0][:40]
        except Exception as e:
            print(f"  [{conv_id[:12]}] 失败,跳过: {str(e)[:80]}")
            continue
        if not ctx:
            continue
        ids = [r[0] for r in rows]
        con.execute("UPDATE memory_item SET context=? WHERE id IN (%s)" % ",".join("?" * len(ids)),
                    [ctx, *ids])
        con.commit()
        done += 1
        print(f"  [{conv_id[:12]}] {len(ids)} 条 ← {ctx}")
    con.close()
    print(f"backfill_context 完成:补了 {done} 个对话。")


if __name__ == "__main__":
    main()
