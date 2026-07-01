#!/usr/bin/env python3
"""MemoryHub · Stage 1 · Step 4 — 四道保真闸 + 写库(含真·矛盾/演化闸)

输入: staging/candidates.jsonl(distill 产出,已过【溯源闸】)。
流程:
  ① 精确去重(按规整化 claim 哈希)合并来源。
  ② 嵌入(text-embedding-v4 1024 维)。
  ③ 近邻关系闸(对每个新候选,找库内/批内最相似条):
     - cosine ≥ AUTO_SAME(0.95) → 直接判同义(免 LLM),并来源。
     - CONTRADICT_LO(0.82) ≤ cosine < 0.95 → qwen 判别 {同义/演化/矛盾/相近}:
         · 同义 → 并来源(不重插);
         · 演化 → 两条都留,较早一条置 valid_until + status=已被取代 + 写 revision,新旧 links 互链;
         · 矛盾 → 两条都留且都现行,links 互链(rel=矛盾),入"待裁决矛盾"队列(不替人判谁对);
         · 相近 → 各自独立入库。
     - cosine < 0.82 → 独立,走常规人工闸/写库。
  ④ 复合置信度打分。
  ⑤ 人工闸:AI 推断(force_review)或高影响业务项(impact 且命中定价/方向/收入)→ human_queue。
幂等:memory_item.id = claim 哈希,INSERT OR IGNORE;已入库/排队 claim 跳过。

用法: python3 gate.py [--near 0.95] [--judge-lo 0.82]
"""
import datetime
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from embed import embed_texts, DIM, MODEL  # noqa: E402
from distill import call_qwen, parse        # noqa: E402  复用 DashScope 调用

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")
CAND = os.path.join(HUB, "staging", "candidates.jsonl")

AUTO_SAME = float(sys.argv[sys.argv.index("--near") + 1]) if "--near" in sys.argv else 0.95
CONTRADICT_LO = float(sys.argv[sys.argv.index("--judge-lo") + 1]) if "--judge-lo" in sys.argv else 0.82
LOW_CONF = 0.45
JUDGE_MODEL = "qwen3-max"
JUDGE_BATCH = 10
REL_OK = ("同义", "演化", "矛盾", "相近")
BUSINESS_KW = ["定价", "价格", "收入", "营收", "销售", "客单", "单价", "商业模式", "变现",
               "方向", "放弃", "战略", "融资", "招聘", "离职", "找工作", "课程", "付费", "订阅价", "回去找工作"]

JUDGE_PROMPT = (
    "你是记忆库的关系判别器。下面给出若干『结论对』,每对含 A、B 两条结论。"
    "判断每对的关系,只能是四类之一:\n"
    "- 同义:同一主题且结论一致(仅措辞不同)。\n"
    "- 演化:同一主题,一条是对另一条的细化/更新/修正(方向一致但更精确或改进)。\n"
    "- 矛盾:同一主题,但结论相互对立/不兼容(如定价不同、方向相反、应该 vs 不应该)。\n"
    "- 相近:主题相关但不构成同义/演化/矛盾(各自独立的不同结论)。\n"
    "严格只输出 JSON 数组,每元素 {\"i\": 序号, \"rel\": \"同义|演化|矛盾|相近\"},不要解释。\n\n结论对:\n")


def norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()


def claim_id(claim):
    return hashlib.sha256(norm(claim).encode("utf-8")).hexdigest()[:16]


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def confidence(merged, ts=None, sr=0.7, em=0.8):
    # 质量分 = 源可信 × 抽取法 × 证据 × 跨源印证。【刻意不含时效衰减】:
    # freshness 只回答"还新不新",不该决定一条洞见值不值得保留、也不该压低它的质量分
    # (否则一年前的好方法会因"老"被跳过或埋没,违背"最大保留"北极星)。
    # 时效语义由 valid_until/status 承载;召回端只按相关性(向量+FTS)排序,不用 confidence。
    ev = 1.0
    cross = 1.0 + min(0.25, 0.05 * (merged - 1))     # 跨源印证(唯一加成)
    return max(0.0, min(1.0, sr * em * ev * cross))


