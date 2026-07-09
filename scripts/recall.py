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

# 余弦软地板:低于此值的纯语义漂移条目丢弃(负样本弃答)。FTS 精确关键词命中可豁免
# (端口号/模型名/版本号等,向量不敏感但关键词精确)。初值经审查实测标定,后由黄金集微调。
FLOOR = 0.55


def cos(a, b):
    d = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return d / (na * nb) if na and nb else 0.0


def fts_ids(con, query, limit=30):
    # 注意:FTS5 的 MATCH 必须写在【表名/列名】上,不能写在表别名上
    # (`f MATCH` 会报 no such column: f)——历史 bug 曾使整条 FTS 分支静默全灭。
    try:
        toks = [t for t in __import__("re").split(r"[\s,，。、?？!！]+", query) if len(t) >= 3]
        if not toks:
            return []
        match = " OR ".join(f'"{t}"' for t in toks[:8])
        rows = con.execute(
            "SELECT mi.id FROM memory_fts JOIN memory_item mi ON mi.rowid=memory_fts.rowid "
            "WHERE memory_fts MATCH ? AND mi.valid_until IS NULL LIMIT ?", (match, limit)).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        sys.stderr.write(f"[recall] FTS 检索失败(降级为纯向量): {str(e)[:120]}\n")
        return []


def recall(query, topk=8, floor=FLOOR):
    qv = embed_texts([query], text_type="query")[0]
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT mi.id,mi.type,mi.claim,mi.evidence,mi.confidence,mi.sources,me.dim,me.vec,"
        "mi.context,mi.status,mi.valid_from "
        "FROM memory_item mi JOIN memory_embedding me ON me.memory_item_id=mi.id "
        "WHERE mi.valid_until IS NULL AND me.dim = ?", (len(qv),)).fetchall()
    vec_rank = sorted(
        ((cos(qv, struct.unpack(f"<{r[6]}f", r[7])), r) for r in rows),
        key=lambda x: -x[0])
    fts = fts_ids(con, query)
    fts_set = set(fts)
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
        if csim < floor and mid not in fts_set:   # 弃答:纯语义漂移丢弃;FTS 精确命中豁免
            continue
        srcs = json.loads(r[5] or "[]")
        out.append({"id": mid, "type": r[1], "claim": r[2], "evidence": r[3],
                    "confidence": r[4], "cosine": round(csim, 3),
                    "context": r[8], "status": r[9], "valid_from": r[10],
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
    if not hits:
        print(f'问:「{q}」→ 未召回到相关记忆(库里可能没有,或都低于相关性地板 {FLOOR})')
        return
    print(f'问:「{q}」→ 召回 {len(hits)} 条\n')
    for i, h in enumerate(hits, 1):
        tag = "" if h.get("status") == "已确认" else "[待核] "
        print(f"[{i}] {tag}【{h['type']}】conf={h['confidence']} cos={h['cosine']} "
              f"日期={h.get('valid_from') or '?'} 源={h['sources']}")
        if h.get("context"):
            print(f"    情境: {h['context']}")
        print(f"    {h['claim']}")
        print(f"    证据: {(h['evidence'] or '')[:90]}\n")


if __name__ == "__main__":
    main()
