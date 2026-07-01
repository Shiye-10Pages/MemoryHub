#!/usr/bin/env python3
"""MemoryHub · Phase③ 队列规模化 — human_queue 聚类 + AI 预审

把 human_queue 的 high_impact / ai_derived_review 候选(矛盾类走逐条裁决,不在此)
按语义聚类 + AI 给每簇一个处置建议,产出 staging/queue_triage.json,供面板按簇批量决策。

处置建议(suggest):
  approve  — 确实高价值/关键决策,建议你确认入库(→已确认)
  downgrade— 无害但非关键(BUSINESS_KW 误命中等),建议自动入库为【待核】(移出队列,仍保留)
  reject   — 噪声/琐碎/无意义,建议拒绝(→ rejected.jsonl 留档)

不改库(纯只读分析 + 写 json)。失败默认 downgrade(最大保留,不误删)。
用法: python3 queue_triage.py [--near 0.85]
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3

from embed import embed_texts  # noqa: E402
from distill import call_qwen, parse  # noqa: E402

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")
OUT = os.path.join(HUB, "staging", "queue_triage.json")
NEAR = float(sys.argv[sys.argv.index("--near") + 1]) if "--near" in sys.argv else 0.85
MODEL = "qwen3-max"
BATCH = 12

SUGGEST_PROMPT = (
    "你是记忆库的队列预审员。下面每条是一个『待人工确认』的候选结论(多为被标记为高影响)。"
    "为每条给出处置建议,三选一:\n"
    "- approve:确实是值得长期记住的关键结论(定价/方向/收入/重要决策/核心方法),建议人工确认入库。\n"
    "- downgrade:无害但并非关键(只是恰好命中了价格/课程等词,实为普通信息),建议自动入库为普通记忆即可,不必占用人工队列。\n"
    "- reject:噪声/琐碎/重复/无意义/不像可复用结论,建议丢弃。\n"
    "严格只输出 JSON 数组,每元素 {\"i\":序号, \"suggest\":\"approve|downgrade|reject\", "
    "\"theme\":\"≤8字主题\", \"why\":\"≤20字理由\"},不要解释。\n\n候选:\n")


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def cluster(units, near):
    n = len(units)
    E = np.stack([u["vec"] for u in units]) if n else np.zeros((0, 1))
    assigned = [False] * n
    clusters = []
    for i in range(n):
        if assigned[i]:
            continue
        members = [i]
        assigned[i] = True
        sims = E @ E[i]
        for j in range(i + 1, n):
            if not assigned[j] and float(sims[j]) >= near:
                assigned[j] = True
                members.append(j)
        clusters.append(members)
    return clusters


def suggest(reps):
    """reps=[claim,...] → [{suggest,theme,why},...] 对齐。失败默认 downgrade。"""
    out = [None] * len(reps)
    for k in range(0, len(reps), BATCH):
        chunk = reps[k:k + BATCH]
        body = "\n".join(f"{i}. {c[:160]}" for i, c in enumerate(chunk, 1))
        try:
            data = parse(call_qwen(MODEL, SUGGEST_PROMPT + body))
            by_i = {int(d["i"]): d for d in data if isinstance(d, dict) and "i" in d}
        except Exception as e:
            print(f"  预审失败(默认 downgrade): {str(e)[:80]}")
            by_i = {}
        for i in range(1, len(chunk) + 1):
            d = by_i.get(i, {})
            s = d.get("suggest")
            out[k + i - 1] = {
                "suggest": s if s in ("approve", "downgrade", "reject") else "downgrade",
                "theme": (d.get("theme") or "")[:12], "why": (d.get("why") or "")[:30]}
    return out


def main():
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT id, candidate, reason FROM human_queue WHERE status='pending' "
        "AND reason IN ('high_impact','ai_derived_review') ORDER BY created_at").fetchall()
    con.close()
    if not rows:
        print("无可预审候选(high_impact/ai_derived_review)。")
        return
    units = []
    for qid, cand, reason in rows:
        c = json.loads(cand)
        units.append({"qid": qid, "claim": c.get("claim") or "", "reason": reason,
                      "type": c.get("type")})
    print(f"待预审 {len(units)} 条,嵌入聚类中…")
    vecs = embed_texts([u["claim"] for u in units])
    for u, v in zip(units, vecs):
        u["vec"] = unit(v)
    cl = cluster(units, NEAR)
    print(f"聚成 {len(cl)} 簇,AI 预审中…")
    reps = [units[m[0]]["claim"] for m in cl]
    sg = suggest(reps)

    out = []
    for ci, (members, s) in enumerate(zip(cl, sg)):
        out.append({
            "cluster_id": ci,
            "suggest": s["suggest"], "theme": s["theme"], "why": s["why"],
            "size": len(members),
            "qids": [units[m]["qid"] for m in members],
            "claims": [units[m]["claim"] for m in members[:8]],
        })
    out.sort(key=lambda x: -x["size"])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    dist = Counter(x["suggest"] for x in out)
    print(f"完成 → {OUT}")
    print(f"簇数 {len(out)} | 建议分布 {dict(dist)} | 覆盖候选 {sum(x['size'] for x in out)} 条")


if __name__ == "__main__":
    main()