def high_impact(it):
    if not it.get("impact"):
        return False
    blob = norm(it.get("claim", "")) + norm(it.get("evidence", ""))
    return any(norm(k) in blob for k in BUSINESS_KW)


def load_candidates():
    """精确去重 → [{item, sources, n}]。"""
    by_id = {}
    for line in open(CAND, encoding="utf-8"):
        it = json.loads(line)
        cid = claim_id(it.get("claim", ""))
        if cid not in by_id:
            by_id[cid] = {"item": it, "sources": [], "n": 0}
        by_id[cid]["sources"].extend(it.get("sources", []))
        by_id[cid]["n"] += 1
    return list(by_id.values())


def uniq_sources(sources):
    seen, out = set(), []
    for s in sources:
        k = (s.get("source"), s.get("conv_id"))
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def judge_pairs(pairs, model=JUDGE_MODEL):
    """pairs=[(claim_a, claim_b)…] → ['同义'|'演化'|'矛盾'|'相近', …](对齐)。失败默认'相近'(独立,不破坏)。"""
    out = []
    for k in range(0, len(pairs), JUDGE_BATCH):
        chunk = pairs[k:k + JUDGE_BATCH]
        body = "\n".join(f"{i}. A: {a}\n   B: {b}" for i, (a, b) in enumerate(chunk, 1))
        rel_by_i = {}
        try:
            data = parse(call_qwen(model, JUDGE_PROMPT + body))
            for d in data:
                if isinstance(d, dict) and "i" in d:
                    rel_by_i[int(d["i"])] = d.get("rel")
        except Exception as e:
            print(f"  判别失败(默认相近): {str(e)[:80]}")
        for i in range(1, len(chunk) + 1):
            r = rel_by_i.get(i, "相近")
            out.append(r if r in REL_OK else "相近")
    return out


# ---------- 写库辅助(矛盾/演化留痕) ----------
def insert_memory(con, cid, it, srcs, conf, vfrom, status, links, vec, valid_until=None):
    review = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    rev_id = cid + "-r1"
    links_json = json.dumps(links, ensure_ascii=False)
    con.execute(
        "INSERT OR IGNORE INTO memory_item"
        "(id,type,claim,context,evidence,sources,confidence,valid_from,valid_until,status,"
        " review_date,links,content_hash,current_revision_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, it.get("type"), it["claim"], it.get("context"), it.get("evidence"),
         json.dumps(srcs, ensure_ascii=False), round(conf, 3),
         vfrom or None, valid_until, status, review, links_json, cid, rev_id))
    con.execute(
        "INSERT OR IGNORE INTO memory_item_revision"
        "(id,memory_item_id,revision_num,claim,evidence,sources,confidence,"
        " valid_from,valid_until,status,change_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (rev_id, cid, 1, it["claim"], it.get("evidence"),
         json.dumps(srcs, ensure_ascii=False), round(conf, 3), vfrom or None,
         valid_until, status, "create"))
    con.execute("INSERT OR IGNORE INTO memory_embedding(memory_item_id,model,dim,vec) VALUES(?,?,?,?)",
                (cid, MODEL, DIM, struct.pack(f"<{DIM}f", *vec)))


def append_link(con, item_id, link):
    row = con.execute("SELECT links FROM memory_item WHERE id=?", (item_id,)).fetchone()
    if not row:
        return
    links = json.loads(row[0] or "[]")
    if not any(l.get("id") == link["id"] for l in links):
        links.append(link)
        con.execute("UPDATE memory_item SET links=? WHERE id=?",
                    (json.dumps(links, ensure_ascii=False), item_id))


