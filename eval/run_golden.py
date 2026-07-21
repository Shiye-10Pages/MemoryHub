#!/usr/bin/env python3
"""MemoryHub · 保真跑分:对黄金题集跑召回,算 Hit@k / 弃答率 / 覆盖缺口闭合。

判定:
- expect_empty=true → 召回必须为空(弃答)。
- expect_ids → 返回集里出现任一 id 即命中。
- expect_substr → 任一返回项的 claim/evidence 含任一子串即命中。
- expect_nonempty=true → 非空即命中。
- known_gap=true → 单独计入"覆盖缺口"桶,不计入发版门槛(等 T2.6 补齐)。

发版门槛(--gate 时启用,失败退出码 1):
- 负样本弃答率 = 100%
- 非缺口正样本 Hit@k ≥ THRESHOLD(默认 0.8)

用法:
    python3 eval/run_golden.py [--db <memory.db>] [--env <.env>] [--gate] [--json]
默认 db/env 取标准安装位置(~/MemoryHub 或本仓库根)。只读:只 SELECT + 调嵌入。
"""
import argparse
import json
import os
import sys

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(HUB, "scripts")
GOLDEN = os.path.join(HUB, "eval", "golden.jsonl")
THRESHOLD = 0.8


def load_env(path):
    if not path or not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def hit(q, hits):
    """按题的期望判定是否命中。返回 True/False。"""
    ids = {h["id"] for h in hits}
    blob = " ".join((h.get("claim") or "") + " " + (h.get("evidence") or "") for h in hits)
    if q.get("expect_empty"):
        return len(hits) == 0
    if q.get("expect_ids"):
        return any(i in ids for i in q["expect_ids"])
    if q.get("expect_substr"):
        return any(s in blob for s in q["expect_substr"])
    if q.get("expect_nonempty"):
        return len(hits) > 0
    return len(hits) > 0


def score(db=None, env=None):
    """跑黄金题集,返回结构化跑分(供 CLI 与面板 /api/golden 共用)。会调嵌入 API。"""
    db = db or (os.path.join(os.path.expanduser("~/MemoryHub"), "memory.db")
                if os.path.exists(os.path.expanduser("~/MemoryHub/memory.db"))
                else os.path.join(HUB, "memory.db"))
    env = env or (os.path.expanduser("~/MemoryHub/.env")
                  if os.path.exists(os.path.expanduser("~/MemoryHub/.env"))
                  else os.path.join(HUB, ".env"))
    load_env(env)
    sys.path.insert(0, SCRIPTS)
    import recall as R
    R.DB = db

    rows = [json.loads(l) for l in open(GOLDEN, encoding="utf-8") if l.strip()]
    buckets = {"positive": [], "negative": [], "gap": []}
    detail = []
    for q in rows:
        try:
            hits = R.recall(q["query"], q.get("topk", 8))
        except Exception as e:
            detail.append({"qid": q["qid"], "ok": False, "error": str(e)[:100]})
            (buckets["gap"] if q.get("known_gap") else
             buckets["negative"] if q.get("expect_empty") else buckets["positive"]).append(False)
            continue
        ok = hit(q, hits)
        b = "gap" if q.get("known_gap") else "negative" if q.get("expect_empty") else "positive"
        buckets[b].append(ok)
        top = hits[0] if hits else None
        detail.append({"qid": q["qid"], "cat": q["category"], "query": q["query"], "ok": ok, "bucket": b,
                       "n": len(hits), "top_cos": (top["cosine"] if top else None),
                       "top_claim": (top["claim"][:44] if top else "—")})

    def rate(lst):
        return (sum(lst) / len(lst)) if lst else 1.0
    pos, neg, gap = rate(buckets["positive"]), rate(buckets["negative"]), rate(buckets["gap"])
    passed = (neg >= 1.0) and (pos >= THRESHOLD)
    return {"positive_hit": pos, "negative_abstain": neg, "gap_closed": gap,
            "counts": {k: [sum(v), len(v)] for k, v in buckets.items()},
            "threshold": THRESHOLD, "passed": passed, "detail": detail,
            "db": os.path.basename(db)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--env", default=None)
    ap.add_argument("--gate", action="store_true", help="按发版门槛退出码")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    res = score(args.db, args.env)
    pos, neg, gap = res["positive_hit"], res["negative_abstain"], res["gap_closed"]
    buckets = {k: {"s": v[0], "n": v[1]} for k, v in res["counts"].items()}
    detail = res["detail"]

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*56}\nMemoryHub 保真跑分  (db={res['db']})\n{'='*56}")
        for d in detail:
            mark = "✅" if d.get("ok") else ("⭕" if d.get("bucket") == "gap" else "❌")
            print(f" {mark} [{d['qid']}·{d.get('cat','')}] "
                  f"n={d.get('n','?')} cos={d.get('top_cos')} — {d.get('top_claim','')}")
        print(f"{'-'*56}")
        print(f" 正样本 Hit@k     : {buckets['positive']['s']}/{buckets['positive']['n']}  ({pos:.0%})")
        print(f" 负样本 弃答率     : {buckets['negative']['s']}/{buckets['negative']['n']}  ({neg:.0%})")
        print(f" 覆盖缺口 已闭合   : {buckets['gap']['s']}/{buckets['gap']['n']}  ({gap:.0%})  ⭕=仍缺(待 T2.6)")
        print(f"{'='*56}")

    if args.gate:
        print(("✅ 发版门槛通过" if res["passed"] else
               f"❌ 发版门槛未过:负样本弃答={neg:.0%}(需100%)、正样本Hit={pos:.0%}(需≥{THRESHOLD:.0%})")
              if not args.json else "")
        sys.exit(0 if res["passed"] else 1)


if __name__ == "__main__":
    main()
