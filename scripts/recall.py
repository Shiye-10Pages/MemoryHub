#!/usr/bin/env python3
"""MemoryHub · Stage 1 · Step 7 — recall(召回引擎)

对 memory.db 做混合召回:向量(text-embedding-v4 余弦)为主 + FTS5 关键词兜底,
RRF 融合,只返回未失效记忆(valid_until IS NULL),带证据/置信度/来源。
既是 CLI,也供 mcp_server.py 复用 recall()。

用法: python3 recall.py "你的问题" [topk]
"""
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3

from embed import embed_texts  # noqa: E402

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")


def cos(a, b):
    d = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return d / (na * nb) if na and nb else 0.0


def fts_ids(con, query, limit=30):
    try:
        toks = [t for t in __import__("re").split(r"[\s,，。、?？!！]+", query) if len(t) >= 3]
        if not toks:
            return []
        match = " OR ".join(f'"{t}"' for t in toks[:8])
        rows = con.execute(
            "SELECT mi.id FROM memory_fts f JOIN memory_item mi ON mi.rowid=f.rowid "
            "WHERE f MATCH ? AND mi.valid_until IS NULL LIMIT ?", (match, limit)).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def recall(query, topk=8):
    qv = embed_texts([query], text_type="query")[0]
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT mi.id,mi.type,mi.claim,mi.evidence,mi.confidence,mi.sources,me.dim,me.vec,mi.context "
        "FROM memory_item mi JOIN memory_embedding me ON me.memory_item_id=mi.id "
        "WHERE mi.valid_until IS NULL").fetchall()
    vec_rank = sorted(
        ((cos(qv, struct.unpack(f"<{r[6]}f", r[7])), r) for r in rows),
        key=lambda x: -x[0])
    fts = fts_ids(con, query)
    con.close()

    rrf, info = {}, {}
    for i, (s, r) in enumerate(vec_rank):
        rrf[r[0]] = rrf.get(r[0], 0) + 1.0 / (60 + i)
        info[r[0]] = (r, s)
    for i, mid in enumerate(fts):
        rrf[mid] = rrf.get(mid, 0) + 1.0 / (60 + i)
    ranked = sorted(rrf.items(), key=lambda kv: -kv[1])[:topk]

    out = []
    for mid, score in ranked:
        if mid not in info:
            continue
        r, csim = info[mid]
        srcs = json.loads(r[5] or "[]")
        out.append({"id": mid, "type": r[1], "claim": r[2], "evidence": r[3],
                    "confidence": r[4], "cosine": round(csim, 3),
                    "context": (r[8] if len(r) > 8 else None),
                    "sources": [f"{s.get('source')}:{(s.get('project') or '').split('/')[-1]}"
                                for s in srcs]})
    return out


def main():
    if len(sys.argv) < 2:
        print('用法: python3 recall.py "你的问题" [topk]')
        return
    q = sys.argv[1]
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    hits = recall(q, k)
    print(f'问:「{q}」→ 召回 {len(hits)} 条\n')
    for i, h in enumerate(hits, 1):
        print(f"[{i}] 【{h['type']}】conf={h['confidence']} cos={h['cosine']} 源={h['sources']}")
        if h.get("context"):
            print(f"    情境: {h['context']}")
        print(f"    {h['claim']}")
        print(f"    证据: {(h['evidence'] or '')[:90]}\n")


if __name__ == "__main__":
    main()