def supersede_old(con, old_id, today, new_id):
    """旧条置 valid_until + status=已被取代 + 写 revision + 互链(镜像 api_cleanup 写法)。"""
    rev = con.execute("SELECT COALESCE(MAX(revision_num),0)+1 FROM memory_item_revision "
                      "WHERE memory_item_id=?", (old_id,)).fetchone()[0]
    con.execute("INSERT OR IGNORE INTO memory_item_revision"
                "(id,memory_item_id,revision_num,status,valid_until,change_reason) "
                "VALUES(?,?,?,?,?,?)",
                (f"{old_id}-r{rev}", old_id, rev, "已被取代", today, "superseded_by_evolution"))
    con.execute("UPDATE memory_item SET valid_until=?, status='已被取代' WHERE id=?", (today, old_id))
    append_link(con, old_id, {"id": new_id, "rel": "被取代"})


def merge_sources_into(con, old_id, new_srcs):
    row = con.execute("SELECT sources FROM memory_item WHERE id=?", (old_id,)).fetchone()
    if not row:
        return
    old = json.loads(row[0] or "[]")
    alls = uniq_sources(old + new_srcs)
    if len(alls) > len(old):
        con.execute("UPDATE memory_item SET sources=? WHERE id=?",
                    (json.dumps(alls, ensure_ascii=False), old_id))


