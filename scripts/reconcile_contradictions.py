#!/usr/bin/env python3
"""MemoryHub · 存量回溯 — 补检测矛盾/演化关系,补双时态留痕

背景:旧版 gate 把近邻对当重复盲并/盲丢,从未写过 valid_until/links。
本脚本扫描现行 memory_item 的近邻对(cosine ≥ CONTRADICT_LO),用 qwen 判别
{同义/演化/矛盾/相近},按 gate 同一规则补留痕(不硬删、可追溯、幂等)。

默认 dry-run:只报『待判别近邻对数 + 预计调用次数』+ 抽样预览,不写库。
加 --apply 才真正判别并写库。

用法:
  python3 reconcile_contradictions.py            # 报量 + 抽样预览
  python3 reconcile_contradictions.py --apply     # 全量回溯写库
  python3 reconcile_contradictions.py --apply --limit 200   # 只处理相似度最高的前 N 对
"""
import datetime
import json
import os
import sqlite3
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate  # noqa: E402  复用 unit/judge_pairs/supersede_old/append_link/CONTRADICT_LO

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")


def load_current(con):
    """现行条目(valid_until IS NULL)→ ids, claims, vfroms, links, 单位化向量矩阵 E。"""
    ids, claims, vfroms, links, rows = [], [], [], [], []
    for r in con.execute(
            "SELECT mi.id, mi.claim, mi.valid_from, mi.links, me.dim, me.vec "
            "FROM memory_item mi JOIN memory_embedding me ON me.memory_item_id=mi.id "
            "WHERE mi.valid_until IS NULL"):
        ids.append(r[0]); claims.append(r[1]); vfroms.append(r[2] or "")
        links.append(json.loads(r[3] or "[]"))
        rows.append(gate.unit(struct.unpack(f"<{r[4]}f", r[5])))
    E = np.vstack(rows).astype(np.float32) if rows else np.zeros((0, gate.DIM), np.float32)
    return ids, claims, vfroms, links, E


def linked_set(links_list, ids):
    """已互链的 (a,b) 对集合,用于幂等跳过。"""
    pos = {mid: i for i, mid in enumerate(ids)}
    s = set()
    for i, ls in enumerate(links_list):
        for l in ls:
            j = pos.get(l.get("id"))
            if j is not None:
                s.add((min(i, j), max(i, j)))
    return s


def main():
    apply = "--apply" in sys.argv
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    today = datetime.date.today().isoformat()
    con = sqlite3.connect(DB, timeout=30.0)
    ids, claims, vfroms, links, E = load_current(con)
    m = len(ids)
    print(f"现行条目 {m}。计算两两相似度…")
    if m < 2:
        print("不足两条,结束。"); return

    S = E @ E.T                                   # m×m 余弦(已单位化)
    iu = np.triu_indices(m, k=1)
    mask = S[iu] >= gate.CONTRADICT_LO
    pi, pj = iu[0][mask], iu[1][mask]
    sims = S[pi, pj]
    order = np.argsort(-sims)                     # 相似度高→低
    already = linked_set(links, ids)
    pairs = []
    for k in order:
        a, b = int(pi[k]), int(pj[k])
        if (a, b) in already:
            continue
        pairs.append((a, b, float(sims[k])))
    if limit:
        pairs = pairs[:limit]
    est_calls = (len(pairs) + gate.JUDGE_BATCH - 1) // gate.JUDGE_BATCH
    print(f"待判别近邻对(cosine≥{gate.CONTRADICT_LO}、未互链): {len(pairs)} 对 → 预计 ~{est_calls} 次 qwen 调用")
    if not pairs:
        print("无待处理近邻对,结束。"); return

    if not apply:
        print("\n[dry-run] 抽样预览(判别相似度最高的 5 对,不写库):")
        sample = pairs[:5]
        rels = gate.judge_pairs([(claims[a], claims[b]) for a, b, _ in sample])
        for (a, b, s), rel in zip(sample, rels):
            print(f"  [{rel}] sim={s:.3f}")
            print(f"     A: {claims[a][:70]}")
            print(f"     B: {claims[b][:70]}")
        print("\n→ 确认无误后加 --apply 全量回溯写库。")
        return

    # --apply:批量判别 + 写库
    st = {"evolved": 0, "contradicted": 0, "same_linked": 0, "near": 0}
    superseded = set()
    BATCH = 200
    for off in range(0, len(pairs), BATCH):
        chunk = pairs[off:off + BATCH]
        rels = gate.judge_pairs([(claims[a], claims[b]) for a, b, _ in chunk])
        for (a, b, s), rel in zip(chunk, rels):
            ida, idb = ids[a], ids[b]
            if ida in superseded or idb in superseded:
                continue
            if rel == "演化":
                # 较早者被取代
                if vfroms[a] and vfroms[b] and vfroms[a] < vfroms[b]:
                    old, new = a, b
                else:
                    old, new = b, a
                gate.supersede_old(con, ids[old], today, ids[new])
                gate.append_link(con, ids[new], {"id": ids[old], "rel": "取代"})
                superseded.add(ids[old])
                st["evolved"] += 1
            elif rel == "矛盾":
                gate.append_link(con, ida, {"id": idb, "rel": "矛盾"})
                gate.append_link(con, idb, {"id": ida, "rel": "矛盾"})
                lo, hi = sorted([ida, idb])
                payload = {"id": idb, "claim": claims[b], "conflict_with": ida,
                           "claim_a": claims[a], "sim": round(s, 3)}
                con.execute("INSERT OR IGNORE INTO human_queue(id,candidate,reason) VALUES(?,?,?)",
                            (f"qc_{lo}_{hi}", json.dumps(payload, ensure_ascii=False), "contradiction"))
                st["contradicted"] += 1
            elif rel == "同义":
                gate.append_link(con, ida, {"id": idb, "rel": "重复"})
                gate.append_link(con, idb, {"id": ida, "rel": "重复"})
                st["same_linked"] += 1
            else:
                st["near"] += 1
        con.commit()
        print(f"  已处理 {min(off + BATCH, len(pairs))}/{len(pairs)} 对…")
    con.close()
    print("回溯完成: " + json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
