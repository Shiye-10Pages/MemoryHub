#!/usr/bin/env python3
"""MemoryHub · 人工闸审核 — 清理 human_queue

把高影响候选,经你确认后写入正库(status=已确认、享人工确认加权 ×1.25),
或丢弃(记入 staging/rejected.jsonl 备查)。序号按列表顺序(= 按 id 排序)。

用法:
  python3 review_queue.py                 # 列出
  python3 review_queue.py --approve-all    # 全部入库
  python3 review_queue.py --approve-all --reject 9   # 除 9 外全入库
  python3 review_queue.py --approve 1,2,3  # 仅这几条入库,其余留队列
  python3 review_queue.py --reject 9       # 仅丢弃第 9 条
"""
import datetime
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3

from embed import embed_texts, DIM, MODEL, pack_embedding  # noqa: E402

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")
REJECTED = os.path.join(HUB, "staging", "rejected.jsonl")


def parse_idx(s):
    out = set()
    for part in (s or "").replace(",", " ").split():
        if part.strip().isdigit():
            out.add(int(part))
    return out


def approve(con, cid, c):
    conf = min(1.0, round(float(c.get("confidence", 0.5)) * 1.25, 3))  # 人工确认加权
    ts = ""
    for s in c.get("sources", []):
        ts = max(ts, s.get("ts") or "")
    review = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    rev = cid + "-r1"
    vec = embed_texts([c["claim"]])[0]
    con.execute(
        "INSERT OR IGNORE INTO memory_item(id,type,claim,evidence,sources,confidence,"
        "valid_from,valid_until,status,review_date,links,content_hash,current_revision_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, c.get("type"), c["claim"], c.get("evidence"),
         json.dumps(c.get("sources", []), ensure_ascii=False), conf,
         (ts[:10] or None), None, "已确认", review, "[]", cid, rev))
    con.execute(
        "INSERT OR IGNORE INTO memory_item_revision(id,memory_item_id,revision_num,claim,"
        "evidence,sources,confidence,valid_from,status,change_reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (rev, cid, 1, c["claim"], c.get("evidence"),
         json.dumps(c.get("sources", []), ensure_ascii=False), conf, (ts[:10] or None),
         "已确认", "human_approved"))
    con.execute("INSERT OR IGNORE INTO memory_embedding(memory_item_id,model,dim,vec) VALUES(?,?,?,?)",
                (cid,) + pack_embedding(vec))


def main():
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT id, candidate FROM human_queue "
                       "WHERE status='pending' OR status IS NULL ORDER BY id").fetchall()
    if not rows:
        print("队列为空。")
        return
    args = sys.argv
    def getval(flag):
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else ""
    reject_idx = parse_idx(getval("--reject"))
    if "--approve-all" in args:
        approve_idx = {i for i in range(1, len(rows) + 1)} - reject_idx
    elif "--approve" in args:
        approve_idx = parse_idx(getval("--approve"))
    elif reject_idx:
        approve_idx = set()
    else:
        for i, (qid, cand) in enumerate(rows, 1):
            print(f"[{i}] {json.loads(cand).get('claim')[:60]}")
        print(f"\n共 {len(rows)} 条。用 --approve-all / --approve 1,2 / --reject 9 处置。")
        return

    st = {"approved": 0, "rejected": 0}
    os.makedirs(os.path.dirname(REJECTED), exist_ok=True)
    rej_f = open(REJECTED, "a", encoding="utf-8")
    for i, (qid, cand) in enumerate(rows, 1):
        c = json.loads(cand)
        if i in approve_idx:
            approve(con, c["id"], c)
            con.execute("DELETE FROM human_queue WHERE id=?", (qid,))
            st["approved"] += 1
        elif i in reject_idx:
            rej_f.write(cand + "\n")
            # 不删行:改状态留档。行的 q_ id 仍在 gate 的 seen 里 → 被拒候选不会每晚复活(审查 P0-B)。
            con.execute("UPDATE human_queue SET status='rejected', resolved_at=datetime('now') WHERE id=?", (qid,))
            st["rejected"] += 1
    rej_f.close()
    con.commit()
    con.close()
    print("审核完成: " + json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