def main():
    today = datetime.date.today().isoformat()
    groups = load_candidates()
    c0 = sqlite3.connect(DB)
    seen = {r[0] for r in c0.execute("SELECT id FROM memory_item")}
    seen |= {r[0][2:] for r in c0.execute("SELECT id FROM human_queue WHERE id LIKE 'q\\_%' ESCAPE '\\'")}
    c0.close()
    before = len(groups)
    groups = [g for g in groups if claim_id(g["item"]["claim"]) not in seen]
    print(f"精确去重: {before} 唯一 claim;增量过滤后新候选 {len(groups)}(跳过 {before - len(groups)})")
    if not groups:
        print("无新候选,结束。")
        return
    print("嵌入中…")
    try:
        vecs = embed_texts([g["item"]["claim"] for g in groups])
    except Exception as e:
        print(f"跳过写库:嵌入失败({e})。候选已保留在 staging/candidates.jsonl;"
              f"在 .env 配好 ALIBABA_KEY 后重跑 `python scripts/gate.py` 即可入库。")
        return

    con = sqlite3.connect(DB, timeout=30.0)
    # 库内现行条目:向量(单位化)+ claim + valid_from,做近邻底座;新写入的也追加进来(批内互检)
    cap = len(vecs[0]) if vecs else DIM   # 用实际嵌入维度,兼容不同 provider
    E = np.zeros((con.execute("SELECT COUNT(*) FROM memory_embedding WHERE dim=?", (cap,)).fetchone()[0] + len(groups), cap),
                 dtype=np.float32)
    meta = []   # 与 E 行对齐:{id, claim, valid_from}
    n = 0
    for r in con.execute("SELECT mi.id, mi.claim, mi.valid_from, me.dim, me.vec "
                         "FROM memory_item mi JOIN memory_embedding me ON me.memory_item_id=mi.id "
                         "WHERE mi.valid_until IS NULL AND me.dim = ?", (cap,)):
        E[n] = unit(struct.unpack(f"<{r[3]}f", r[4]))
        meta.append({"id": r[0], "claim": r[1], "valid_from": r[2] or ""})
        n += 1

    st = {"written": 0, "same_merged": 0, "evolved": 0, "contradicted": 0,
          "queued_impact": 0, "queued_review": 0, "queued_contra": 0, "lowconf_skip": 0}

    for gi, g in enumerate(groups):
        it = g["item"]
        srcs = uniq_sources(g["sources"])
        merged = g["n"]
        ts = max((s.get("ts") or "" for s in srcs), default="")
        vfrom = ts[:10]
        conf = confidence(merged, ts, it.get("sr", 0.7), it.get("em", 0.8))
        cid = claim_id(it["claim"])
        v = unit(vecs[gi])

        # 最近邻(库内 + 已写入)
        rel, j = None, -1
        if n > 0:
            sims = E[:n] @ v
            j = int(np.argmax(sims))
            best = float(sims[j])
            if best >= AUTO_SAME:
                rel = "同义"
            elif best >= CONTRADICT_LO:
                rel = judge_pairs([(it["claim"], meta[j]["claim"])])[0]

        # 同义 → 并来源,不重插
        if rel == "同义":
            merge_sources_into(con, meta[j]["id"], srcs)
            st["same_merged"] += 1
            continue

        # 演化 → 两条都留,较早者被取代 + 互链
        if rel == "演化":
            old_vf = meta[j]["valid_from"]
            if vfrom and old_vf and vfrom < old_vf:        # 新候选反而更早 → 它生而被取代
                insert_memory(con, cid, it, srcs, conf, vfrom, "已被取代",
                              [{"id": meta[j]["id"], "rel": "被取代"}], v, valid_until=(old_vf or today))
                append_link(con, meta[j]["id"], {"id": cid, "rel": "取代"})
            else:                                          # 库内那条更早 → 取代它
                insert_memory(con, cid, it, srcs, conf, vfrom, "待核",
                              [{"id": meta[j]["id"], "rel": "取代"}], v)
                supersede_old(con, meta[j]["id"], today, cid)
            E[n] = v
            meta.append({"id": cid, "claim": it["claim"], "valid_from": vfrom})
            n += 1
            st["evolved"] += 1
            continue

        # 矛盾 → 两条都现行 + 互链 + 入待裁决队列(不替人判谁对;争议靠 links+队列表达,不动 status)
        if rel == "矛盾":
            insert_memory(con, cid, it, srcs, conf, vfrom, "待核",
                          [{"id": meta[j]["id"], "rel": "矛盾"}], v)
            append_link(con, meta[j]["id"], {"id": cid, "rel": "矛盾"})
            payload = {"id": cid, "type": it.get("type"), "claim": it["claim"],
                       "evidence": it.get("evidence"), "sources": srcs,
                       "confidence": round(conf, 3), "conflict_with": meta[j]["id"]}
            con.execute("INSERT OR IGNORE INTO human_queue(id,candidate,reason) VALUES(?,?,?)",
                        ("q_" + cid, json.dumps(payload, ensure_ascii=False), "contradiction"))
            E[n] = v
            meta.append({"id": cid, "claim": it["claim"], "valid_from": vfrom})
            n += 1
            st["contradicted"] += 1
            continue

        # 独立(rel=相近 或 无近邻)→ 常规人工闸 + 写库
        payload = {"id": cid, "type": it.get("type"), "claim": it["claim"],
                   "evidence": it.get("evidence"), "sources": srcs, "impact": it.get("impact"),
                   "confidence": round(conf, 3), "merged": merged}
        if it.get("force_review"):
            con.execute("INSERT OR IGNORE INTO human_queue(id,candidate,reason) VALUES(?,?,?)",
                        ("q_" + cid, json.dumps(payload, ensure_ascii=False), "ai_derived_review"))
            st["queued_review"] += 1
            continue
        if high_impact(it):
            con.execute("INSERT OR IGNORE INTO human_queue(id,candidate,reason) VALUES(?,?,?)",
                        ("q_" + cid, json.dumps(payload, ensure_ascii=False), "high_impact"))
            st["queued_impact"] += 1
            continue
        if conf < 0.12:                                    # 极低分(噪声)不入库
            st["lowconf_skip"] += 1
            continue

        insert_memory(con, cid, it, srcs, conf, vfrom, "待核", [], v)
        E[n] = v
        meta.append({"id": cid, "claim": it["claim"], "valid_from": vfrom})
        n += 1
        st["written"] += 1

    con.commit()
    con.close()
    print("gate 完成: " + json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
