#!/usr/bin/env python3
"""MemoryHub · 本地记忆面板(P1 只读 + 队列审批)

Flask,绑 127.0.0.1:7788。面板服务在本机;但提纯/嵌入/召回会调用你所配的云端 LLM
(默认阿里云 DashScope)——数据边界见 README「数据去哪儿了」。复用 recall/review_queue/memory.db。
安全(评审落实):
- debug=False、显式 host=127.0.0.1。
- 全请求校验 Host 头白名单(防 DNS rebinding);写端点再要求自定义头 X-MemoryHub:1。
- 不挂任何静态目录到 imports/raw/db/.env;/api/raw 只按 DB 主键查。
契约修正(评审落实):
- 队列审批照搬 review_queue 流程:approve(con, cand["id"], cand) → DELETE qid → commit;reject 落 rejected.jsonl。
- recall 加 FTS-only 降级(嵌入/网络失败不 500)。
- 浏览搜索绕开 recall 的 len>=3 过滤,直接 MATCH+LIKE,放开 2 字中文词。
- 详情溯源走 canonical_document,不直拉 raw。

启动: python3 scripts/web/server.py  → http://127.0.0.1:7788
"""
import contextlib
import glob
import hashlib
import json
import os
import sqlite3
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # scripts/
from flask import Flask, request, jsonify, abort, Response  # noqa: E402

import recall as recall_mod        # noqa: E402  recall.recall(query, topk)
from review_queue import approve as rq_approve  # noqa: E402  approve(con, cid, cand)
from embed import embed_texts, DIM, MODEL, pack_embedding  # noqa: E402  队列降级入库需嵌入

WEB = os.path.dirname(os.path.abspath(__file__))
HUB = os.path.dirname(os.path.dirname(WEB))
DB = os.path.join(HUB, "memory.db")
HOST = os.environ.get("MEMORYHUB_HOST", "127.0.0.1")      # 仅本机;改绑外网请自担风险
PORT = int(os.environ.get("MEMORYHUB_PORT", "7788"))      # 端口被占时可用环境变量改
ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

app = Flask(__name__, static_folder=None)   # 不挂静态目录(防穿越)


def db():
    c = sqlite3.connect(DB, timeout=30)   # 管线持写锁时礼让等待,不 5 秒即 database is locked(审查 P1-1)
    c.row_factory = sqlite3.Row
    return c


@app.before_request
def _guard():
    # Host 头白名单 → 防 DNS rebinding(读写都护)
    if request.headers.get("Host", "") not in ALLOWED_HOSTS:
        abort(403)


def require_write():
    if request.headers.get("X-MemoryHub") != "1":
        abort(403)


def fts_match(q):
    return '"' + q.replace('"', " ") + '"'


def search_ids(c, q):
    """绕开 recall 的 2 字过滤:trigram MATCH(>=3字)+ LIKE 兜底。"""
    ids = []
    if len(q) >= 3:
        try:
            ids = [r[0] for r in c.execute(
                "SELECT mi.id FROM memory_fts JOIN memory_item mi ON mi.rowid=memory_fts.rowid "
                "WHERE memory_fts MATCH ?", (fts_match(q),)).fetchall()]
        except Exception:
            ids = []
    like = [r[0] for r in c.execute(
        "SELECT id FROM memory_item WHERE claim LIKE ? OR evidence LIKE ?",
        (f"%{q}%", f"%{q}%")).fetchall()]
    return list(dict.fromkeys(ids + like))


def nightly_status():
    logs = sorted(glob.glob(os.path.join(HUB, "logs", "nightly-*.log")))
    last = ""
    if logs:
        try:
            last = open(logs[-1], encoding="utf-8").read().strip().split("\n")[-1][:200]
        except Exception:
            pass
    state = "?"
    try:
        out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.memoryhub.nightly"],
                             capture_output=True, text=True, timeout=5).stdout
        for line in out.split("\n"):
            if "state =" in line:
                state = line.split("=", 1)[1].strip()
                break
    except Exception:
        pass
    last_failure = ""
    try:
        fp = os.path.join(HUB, "logs", "last_failure.txt")
        if os.path.exists(fp):
            last_failure = open(fp, encoding="utf-8").read().strip()[:200]
    except Exception:
        pass
    return {"state": state, "last_log": last, "log_count": len(logs),
            "last_failure": last_failure}


# ---------- 页面 ----------
@app.route("/")
def index():
    return Response(open(os.path.join(WEB, "index.html"), encoding="utf-8").read(),
                    mimetype="text/html")


@app.route("/vendor/force-graph.js")
def vendor_force_graph():
    # 单文件白名单:固定绝对路径,无用户输入→无穿越;本地 vendored 离线库
    return Response(open(os.path.join(WEB, "static", "force-graph.min.js"), "rb").read(),
                    mimetype="application/javascript")


# ---------- 只读 API ----------
@app.route("/api/stats")
def api_stats():
    c = db()
    g = lambda q: c.execute(q).fetchone()[0]
    by_type = [dict(r) for r in c.execute(
        "SELECT type, count(*) n FROM memory_item WHERE valid_until IS NULL GROUP BY type ORDER BY n DESC")]
    by_status = [dict(r) for r in c.execute(
        "SELECT status, count(*) n FROM memory_item GROUP BY status ORDER BY n DESC")]
    growth = [dict(r) for r in c.execute(
        "SELECT substr(created_at,1,10) d, count(*) n FROM memory_item GROUP BY d ORDER BY d")]
    src = {}
    for (s,) in c.execute("SELECT sources FROM memory_item WHERE valid_until IS NULL"):
        for x in json.loads(s or "[]"):
            k = x.get("source", "?")
            src[k] = src.get(k, 0) + 1
    raw = [dict(r) for r in c.execute(
        "SELECT source, count(*) n FROM raw_event GROUP BY source ORDER BY n DESC")]
    out = {
        "memory_item": g("SELECT count(*) FROM memory_item WHERE valid_until IS NULL"),
        "memory_total": g("SELECT count(*) FROM memory_item"),
        "raw_event": g("SELECT count(*) FROM raw_event"),
        "queue": g("SELECT count(*) FROM human_queue WHERE status='pending' OR status IS NULL"),
        "by_type": by_type, "by_status": by_status, "by_source": src,
        "raw_sources": raw, "growth": growth, "nightly": nightly_status(),
    }
    c.close()
    return jsonify(out)


def brief(r):
    srcs = json.loads(r["sources"] or "[]")
    return {"id": r["id"], "type": r["type"], "claim": r["claim"],
            "confidence": r["confidence"], "status": r["status"],
            "valid_from": r["valid_from"], "review_date": r["review_date"],
            "sources": [s.get("source") for s in srcs]}


@app.route("/api/memories")
def api_memories():
    a = request.args
    page, size = int(a.get("page", 0)), 30
    where, params = [], []
    if a.get("include_invalid") != "1":
        where.append("valid_until IS NULL")
    if a.get("type"):
        where.append("type=?"); params.append(a["type"])
    if a.get("status"):
        where.append("status=?"); params.append(a["status"])
    if a.get("source"):
        where.append("sources LIKE ?"); params.append(f'%"{a["source"]}"%')
    if a.get("conf_min"):
        where.append("confidence>=?"); params.append(float(a["conf_min"]))
    c = db()
    q = (a.get("q") or "").strip()
    if q:
        ids = search_ids(c, q)
        if ids:
            where.append("id IN (%s)" % ",".join("?" * len(ids))); params += ids
        else:
            where.append("0")
    wsql = " AND ".join(where) if where else "1"
    order = {"conf": "confidence DESC", "new": "created_at DESC",
             "review": "review_date ASC"}.get(a.get("sort"), "confidence DESC")
    total = c.execute(f"SELECT count(*) FROM memory_item WHERE {wsql}", params).fetchone()[0]
    rows = c.execute(
        f"SELECT id,type,claim,confidence,status,sources,valid_from,review_date "
        f"FROM memory_item WHERE {wsql} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [size, page * size]).fetchall()
    c.close()
    return jsonify({"total": total, "page": page, "size": size,
                    "items": [brief(r) for r in rows]})


@app.route("/api/memory/<mid>")
def api_memory(mid):
    c = db()
    r = c.execute("SELECT * FROM memory_item WHERE id=?", (mid,)).fetchone()
    if not r:
        c.close(); abort(404)
    revs = [dict(x) for x in c.execute(
        "SELECT revision_num,change_reason,confidence,status,valid_from,valid_until,created_at "
        "FROM memory_item_revision WHERE memory_item_id=? ORDER BY revision_num", (mid,))]
    srcs = json.loads(r["sources"] or "[]")
    prov = []
    for s in srcs:
        conv = s.get("conv_id")
        doc = None
        if conv:
            d = c.execute("SELECT id,title,ts_start,ts_end FROM canonical_document "
                          "WHERE conv_id=? LIMIT 1", (conv,)).fetchone()
            doc = dict(d) if d else None
        prov.append({"source": s.get("source"), "conv_id": conv,
                     "project": s.get("project"), "uri": s.get("uri"), "doc": doc})
    links = json.loads(r["links"] or "[]")
    for l in links:                                      # 富化:带上对方 claim/type/是否被取代
        t = c.execute("SELECT type,claim,valid_until FROM memory_item WHERE id=?",
                      (l.get("id"),)).fetchone()
        if t:
            l["claim"] = t["claim"]; l["type"] = t["type"]; l["superseded"] = bool(t["valid_until"])
    try:
        neighbors = _neighbors(c, mid)
    except Exception:
        neighbors = []
    c.close()
    return jsonify({"id": r["id"], "type": r["type"], "claim": r["claim"],
                    "context": (r["context"] if "context" in r.keys() else None),
                    "evidence": r["evidence"], "confidence": r["confidence"],
                    "status": r["status"], "valid_from": r["valid_from"],
                    "valid_until": r["valid_until"], "review_date": r["review_date"],
                    "sources": srcs, "provenance": prov, "revisions": revs,
                    "links": links, "neighbors": neighbors})


@app.route("/api/memory/<mid>/source")
def api_memory_source(mid):
    """取该记忆所属对话的整段原文 + 证据定位(供面板"看原文上下文"高亮)。只读。"""
    c = db()
    r = c.execute("SELECT sources,evidence FROM memory_item WHERE id=?", (mid,)).fetchone()
    if not r:
        c.close(); abort(404)
    srcs = json.loads(r["sources"] or "[]")
    conv = next((s.get("conv_id") for s in srcs if s.get("conv_id")), None)
    ev = r["evidence"] or ""
    doc = c.execute("SELECT source,text,ts_start FROM canonical_document WHERE conv_id=? LIMIT 1",
                    (conv,)).fetchone() if conv else None
    c.close()
    if not doc or not doc["text"]:
        return jsonify({"text": None, "conv_id": conv})
    pos = doc["text"].find(ev) if ev else -1
    return jsonify({"text": doc["text"], "evidence_start": pos,
                    "evidence_len": (len(ev) if ev else 0),
                    "source": doc["source"], "ts": doc["ts_start"], "conv_id": conv})


@app.route("/api/recall", methods=["POST"])
def api_recall():
    data = request.get_json(force=True, silent=True) or {}
    q = (data.get("query") or "").strip()
    k = int(data.get("topk", 8))
    if not q:
        return jsonify({"hits": [], "degraded": False})
    try:
        return jsonify({"hits": recall_mod.recall(q, k), "degraded": False})
    except Exception as e:
        # FTS-only 降级:嵌入/网络失败也能用
        c = db()
        ids = search_ids(c, q)[:k]
        hits = []
        for mid in ids:
            r = c.execute("SELECT id,type,claim,evidence,confidence,sources "
                          "FROM memory_item WHERE id=? AND valid_until IS NULL", (mid,)).fetchone()
            if r:
                hits.append({"id": r["id"], "type": r["type"], "claim": r["claim"],
                             "evidence": r["evidence"], "confidence": r["confidence"],
                             "cosine": None,
                             "sources": [x.get("source") for x in json.loads(r["sources"] or "[]")]})
        c.close()
        return jsonify({"hits": hits, "degraded": True, "error": str(e)[:140]})


@app.route("/api/queue")
def api_queue():
    c = db()
    rows = c.execute("SELECT id,candidate,reason,created_at FROM human_queue "
                     "WHERE status='pending' OR status IS NULL ORDER BY created_at").fetchall()
    out = []
    for r in rows:
        cand = json.loads(r["candidate"])
        item = {"qid": r["id"], "reason": r["reason"], "type": cand.get("type"),
                "claim": cand.get("claim"), "context": cand.get("context"),
                "evidence": cand.get("evidence"),
                "confidence": cand.get("confidence"),
                "sources": [s.get("source") for s in cand.get("sources", [])]}
        if r["reason"] == "contradiction":                  # 并排展示冲突双方,供裁决
            other_id = cand.get("conflict_with")
            other = c.execute("SELECT claim,confidence FROM memory_item WHERE id=?",
                              (other_id,)).fetchone() if other_id else None
            item["this_id"] = cand.get("id")
            item["other_id"] = other_id
            item["other_claim"] = cand.get("claim_a") or (other["claim"] if other else None)
            item["other_confidence"] = other["confidence"] if other else None
        out.append(item)
    c.close()
    return jsonify({"items": out})


@app.route("/api/relations")
def api_relations():
    """策展关系图谱:矛盾/演化/重复 的真链接(含被取代条目,前端褪色显示)。"""
    c = db()
    rows = c.execute("SELECT id,type,claim,confidence,status,valid_until,links FROM memory_item "
                     "WHERE links IS NOT NULL AND links NOT IN ('[]','')").fetchall()
    nodes, edges, seen = {}, [], set()

    def add_node(r):
        nodes[r["id"]] = {"id": r["id"], "type": r["type"], "claim": (r["claim"] or "")[:90],
                          "confidence": r["confidence"], "status": r["status"],
                          "superseded": bool(r["valid_until"])}
    for r in rows:
        add_node(r)
        for l in json.loads(r["links"] or "[]"):
            b, rel = l.get("id"), l.get("rel")
            if not b:
                continue
            if rel in ("矛盾", "重复"):                       # 无向
                key = (rel,) + tuple(sorted([r["id"], b]))
                if key not in seen:
                    seen.add(key); edges.append({"a": r["id"], "b": b, "rel": rel})
            elif rel in ("取代", "被取代"):                   # 归一为 老→新 有向演化边
                old, new = (b, r["id"]) if rel == "取代" else (r["id"], b)
                key = ("演化", old, new)
                if key not in seen:
                    seen.add(key); edges.append({"a": old, "b": new, "rel": "演化"})
    # 补全边端点缺失的节点(对方条目)
    for mid in ({e["a"] for e in edges} | {e["b"] for e in edges}) - set(nodes):
        r = c.execute("SELECT id,type,claim,confidence,status,valid_until FROM memory_item "
                      "WHERE id=?", (mid,)).fetchone()
        if r:
            add_node(r)
    c.close()
    cnt = {k: sum(1 for e in edges if e["rel"] == k) for k in ("矛盾", "演化", "重复")}
    cnt["superseded"] = sum(1 for n in nodes.values() if n["superseded"])
    return jsonify({"nodes": list(nodes.values()), "edges": edges, "counts": cnt})


# ---------- 复盘/回溯:主题驱动的认知复盘 ----------
_RETRO = {}
TOPIC_SEED = ["定价", "客单价", "小红书", "变现", "副业", "内容", "选题", "账号", "方向",
              "本地", "模型", "提示词", "RedSkill", "OPC", "培训", "客户", "流量", "矩阵",
              "工作流", "记忆", "视频", "调色", "定位", "成本", "招聘", "找工作"]


def _retro_synth(q, timeline):
    """一次 qwen 合成:种类 + 一句话 + 转折(为什么变) + 张力 + 尖锐判断问题。失败给安全默认。"""
    from distill import call_qwen, parse                       # scripts/ 已在 path
    lines = "\n".join(
        f"{i}. [{t['date']}][{t['type']}]{'(已被取代)' if t['superseded'] else ''} {t['claim']}"
        for i, t in enumerate(timeline))
    prompt = (
        f"你在帮用户复盘他自己关于「{q}」的思考。下面是他的相关记忆,已按时间排序(序号仅供你内部定位)。\n"
        "请用第二人称(\"你…\")。所有文字都要自成一句人话、能独立读懂;"
        "严禁在任何文字里出现\"记忆5/第3条/记忆3、12\"这类编号引用(序号只允许填进 turns 的 after 字段)。\n"
        "只输出严格 JSON,不要任何解释:\n"
        "{\n"
        '"kind":"矛盾|演化|精炼|一致",'
        "  // 观点对立=矛盾;结论随时间更新换代=演化;方向没变只是越来越细=精炼;基本一致=一致\n"
        '"summary":"一句话说清整体是怎么回事",\n'
        '"turns":[{"after":序号,"why":"在这条之后你的想法变了,为什么变(从记忆本身推断触发原因)"}],'
        "  // 0-3 个关键转折,没有就空数组\n"
        '"tension":{"a":"一种立场","b":"对立或不同的立场","note":"为什么看似冲突,以及能否共存"},'
        "  // 没有明显张力则为 null\n"
        '"questions":["2-4 个尖锐、具体的问题,逼你自己做判断,绝不替你下结论"]\n'
        "}\n\n记忆:\n" + lines)
    try:
        d = parse(call_qwen("qwen3-max", prompt))
        if isinstance(d, list):
            d = d[0] if d else {}
        return {"kind": d.get("kind"), "summary": d.get("summary"),
                "turns": d.get("turns") or [], "tension": d.get("tension"),
                "questions": d.get("questions") or []}
    except Exception as e:
        return {"kind": None, "summary": None, "turns": [], "tension": None,
                "questions": [], "error": str(e)[:120]}


@app.route("/api/retrospect")
def api_retrospect():
    q = (request.args.get("q") or "").strip()
    if not q:
        abort(400)
    c = db()

    def opaque(cl):                                        # claude-memory 的无意义标题(如"Claude项目记忆 · 019c"),排除
        return bool(cl) and cl.startswith("Claude") and "记忆" in cl[:14]

    kwrows = c.execute("SELECT id,claim FROM memory_item WHERE valid_until IS NULL "
                       "AND claim LIKE ? ORDER BY valid_from", (f"%{q}%",)).fetchall()
    kw = [r["id"] for r in kwrows if not opaque(r["claim"])]
    if len(kw) > 25:                                       # 太多 → 沿时间均匀抽样,保留演化弧
        step = len(kw) / 25.0
        kw = [kw[int(i * step)] for i in range(25)]
    try:                                                   # 语义补充:加余弦地板丢掉漂移尾部 + 过滤 opaque
        sem = [h["id"] for h in recall_mod.recall(q, 25)
               if (h.get("cosine") or 0) >= 0.45 and not opaque(h.get("claim"))]
    except Exception:
        sem = search_ids(c, q)[:20]
    ids = list(dict.fromkeys(kw + sem))[:28]
    seen, rows = set(), []

    def load(mid):
        if not mid or mid in seen:
            return
        seen.add(mid)
        r = c.execute("SELECT id,type,claim,confidence,valid_from,valid_until,links "
                      "FROM memory_item WHERE id=?", (mid,)).fetchone()
        if r:
            rows.append(r)
    for mid in ids:
        load(mid)
    for r in list(rows):                                       # 顺 links 补全演化链/矛盾对(含被取代),重复链不补
        for l in json.loads(r["links"] or "[]"):
            if l.get("rel") in ("取代", "被取代", "矛盾"):
                load(l.get("id"))
    c.close()
    if not rows:
        return jsonify({"q": q, "empty": True})
    rows.sort(key=lambda r: (r["valid_from"] or ""))
    timeline = [{"id": r["id"], "type": r["type"], "claim": r["claim"],
                 "confidence": r["confidence"], "date": (r["valid_from"] or "")[:10],
                 "superseded": bool(r["valid_until"])} for r in rows]
    key = (q, len(timeline))
    if key in _RETRO:
        return jsonify(_RETRO[key])
    out = {"q": q, "count": len(timeline), "timeline": timeline, **_retro_synth(q, timeline)}
    _RETRO[key] = out
    return jsonify(out)


@app.route("/api/topics")
def api_topics():
    """建议主题卡:对种子词扫现行 claim,有张力(矛盾/取代)的主题优先。"""
    from collections import Counter
    c = db()
    rows = c.execute("SELECT claim,links FROM memory_item WHERE valid_until IS NULL").fetchall()
    c.close()
    cnt, tcnt = Counter(), Counter()
    for r in rows:
        cl = r["claim"] or ""
        has_contra = "矛盾" in (r["links"] or "")     # 只算真矛盾,不含演化/重复,避免全标
        for t in TOPIC_SEED:
            if t in cl:
                cnt[t] += 1
                if has_contra:
                    tcnt[t] += 1
    topics = [{"label": t, "count": n, "tension": tcnt[t] >= 2}   # ≥2 条真矛盾才算"有张力"
              for t, n in cnt.most_common(16) if n >= 4]
    topics.sort(key=lambda x: (not x["tension"], -x["count"]))
    return jsonify({"topics": topics[:12]})


@app.route("/api/sources")
def api_sources():
    c = db()
    rows = [dict(r) for r in c.execute(
        "SELECT source, count(*) n FROM raw_event GROUP BY source ORDER BY n DESC")]
    c.close()
    try:
        gate = json.load(open(os.path.join(HUB, "sources.json"), encoding="utf-8"))
    except Exception:
        gate = {}
    return jsonify({"raw_sources": rows, "gate": gate})


@app.route("/api/raw/<rid>")
def api_raw(rid):
    c = db()
    r = c.execute("SELECT id,source,project,conv_id,ts,role,text FROM raw_event WHERE id=?",
                  (rid,)).fetchone()
    c.close()
    if not r:
        abort(404)
    return jsonify(dict(r))


_EMB = {"n": -1, "ids": None, "M": None}


def _emb_matrix(con):
    """现行记忆的归一化向量矩阵,按条数缓存。供语义近邻复用。"""
    import numpy as np
    rows = con.execute(
        "SELECT me.memory_item_id mid, me.dim, me.vec FROM memory_embedding me "
        "JOIN memory_item mi ON mi.id=me.memory_item_id WHERE mi.valid_until IS NULL").fetchall()
    if _EMB["n"] == len(rows) and _EMB["M"] is not None:
        return _EMB["ids"], _EMB["M"]
    ids = [r["mid"] for r in rows]
    M = np.array([struct.unpack(f"<{r['dim']}f", r["vec"]) for r in rows]) if rows else np.zeros((0, 1))
    M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    _EMB.update({"n": len(rows), "ids": ids, "M": M})
    return ids, M


def _neighbors(con, mid, top=5):
    """某条记忆的语义最近邻(余弦),带 sim。"""
    import numpy as np
    ids, M = _emb_matrix(con)
    if mid not in ids:
        return []
    sims = M @ M[ids.index(mid)]
    picks = []
    for oi in np.argsort(-sims):
        if ids[oi] != mid:
            picks.append((ids[oi], float(sims[oi])))
        if len(picks) >= top:
            break
    qids = [i for i, _ in picks]
    if not qids:
        return []
    meta = {r["id"]: r for r in con.execute(
        "SELECT id,type,claim,confidence FROM memory_item WHERE id IN (%s)"
        % ",".join("?" * len(qids)), qids)}
    return [{"id": i, "type": meta[i]["type"], "claim": meta[i]["claim"],
             "confidence": meta[i]["confidence"], "sim": round(s, 3)}
            for i, s in picks if i in meta]


_MAP_CACHE = {"n": -1, "data": None}


def _cluster_labels(rows, assign, k):
    """每簇用 jieba TF-IDF 抽 2-3 关键词当主题名。"""
    try:
        import jieba.analyse as ja
    except Exception:
        ja = None
    out = {}
    for j in range(k):
        claims = " ".join(rows[i]["claim"] for i in range(len(rows)) if assign[i] == j)
        kw = ja.extract_tags(claims, topK=3) if (ja and claims.strip()) else []
        out[j] = "·".join(kw) if kw else f"主题{j + 1}"
    return out


def _llm_cluster_names(samples):
    """一次调用 qwen3-max 给所有簇起人话主题名;失败返回 {}(回退 jieba)。"""
    import re
    import requests
    from embed import _alibaba_key
    lines = [f"{cid}: " + " / ".join(c[:42] for c in claims) for cid, claims in samples]
    prompt = ("下面每行是一组语义相近的记忆结论(行首是簇号)。给每个簇起一个 4–12 字、精准具体的中文主题名"
              "(避免'系统/方法/AI'这类泛词)。只输出 JSON 对象,键=簇号字符串、值=主题名,无任何解释。\n\n"
              + "\n".join(lines))
    try:
        r = requests.post(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
            headers={"Authorization": f"Bearer {_alibaba_key()}", "Content-Type": "application/json"},
            json={"model": "qwen3-max",
                  "input": {"messages": [{"role": "user", "content": prompt}]},
                  "parameters": {"result_format": "message"}}, timeout=45)
        txt = r.json()["output"]["choices"][0]["message"]["content"]
        d = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        return {int(k): str(v).strip() for k, v in d.items()}
    except Exception:
        return {}


def _layout_cluster(X, Xn):
    """返回 (assign, pos2d, C, k)。优先 UMAP 布局 + HDBSCAN 聚类(噪声=-1);失败回退 kmeans+PCA。"""
    import numpy as np
    N = X.shape[0]
    try:
        import umap
        from sklearn.cluster import HDBSCAN
        pos = np.asarray(umap.UMAP(n_components=2, metric="cosine", random_state=42,
                                   n_neighbors=15, min_dist=0.12).fit_transform(X), dtype=float)
        emb5 = umap.UMAP(n_components=5, metric="cosine", random_state=42,
                         n_neighbors=15).fit_transform(X)
        raw = HDBSCAN(min_cluster_size=max(10, N // 55), min_samples=5).fit_predict(emb5)
        real = sorted(set(int(l) for l in raw if l >= 0))
        if len(real) >= 2:
            remap = {o: i for i, o in enumerate(real)}
            assign = np.array([remap[int(l)] if int(l) >= 0 else -1 for l in raw])
            C = np.array([Xn[assign == j].mean(0) for j in range(len(real))])
            C = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-9)
            return assign, pos, C, len(real)
    except Exception:
        pass
    k = max(5, min(24, N // 70))                          # 回退:kmeans++ + 质心混合 PCA(主题数随数据自适应)
    rng = np.random.default_rng(42)
    centers = [Xn[rng.integers(N)]]
    for _ in range(k - 1):
        d = np.min(np.stack([((Xn - c) ** 2).sum(1) for c in centers]), axis=0)
        s = d.sum()
        centers.append(Xn[rng.choice(N, p=(d / s) if s > 0 else None)])
    C = np.array(centers)
    for _ in range(30):
        assign = (Xn @ C.T).argmax(1)
        newC = np.array([Xn[assign == j].mean(0) if (assign == j).any() else C[j] for j in range(k)])
        newC = newC / (np.linalg.norm(newC, axis=1, keepdims=True) + 1e-9)
        if np.allclose(newC, C):
            C = newC
            break
        C = newC
    assign = (Xn @ C.T).argmax(1)
    mean = Xn.mean(0)
    _, _, Vt = np.linalg.svd(Xn - mean, full_matrices=False)
    pos = 0.62 * ((C - mean) @ Vt[:2].T)[assign] + 0.38 * ((Xn - mean) @ Vt[:2].T)
    return assign, pos, C, k


def _build_map(rows):
    """聚类(UMAP+HDBSCAN,回退 kmeans)+ LLM 命名 + 近邻连线 + 盲区。"""
    import numpy as np
    X = np.asarray([struct.unpack(f"<{r['dim']}f", r["vec"]) for r in rows], dtype=float)
    N = X.shape[0]
    Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    assign, pos, C, k = _layout_cluster(X, Xn)
    mn, mx = pos.min(0), pos.max(0)
    pos = (pos - mn) / np.where(mx - mn == 0, 1.0, mx - mn)
    # 近邻边:每点连最像的一条,sim>0.6,去重
    S = Xn @ Xn.T
    np.fill_diagonal(S, -1)
    nn, nns = S.argmax(1), S.max(1)
    seen, edges = set(), []
    for i in range(N):
        if nns[i] < 0.6:
            continue
        a, b = (i, int(nn[i])) if i < nn[i] else (int(nn[i]), i)
        if (a, b) not in seen:
            seen.add((a, b))
            edges.append([a, b])
    labels = _cluster_labels(rows, assign, k)
    samples = []                                            # 每簇取最靠质心的 5 条喂 LLM 命名
    for j in range(k):
        idx = np.where(assign == j)[0]
        if len(idx):
            top = idx[np.argsort(-(Xn[idx] @ C[j]))[:5]]
            samples.append((j, [rows[i]["claim"] for i in top]))
    for j, name in _llm_cluster_names(samples).items():
        if name:
            labels[j] = name
    # 盲区/结构洞:用 top-8 邻域关联(比 top-1 边更准)。质心相近却几乎不互为邻域 = 各自成体系
    Csim = C @ C.T
    topk = np.argsort(-S, axis=1)[:, :8]
    assoc = np.zeros((k, k))
    for i in range(N):
        ci = int(assign[i])
        if ci < 0:
            continue
        for j in topk[i]:
            cj = int(assign[j])
            if cj >= 0 and ci != cj:
                assoc[ci, cj] += 1
    gaps = []
    for i in range(k):
        for j in range(i + 1, k):
            a = int(assoc[i, j] + assoc[j, i])
            if float(Csim[i, j]) >= 0.3 and a <= 2:
                gaps.append({"a": int(i), "b": int(j), "sim": round(float(Csim[i, j]), 3), "cross": a})
    gaps.sort(key=lambda g: (g["cross"], -g["sim"]))
    isolated = int((assign == -1).sum()) or int((nns < 0.6).sum())
    clusters = []
    for j in range(k):
        idx = np.where(assign == j)[0]
        if len(idx):
            clusters.append({"id": int(j), "label": labels.get(j, f"主题{j+1}"),
                             "size": int(len(idx)),
                             "x": round(float(pos[idx, 0].mean()), 4),
                             "y": round(float(pos[idx, 1].mean()), 4)})
    pts = [{"id": rows[i]["id"], "type": rows[i]["type"], "claim": rows[i]["claim"],
            "confidence": rows[i]["confidence"], "cluster": int(assign[i]),
            "x": round(float(pos[i, 0]), 4), "y": round(float(pos[i, 1]), 4),
            "nbr": [[int(j), round(float(S[i, j]), 3)] for j in topk[i][:5] if S[i, j] >= 0.45]}
           for i in range(N)]
    return {"points": pts, "clusters": clusters, "edges": edges,
            "gaps": gaps[:6], "isolated": isolated, "count": N, "k": int(k)}


@app.route("/api/map")
def api_map():
    c = db()
    rows = c.execute(
        "SELECT mi.id, mi.type, mi.claim, mi.confidence, me.dim, me.vec "
        "FROM memory_item mi JOIN memory_embedding me ON me.memory_item_id=mi.id "
        "WHERE mi.valid_until IS NULL").fetchall()
    c.close()
    if _MAP_CACHE["n"] == len(rows) and _MAP_CACHE["data"]:
        return jsonify(_MAP_CACHE["data"])
    if len(rows) < 6:
        return jsonify({"points": [], "clusters": [], "edges": [],
                        "count": len(rows), "note": "记忆太少,无法成图"})
    try:
        out = _build_map(rows)
    except Exception as e:
        return jsonify({"points": [], "clusters": [], "edges": [],
                        "count": len(rows), "note": f"成图失败: {str(e)[:140]}"})
    _MAP_CACHE.update({"n": len(rows), "data": out})
    return jsonify(out)


@app.route("/api/map-timeline")
def api_map_timeline():
    """主题演化时间线:每主题按月的记忆形成量(搭车 _MAP_CACHE 的聚类结果)。"""
    data = _MAP_CACHE.get("data") or {}
    pts = data.get("points") or []
    if not pts:
        return jsonify({"months": [], "series": [],
                        "note": "先打开「地图」等聚类完成,再看主题演化。"})
    cl_of = {p["id"]: p["cluster"] for p in pts if p.get("cluster", -1) >= 0}
    labels = {cl["id"]: cl["label"] for cl in (data.get("clusters") or [])}
    c = db()
    rows = c.execute("SELECT id, substr(valid_from,1,7) FROM memory_item "
                     "WHERE valid_until IS NULL AND valid_from IS NOT NULL").fetchall()
    c.close()
    months, buckets = set(), {}
    for mid, m in rows:
        ci = cl_of.get(mid)
        if ci is None or not m or len(m) < 7:
            continue
        months.add(m)
        buckets.setdefault(ci, {})
        buckets[ci][m] = buckets[ci].get(m, 0) + 1
    months = sorted(months)[-24:]                       # 最多看最近 24 个月
    series = [{"id": ci, "label": labels.get(ci, f"主题{ci}"),
               "total": sum(mb.values()),
               "counts": [mb.get(m, 0) for m in months]}
              for ci, mb in buckets.items()]
    series.sort(key=lambda s: -s["total"])
    return jsonify({"months": months, "series": series})


@app.route("/api/health")
def api_health():
    c = db()
    g = lambda q: c.execute(q).fetchone()[0]
    by_status = [dict(r) for r in c.execute(
        "SELECT status, count(*) n FROM memory_item WHERE valid_until IS NULL GROUP BY status ORDER BY n DESC")]
    low_src = {}
    for (s,) in c.execute("SELECT sources FROM memory_item WHERE valid_until IS NULL AND confidence<0.45"):
        for x in json.loads(s or "[]"):
            k = x.get("source", "?")
            low_src[k] = low_src.get(k, 0) + 1
    lowest = [{"id": r["id"], "type": r["type"], "claim": r["claim"], "confidence": r["confidence"]}
              for r in c.execute("SELECT id,type,claim,confidence FROM memory_item "
                                 "WHERE valid_until IS NULL ORDER BY confidence ASC LIMIT 12")]
    try:
        db_mb = round(os.path.getsize(DB) / 1048576, 1)
    except Exception:
        db_mb = None
    out = {
        "current": g("SELECT count(*) FROM memory_item WHERE valid_until IS NULL"),
        "superseded": g("SELECT count(*) FROM memory_item WHERE valid_until IS NOT NULL"),
        "low_conf": g("SELECT count(*) FROM memory_item WHERE valid_until IS NULL AND confidence<0.45"),
        "stale": g("SELECT count(*) FROM memory_item WHERE valid_until IS NULL AND review_date<date('now')"),
        "by_status": by_status, "low_by_source": low_src, "lowest": lowest,
        "db_size_mb": db_mb,
    }
    c.close()
    return jsonify(out)


# ---------- 写动作:队列审批 ----------
@app.route("/api/queue/<qid>", methods=["POST"])
def api_queue_action(qid):
    require_write()
    action = (request.get_json(force=True, silent=True) or {}).get("action")
    c = db()
    # 只对仍待处理的行动作:防对已 rejected/approved 的陈旧 qid 再 POST approve 把它复活
    row = c.execute("SELECT candidate FROM human_queue WHERE id=? "
                    "AND (status='pending' OR status IS NULL)", (qid,)).fetchone()
    if not row:
        c.close(); abort(404)
    cand = json.loads(row["candidate"])
    if action == "approve":
        try:
            rq_approve(c, cand["id"], cand)        # 内部嵌入 + 写库,不 commit
        except Exception as e:
            c.close()
            return jsonify({"ok": False, "error": str(e)[:160]}), 500
        c.execute("DELETE FROM human_queue WHERE id=?", (qid,))
        c.commit()
    elif action == "reject":
        os.makedirs(os.path.join(HUB, "staging"), exist_ok=True)
        with open(os.path.join(HUB, "staging", "rejected.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(cand, ensure_ascii=False) + "\n")
        # 不删行:改状态留档。行的 q_ id 留在 gate 的 seen 里 → 被拒候选不会每晚复活(审查 P0-B)。
        c.execute("UPDATE human_queue SET status='rejected', resolved_at=datetime('now') WHERE id=?", (qid,))
        c.commit()
    elif action in ("keep_this", "keep_other", "coexist"):   # 矛盾裁决:取代败者 / 都保留
        import datetime
        today = datetime.date.today().isoformat()
        loser = cand.get("conflict_with") if action == "keep_this" else \
            (cand.get("id") if action == "keep_other" else None)
        if loser:
            rev = c.execute("SELECT COALESCE(MAX(revision_num),0)+1 FROM memory_item_revision "
                            "WHERE memory_item_id=?", (loser,)).fetchone()[0]
            c.execute("INSERT OR IGNORE INTO memory_item_revision"
                      "(id,memory_item_id,revision_num,status,valid_until,change_reason) "
                      "VALUES(?,?,?,?,?,?)",
                      (f"{loser}-r{rev}", loser, rev, "已被取代", today, "contradiction_resolved"))
            c.execute("UPDATE memory_item SET valid_until=?, status='已被取代' WHERE id=?", (today, loser))
        c.execute("DELETE FROM human_queue WHERE id=?", (qid,))
        c.commit()
    else:
        c.close(); abort(400)
    c.close()
    return jsonify({"ok": True})


@app.route("/api/cleanup", methods=["POST"])
def api_cleanup():
    """按来源 + 低置信批量退役(置 valid_until + 归档 + 写 revision,不硬删)。"""
    require_write()
    import datetime
    data = request.get_json(force=True, silent=True) or {}
    source = (data.get("source") or "").strip()
    max_conf = float(data.get("max_conf", 0.45))
    if not source:
        abort(400)
    today = datetime.date.today().isoformat()
    c = db()
    ids = [r["id"] for r in c.execute(
        "SELECT id FROM memory_item WHERE valid_until IS NULL AND confidence < ? AND sources LIKE ?",
        (max_conf, f'%"{source}"%')).fetchall()]
    for mid in ids:
        rev = c.execute("SELECT COALESCE(MAX(revision_num),0)+1 FROM memory_item_revision "
                        "WHERE memory_item_id=?", (mid,)).fetchone()[0]
        c.execute("INSERT OR IGNORE INTO memory_item_revision"
                  "(id,memory_item_id,revision_num,status,valid_until,change_reason) "
                  "VALUES(?,?,?,?,?,?)", (f"{mid}-r{rev}", mid, rev, "休眠", today, "cleanup_low_conf"))
        c.execute("UPDATE memory_item SET valid_until=?, status='休眠' WHERE id=?", (today, mid))
    c.commit()
    c.close()
    return jsonify({"ok": True, "retired": len(ids)})


def _lifecycle(c, mid, action):
    """逐条生命周期跃迁(双向)。返回 True/False。不 commit。
    enable=待核→已确认(+×1.25,确保现行)· sleep=现行→休眠 · wake=休眠/已被取代→现行(待核)。"""
    import datetime
    today = datetime.date.today().isoformat()
    row = c.execute("SELECT status, confidence FROM memory_item WHERE id=?", (mid,)).fetchone()
    if not row:
        return False
    rev = c.execute("SELECT COALESCE(MAX(revision_num),0)+1 FROM memory_item_revision "
                    "WHERE memory_item_id=?", (mid,)).fetchone()[0]
    if action == "enable":
        conf = row["confidence"] or 0.56
        if row["status"] != "已确认":
            conf = min(1.0, round(conf * 1.25, 3))
        c.execute("UPDATE memory_item SET status='已确认', confidence=?, valid_until=NULL WHERE id=?",
                  (conf, mid))
        st, vu, reason = "已确认", None, "human_confirmed"
    elif action == "sleep":
        c.execute("UPDATE memory_item SET status='休眠', valid_until=? WHERE id=?", (today, mid))
        st, vu, reason = "休眠", today, "manual_sleep"
    elif action == "wake":
        c.execute("UPDATE memory_item SET status='待核', valid_until=NULL WHERE id=?", (mid,))
        st, vu, reason = "待核", None, "manual_wake"
    else:
        return None
    c.execute("INSERT OR IGNORE INTO memory_item_revision"
              "(id,memory_item_id,revision_num,status,valid_until,change_reason) "
              "VALUES(?,?,?,?,?,?)", (f"{mid}-r{rev}", mid, rev, st, vu, reason))
    return True


@app.route("/api/memory/<mid>/<action>", methods=["POST"])
def api_memory_lifecycle(mid, action):
    if action not in ("enable", "sleep", "wake"):
        abort(400)
    require_write()
    c = db()
    r = _lifecycle(c, mid, action)
    if r is False:
        c.close(); abort(404)
    c.commit(); c.close()
    return jsonify({"ok": True})


@app.route("/api/memory/bulk", methods=["POST"])
def api_memory_bulk():
    """批量生命周期跃迁。body: {ids:[...], action: enable|sleep|wake}"""
    require_write()
    data = request.get_json(force=True, silent=True) or {}
    action = (data.get("action") or "").strip()
    ids = data.get("ids") or []
    if action not in ("enable", "sleep", "wake") or not isinstance(ids, list):
        abort(400)
    c = db()
    done = sum(1 for mid in ids if _lifecycle(c, mid, action))
    c.commit(); c.close()
    return jsonify({"ok": True, "done": done})


@app.route("/api/queue/clusters")
def api_queue_clusters():
    """读 queue_triage.py 产出的聚类预审,只保留仍 pending 的 qid。"""
    path = os.path.join(HUB, "staging", "queue_triage.json")
    if not os.path.exists(path):
        return jsonify({"ready": False, "clusters": []})
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return jsonify({"ready": False, "clusters": []})
    c = db()
    pend = {r["id"] for r in c.execute("SELECT id FROM human_queue WHERE status='pending'")}
    c.close()
    out = []
    for cl in data:
        qids = [q for q in cl.get("qids", []) if q in pend]
        if qids:
            out.append({**cl, "qids": qids, "size": len(qids)})
    out.sort(key=lambda x: -x["size"])
    return jsonify({"ready": True, "clusters": out})


def _queue_downgrade(c, cand):
    """队列候选 → 普通【待核】记忆(移出队列,不占人工闸,仍保留)。"""
    cid = cand.get("id")
    if not cid or c.execute("SELECT 1 FROM memory_item WHERE id=?", (cid,)).fetchone():
        return
    import datetime
    vec = embed_texts([cand["claim"]])[0]
    ts = max((s.get("ts") or "" for s in cand.get("sources", [])), default="")
    review = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    rev = cid + "-r1"
    merged = cand.get("merged") or len(cand.get("sources") or []) or 1   # 质量分(去时效,与 Phase① 同口径)
    conf = round(min(1.0, 0.7 * 0.8 * (1.0 + min(0.25, 0.05 * (merged - 1)))), 3)
    c.execute(
        "INSERT OR IGNORE INTO memory_item(id,type,claim,evidence,sources,confidence,valid_from,"
        "valid_until,status,review_date,links,content_hash,current_revision_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, cand.get("type"), cand["claim"], cand.get("evidence"),
         json.dumps(cand.get("sources", []), ensure_ascii=False),
         conf, (ts[:10] or None), None, "待核", review, "[]", cid, rev))
    c.execute("INSERT OR IGNORE INTO memory_item_revision(id,memory_item_id,revision_num,claim,"
              "evidence,sources,confidence,valid_from,status,change_reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (rev, cid, 1, cand["claim"], cand.get("evidence"),
               json.dumps(cand.get("sources", []), ensure_ascii=False),
               conf, (ts[:10] or None), "待核", "downgraded_from_queue"))
    c.execute("INSERT OR IGNORE INTO memory_embedding(memory_item_id,model,dim,vec) VALUES(?,?,?,?)",
              (cid,) + pack_embedding(vec))


@app.route("/api/queue/bulk", methods=["POST"])
def api_queue_bulk():
    """按簇批量处置队列。body: {qids:[...], action: approve|downgrade|reject}"""
    require_write()
    data = request.get_json(force=True, silent=True) or {}
    action = (data.get("action") or "").strip()
    qids = data.get("qids") or []
    if action not in ("approve", "downgrade", "reject") or not isinstance(qids, list):
        abort(400)
    c = db()
    rej = open(os.path.join(HUB, "staging", "rejected.jsonl"), "a", encoding="utf-8") \
        if action == "reject" else None
    done = 0
    for qid in qids:
        row = c.execute("SELECT candidate FROM human_queue WHERE id=? AND status='pending'", (qid,)).fetchone()
        if not row:
            continue
        cand = json.loads(row["candidate"])
        try:
            if action == "approve":
                rq_approve(c, cand["id"], cand)
            elif action == "downgrade":
                _queue_downgrade(c, cand)
            elif action == "reject":
                rej.write(json.dumps(cand, ensure_ascii=False) + "\n")
        except Exception:
            continue
        if action == "reject":     # 拒绝改状态留档,不删行(seen 保留 → 不复活,审查 P0-B)
            c.execute("UPDATE human_queue SET status='rejected', resolved_at=datetime('now') WHERE id=?", (qid,))
        else:                      # approve/downgrade 的 claim 已进 memory_item,seen 自会拦,删行安全
            c.execute("DELETE FROM human_queue WHERE id=?", (qid,))
        done += 1
    if rej:
        rej.close()
    c.commit(); c.close()
    return jsonify({"ok": True, "done": done})


# ---------- 接入 AI / 检查更新 / 一键导入 ----------
GITHUB_REPO = os.environ.get("MEMORYHUB_REPO", "Shiye-10Pages/MemoryHub")


def _alibaba_key():
    """当前 provider 的可用密钥(嵌入优先);用于导入前预检。现读 .env,填完即时生效。"""
    import provider
    c = provider.resolve()
    return c["embed_key"] or c["key"]


def _set_env(updates):
    """把 updates 写回 .env(存在的键改值,不存在的追加),保留其它行与注释。"""
    updates = {k: "".join(c for c in str(v) if c == "\t" or ord(c) >= 32)
               for k, v in updates.items()}   # 去除换行等控制符,防 .env 行注入
    p = os.path.join(HUB, ".env")
    lines = open(p, encoding="utf-8").read().splitlines() if os.path.exists(p) else []
    keys, out, seen = set(updates), [], set()
    for line in lines:
        k = line.split("=", 1)[0].strip() if ("=" in line and not line.strip().startswith("#")) else None
        if k in keys:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            out.append(line)
    for k in keys - seen:
        out.append(f"{k}={updates[k]}")
    open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")


def _vgt(a, b):
    """语义化版本比较:a > b ?"""
    def t(v):
        out = []
        for x in str(v).split("."):
            d = "".join(ch for ch in x if ch.isdigit())
            out.append(int(d) if d else 0)
        return out
    return t(a) > t(b)


@app.route("/api/connect-info")
def api_connect_info():
    py = sys.executable
    mcp = os.path.join(HUB, "scripts", "mcp_server.py")
    instruction = (
        "当 recall_memory 工具可用时,凡涉及【事实 / 既定决策 / 业务实情 / 我的偏好或原则 / 过往结论】的问题,"
        "先调用 recall_memory 查 MemoryHub,并以它返回的【逐字证据 + 来源 + 置信度】为准;"
        "与你自带的模糊印象冲突时,信 MemoryHub 并向我指出冲突点;查不到才用你自己的知识,"
        "且须说明这是未经核实的印象、非权威。"
    )
    return jsonify({
        "python_path": py,
        "mcp_script": mcp,
        "mcp_json": {"mcpServers": {"memoryhub": {"command": py, "args": [mcp]}}},
        "cli_command": f"claude mcp add memoryhub -- {py} {mcp}",
        "instruction": instruction,
    })


@app.route("/api/update-check")
def api_update_check():
    import datetime
    import urllib.request
    now = datetime.datetime.now()
    try:
        cur = open(os.path.join(HUB, "VERSION"), encoding="utf-8").read().strip()
    except Exception:
        cur = "0.0.0"
    cache = os.path.expanduser("~/.cache/memoryhub/update_check.json")
    try:                                                   # 按天缓存,避免频繁请求 / 限流
        cached = json.load(open(cache, encoding="utf-8"))
        t = datetime.datetime.fromisoformat(cached.get("checked_at", ""))
        if (now - t).total_seconds() < 86400:
            cached["current"] = cur                        # 版本可能已变(如刚一键更新):用缓存的 latest 重算
            cached["update_available"] = _vgt(cached.get("latest") or "0", cur)
            return jsonify(cached)
    except Exception:
        pass
    res = {"current": cur, "latest": None, "update_available": False, "url": None,
           "checked_at": now.isoformat()}
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"User-Agent": "MemoryHub-update-check",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310  仅 github api
            data = json.loads(r.read().decode("utf-8"))
        latest = (data.get("tag_name") or "").lstrip("v")
        if latest:
            res.update(latest=latest, url=data.get("html_url"),
                       update_available=_vgt(latest, cur))
    except Exception:
        pass                                               # 离线 / 无 release(404)/ 限流 → 静默
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        json.dump(res, open(cache, "w", encoding="utf-8"))
    except Exception:
        pass
    return jsonify(res)


def _git(args, timeout=60):
    p = subprocess.run(["git", "-C", HUB] + args, capture_output=True,
                       text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def _restart_soon():
    """响应发出后重启:起分离子进程接班,父进程退位释放端口。

    不用 os.execv:werkzeug 会把监听 socket 设为可继承(serving.py 里
    set_inheritable(True)),execv 后旧 fd 仍占着端口 → 新进程 EADDRINUSE。
    Popen 默认 close_fds=True,子进程不带旧 fd;父进程退出后端口释放,
    子进程靠 __main__ 的 bind 重试完成交接。"""
    import threading

    def _go():
        time.sleep(1.2)
        env = {k: v for k, v in os.environ.items()
               if k not in ("WERKZEUG_SERVER_FD", "WERKZEUG_RUN_MAIN")}
        try:
            subprocess.Popen([sys.executable, os.path.join(WEB, "server.py")],
                             cwd=HUB, env=env, start_new_session=True)
        except Exception:
            os._exit(3)   # 起不来接班进程:退出,交给启动器/用户重开
        time.sleep(0.8)
        os._exit(0)       # 父进程退位 → 监听端口释放
    threading.Thread(target=_go, daemon=True).start()


@app.route("/api/update-apply", methods=["POST"])
def api_update_apply():
    require_write()
    import shutil
    gh = "https://github.com/" + GITHUB_REPO
    if not os.path.isdir(os.path.join(HUB, ".git")):
        return jsonify(ok=False, url=gh,
                       message="当前不是 git 安装(可能是下载的 zip 包),无法一键更新。请到 GitHub 下载最新版覆盖,或用 git clone 重装。")
    if not shutil.which("git"):
        return jsonify(ok=False, url=gh,
                       message="系统未安装 git,无法一键更新。装好 git 后重试,或手动到 GitHub 下载最新版。")
    try:
        rc, dirty = _git(["status", "--porcelain"])
        if rc == 0 and dirty:
            return jsonify(ok=False,
                           message="检测到你本地改动过代码(未提交),一键更新已中止以免覆盖你的改动。请先 git stash / commit,或手动更新。")
        rc, out = _git(["pull", "--ff-only"], timeout=120)
        if rc != 0:
            return jsonify(ok=False, url=gh,
                           message="拉取更新失败:" + (out or "未知错误") + "。可到 GitHub 手动更新。")
    except Exception as e:
        return jsonify(ok=False, url=gh, message="更新出错:" + str(e))
    req = os.path.join(HUB, "requirements.txt")   # 依赖有变则补装
    dep_warn = ""
    if os.path.exists(req):
        # 与 setup.sh 一致:多镜像兜底,任一成功即止;全失败则警示(否则重启后可能起不来)
        mirrors = ["https://pypi.tuna.tsinghua.edu.cn/simple",
                   "https://mirrors.aliyun.com/pypi/simple",
                   "https://pypi.org/simple"]
        dep_ok = False
        for m in mirrors:
            try:
                p = subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                                    "--retries", "3", "-i", m, "-r", req],
                                   cwd=HUB, capture_output=True, text=True, timeout=180)
                if p.returncode == 0:
                    dep_ok = True
                    break
            except Exception:
                continue
        if not dep_ok:
            dep_warn = "(⚠️ 依赖补装未成功,如面板起不来请手动 pip install -r requirements.txt)"
    try:
        new = open(os.path.join(HUB, "VERSION"), encoding="utf-8").read().strip()
    except Exception:
        new = "?"
    _restart_soon()
    return jsonify(ok=True, restarting=True, version=new,
                   message="已更新到 " + new + ",面板正在重启,请稍候…" + dep_warn)


def _detect_json_kind(path):
    """判断一个 json 是哪种源:memories / claude-web / chatgpt / unknown。"""
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return "unknown"
    if isinstance(data, dict) and any(k in data for k in ("conversations_memory", "project_memories", "memory_files")):
        return "memories"
    convs = data.get("conversations") if isinstance(data, dict) else data
    if isinstance(convs, list) and convs and isinstance(convs[0], dict):
        if "mapping" in convs[0]:
            return "chatgpt"
        if "chat_messages" in convs[0]:
            return "claude-web"
    return "unknown"


_CONNECTOR = {
    "memories": ("ingest_claude_memories.py", "Claude 云端记忆"),
    "claude-web": ("ingest_claude_web.py", "claude.ai 网页对话"),
    "chatgpt": ("ingest_chatgpt.py", "ChatGPT 对话"),
}


PIPELINE_LOCK = os.path.join(HUB, "staging", "pipeline.lock.d")


@contextlib.contextmanager
def _pipeline_lock():
    """跨进程管线互斥:面板导入/同步 与 夜跑 不并发跑 gate/distill(否则双份 API 花费 + 撞写锁)。
    用 mkdir 原子锁(mac/linux 通用;flock 命令 mac 没有)。拿不到 → 抛 RuntimeError。
    陈旧锁(>2h,进程被杀没清理)自动接管。"""
    os.makedirs(os.path.dirname(PIPELINE_LOCK), exist_ok=True)
    try:
        os.mkdir(PIPELINE_LOCK)
    except FileExistsError:
        try:
            stale = time.time() - os.path.getmtime(PIPELINE_LOCK) > 7200
        except OSError:
            stale = False
        if stale:
            try:
                os.rmdir(PIPELINE_LOCK)
                os.mkdir(PIPELINE_LOCK)
            except OSError:
                raise RuntimeError("已有一次采集/同步在进行中,请稍后再试")
        else:
            raise RuntimeError("已有一次采集/同步在进行中,请稍后再试")
    try:
        yield
    finally:
        try:
            os.rmdir(PIPELINE_LOCK)
        except OSError:
            pass


def _run_step(script, *args, timeout=1800):
    """跑一个管线脚本 → (ok, tail):ok=退出码 0;tail=stderr/stdout 末行(失败定位)。
    check=False + 手动判 returncode:非零不抛,由调用方决定如何回报,杜绝'伪成功'(审查 P1)。"""
    p = subprocess.run([sys.executable, os.path.join(HUB, "scripts", script), *args],
                       cwd=HUB, capture_output=True, text=True, timeout=timeout)
    lines = (p.stderr or p.stdout or "").strip().splitlines()
    return p.returncode == 0, (lines[-1] if lines else "")


@app.route("/api/import/claude-memory", methods=["POST"])
@app.route("/api/import", methods=["POST"])
def api_import():
    require_write()
    if not _alibaba_key():                                 # B1:缺 key 预检 → 可操作提示,不空跑 gate
        return jsonify({"ok": False, "need_key": True,
                        "message": "导入需要先在「设置」里选模型 provider + 填 API Key(用于把记忆向量化),保存后即可直接导入。"}), 400
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "message": "没有收到文件。"}), 400
    lower = f.filename.lower()
    if not (lower.endswith(".json") or lower.endswith(".zip")):
        return jsonify({"ok": False, "message": "只接受 .json(memories.json / conversations.json)或整包 .zip。"}), 400
    raw = f.read()
    if len(raw) > 200 * 1024 * 1024:
        return jsonify({"ok": False, "message": "文件过大(>200MB)。"}), 400
    dest = os.path.join(HUB, "imports", "_upload", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dest, exist_ok=True)
    paths = []
    if lower.endswith(".zip"):
        import io
        import zipfile
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except Exception:
            return jsonify({"ok": False, "message": "无法读取 zip。"}), 400
        for want in ("memories.json", "conversations.json"):   # 只按 basename 取,防 zip-slip
            m = next((n for n in zf.namelist()
                      if os.path.basename(n) == want and ".." not in n and not n.startswith("/")), None)
            if m:
                p = os.path.join(dest, want)
                with zf.open(m) as s, open(p, "wb") as d:
                    d.write(s.read(200 * 1024 * 1024))
                paths.append(p)
        if not paths:
            return jsonify({"ok": False, "message": "zip 里没找到 memories.json 或 conversations.json。"}), 400
    else:
        p = os.path.join(dest, "upload.json")
        with open(p, "wb") as d:
            d.write(raw)
        paths.append(p)
    routed, unknown, gtail = [], [], ""
    try:
        with _pipeline_lock():
            for p in paths:
                kind = _detect_json_kind(p)
                if kind not in _CONNECTOR:
                    unknown.append(os.path.basename(p))
                    continue
                script, label = _CONNECTOR[kind]
                ok, tail = _run_step(script, "--file", p, timeout=600)
                if not ok:   # 连接器崩溃 → 如实报错,别再说"已导过"(数据可能没进来)
                    return jsonify({"ok": False, "message": f"导入【{label}】失败:连接器报错,数据未入库。",
                                    "detail": tail}), 500
                routed.append(label)
            if not routed:
                return jsonify({"ok": False,
                                "message": f"没认出可导入的记忆源(收到:{', '.join(unknown) or '空'})。请确认是 Claude / ChatGPT 官方导出。"}), 400
            gok, gtail = _run_step("gate.py", "--near", "0.88", timeout=1800)
            if not gok:
                return jsonify({"ok": False, "message": "保真闸处理失败(候选已采集,稍后可重试,会从断点继续)。",
                                "detail": gtail}), 500
    except RuntimeError as e:
        return jsonify({"ok": False, "message": str(e)}), 409
    except Exception as e:
        return jsonify({"ok": False, "message": f"处理失败:{e}"}), 500
    c = db()
    qn = c.execute("SELECT count(*) FROM human_queue WHERE status='pending' OR status IS NULL").fetchone()[0]
    c.close()
    src = "、".join(routed)
    if qn == 0:
        return jsonify({"ok": True, "queued": 0,
                        "message": f"已处理【{src}】,但没有可导入的新记忆(可能之前已导过)。",
                        "detail": gtail})
    return jsonify({"ok": True, "queued": qn,
                    "message": f"已从【{src}】导入,{qn} 条候选进入「待确认队列」,去逐条批准 / 丢弃。",
                    "detail": gtail})


@app.route("/api/import/claude-code", methods=["POST"])
def api_import_claude_code():
    require_write()
    if not _alibaba_key():
        return jsonify({"ok": False, "need_key": True,
                        "message": "扫描需要先在「设置」里填 API Key(用于把记忆向量化)。"}), 400
    try:
        with _pipeline_lock():
            for step, ta in (("ingest.py", 600), ("distill.py", 1800), ("gate.py", 1800)):
                args = ("--near", "0.88") if step == "gate.py" else ()
                ok, tail = _run_step(step, *args, timeout=ta)
                if not ok:
                    return jsonify({"ok": False, "message": f"扫描失败({step}):{tail or '子进程非零退出'}"}), 500
    except RuntimeError as e:
        return jsonify({"ok": False, "message": str(e)}), 409
    except Exception as e:
        return jsonify({"ok": False, "message": f"扫描失败:{e}"}), 500
    c = db()
    qn = c.execute("SELECT count(*) FROM human_queue WHERE status='pending' OR status IS NULL").fetchone()[0]
    raw_n = c.execute("SELECT count(*) FROM raw_event WHERE source='claude-code'").fetchone()[0]
    c.close()
    return jsonify({"ok": True, "queued": qn,
                    "message": f"已扫描本机 Claude Code 对话(累计 {raw_n} 条原始记录),「待确认队列」现有 {qn} 条。",
                    "detail": tail})


@app.route("/api/import/codex", methods=["POST"])
def api_import_codex():
    require_write()
    if not _alibaba_key():
        return jsonify({"ok": False, "need_key": True,
                        "message": "扫描需要先在「设置」里填 API Key(用于把记忆向量化)。"}), 400
    if not glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/rollout-*.jsonl")):
        return jsonify({"ok": False,
                        "message": "未找到本机 Codex 会话(~/.codex/sessions):没装 Codex CLI 或还没用它聊过。"}), 400
    try:
        with _pipeline_lock():
            for step, ta in (("ingest_codex.py", 600), ("distill.py", 1800), ("gate.py", 1800)):
                args = ("--near", "0.88") if step == "gate.py" else ()
                ok, tail = _run_step(step, *args, timeout=ta)
                if not ok:
                    return jsonify({"ok": False, "message": f"扫描失败({step}):{tail or '子进程非零退出'}"}), 500
    except RuntimeError as e:
        return jsonify({"ok": False, "message": str(e)}), 409
    except Exception as e:
        return jsonify({"ok": False, "message": f"扫描失败:{e}"}), 500
    c = db()
    qn = c.execute("SELECT count(*) FROM human_queue WHERE status='pending' OR status IS NULL").fetchone()[0]
    raw_n = c.execute("SELECT count(*) FROM raw_event WHERE source='codex'").fetchone()[0]
    c.close()
    return jsonify({"ok": True, "queued": qn,
                    "message": f"已扫描本机 Codex 对话(累计 {raw_n} 条原始记录),「待确认队列」现有 {qn} 条。",
                    "detail": tail})


SYNC_STATE = os.path.join(HUB, "logs", "sync_state.json")
_sync_lock = threading.Lock()


def _sync_pipeline():
    """增量同步本地源:ingest(claude-code + codex)→ distill → gate。

    ingest 靠 ingest_cursor 增量、distill/gate 靠去重幂等 → 重复跑安全。"""
    if not _alibaba_key():
        return {"ok": False, "message": "未配 API Key,跳过同步(在「设置」里配好即可)"}
    if not _sync_lock.acquire(blocking=False):
        return {"ok": False, "message": "已有一次同步在进行中"}
    try:
        failed = None
        with _pipeline_lock():   # 跨进程互斥:不与夜跑/另一次导入并发跑管线
            for script in ("ingest.py", "ingest_codex.py", "distill.py", "gate.py"):
                args = ("--near", "0.88") if script == "gate.py" else ()
                ta = 600 if script.startswith("ingest") else 1800
                ok, tail = _run_step(script, *args, timeout=ta)
                if not ok:
                    failed = (script, tail)
                    break
        if failed:
            res = {"ok": False, "message": f"同步失败({failed[0]}):{failed[1][:120] or '子进程非零退出'}"}
        else:
            c = db()
            qn = c.execute("SELECT count(*) FROM human_queue WHERE status='pending' OR status IS NULL").fetchone()[0]
            rn = c.execute("SELECT count(*) FROM raw_event").fetchone()[0]
            c.close()
            res = {"ok": True, "queued": qn,
                   "message": f"同步完成:累计 {rn} 条原始记录,「待确认」现有 {qn} 条。"}
    except RuntimeError as e:
        res = {"ok": False, "message": str(e)}
    except Exception as e:
        res = {"ok": False, "message": f"同步失败:{str(e)[:160]}"}
    finally:
        _sync_lock.release()
    res["ts"] = time.strftime("%Y-%m-%d %H:%M")
    res["ts_epoch"] = time.time()
    try:
        os.makedirs(os.path.dirname(SYNC_STATE), exist_ok=True)
        json.dump(res, open(SYNC_STATE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return res


def _autosync_hours():
    import provider
    try:
        return float(provider._env("MEMORYHUB_AUTOSYNC_HOURS") or 0)
    except Exception:
        return 0


def _autosync_loop():
    """每小时醒来看一眼:开关打开且距上次同步够久 → 跑一轮。异常静默,不拖垮面板。"""
    while True:
        time.sleep(3600)
        try:
            hours = _autosync_hours()
            if hours <= 0:
                continue
            last = 0
            if os.path.exists(SYNC_STATE):
                last = (json.load(open(SYNC_STATE, encoding="utf-8")) or {}).get("ts_epoch", 0)
            if time.time() - last >= hours * 3600:
                _sync_pipeline()
        except Exception:
            pass


@app.route("/api/sync", methods=["POST"])
def api_sync():
    require_write()
    return jsonify(_sync_pipeline())


@app.route("/api/sync-status")
def api_sync_status():
    st = {}
    try:
        if os.path.exists(SYNC_STATE):
            st = json.load(open(SYNC_STATE, encoding="utf-8")) or {}
    except Exception:
        st = {}
    return jsonify({"last": {k: st.get(k) for k in ("ok", "message", "ts")},
                    "auto_hours": _autosync_hours()})


@app.route("/api/autosync", methods=["POST"])
def api_autosync():
    require_write()
    a = request.get_json(force=True, silent=True) or {}
    try:
        hours = float(a.get("hours", 0))
        assert 0 <= hours <= 168
    except Exception:
        return jsonify({"ok": False, "message": "hours 需在 0–168 之间(0=关闭)。"}), 400
    _set_env({"MEMORYHUB_AUTOSYNC_HOURS": str(int(hours) if hours == int(hours) else hours)})
    return jsonify({"ok": True, "auto_hours": hours,
                    "message": ("已开启:每 %g 小时自动增量同步一次。" % hours) if hours else "已关闭自动同步。"})


@app.route("/api/config")
def api_config():
    import provider
    c = provider.resolve()
    return jsonify({
        "provider": c["provider"], "has_key": bool(c["key"]),
        "chat_model": c["chat_model"], "embed_model": c["embed_model"],
        "embed_dim": c["embed_dim"], "base_url": c["base"],
        "presets": {k: {"base": v[0], "chat": v[1], "embed": v[2], "dim": v[3], "format": v[4]}
                    for k, v in provider.PRESETS.items()},
    })


@app.route("/api/config", methods=["POST"])
def api_config_save():
    require_write()
    a = request.get_json(force=True, silent=True) or {}
    updates = {}
    if (a.get("provider") or "").strip():
        updates["LLM_PROVIDER"] = a["provider"].strip()
    if a.get("api_key") is not None:
        updates["LLM_API_KEY"] = str(a["api_key"]).strip()
    for kj, ke in (("chat_model", "LLM_MODEL"), ("base_url", "LLM_BASE_URL"),
                   ("embed_model", "EMBED_MODEL"), ("embed_api_key", "EMBED_API_KEY"),
                   ("embed_base_url", "EMBED_BASE_URL"), ("embed_dim", "EMBED_DIM")):
        if a.get(kj) is not None:
            updates[ke] = str(a[kj]).strip()
    if not updates:
        return jsonify({"ok": False, "message": "没有要保存的字段。"}), 400
    try:
        _set_env(updates)
    except Exception as e:
        return jsonify({"ok": False, "message": f"写 .env 失败:{e}"}), 500
    return jsonify({"ok": True, "message": "已保存到 .env,即时生效(无需重启)。"})


@app.route("/api/provider-test", methods=["POST"])
def api_provider_test():
    require_write()
    import provider
    ok, detail = provider.test_connectivity()
    return jsonify({"ok": ok, "detail": detail})


@app.route("/favicon.ico")
@app.route("/api/icon")
def api_icon():
    p = os.path.join(HUB, "assets", "icon-dark.png")
    if not os.path.exists(p):
        abort(404)
    return Response(open(p, "rb").read(), mimetype="image/png")


@app.route("/api/brand-qr")
def api_brand_qr():
    p = os.path.join(WEB, "static", "qr-wecom.png")
    if not os.path.exists(p):
        abort(404)
    return Response(open(p, "rb").read(), mimetype="image/png")


@app.route("/api/backup")
def api_backup():
    """一键备份:sqlite 在线备份 API 做一致性快照(不裸拷正在写的库)→ zip 下载。

    浏览器 <a download> 无法带自定义头,故只做 Host 白名单(只读、本机)。"""
    import shutil
    import tempfile
    import zipfile
    from flask import after_this_request, send_file
    ts = time.strftime("%Y%m%d-%H%M")
    tmpdir = tempfile.mkdtemp(prefix="mhbackup-")
    snap = os.path.join(tmpdir, "memory.db")
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(snap)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    zpath = os.path.join(tmpdir, f"memoryhub-backup-{ts}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(snap, "memory.db")
        vp = os.path.join(HUB, "VERSION")
        if os.path.exists(vp):
            zf.write(vp, "VERSION")

    @after_this_request
    def _cleanup(resp):
        try:
            shutil.rmtree(tmpdir)   # POSIX:send_file 已持有 fd,删除不影响传输
        except Exception:
            pass
        return resp
    return send_file(zpath, as_attachment=True, download_name=os.path.basename(zpath))


_GOLDEN_CACHE = {"data": None, "ts": None}


@app.route("/api/golden", methods=["POST"])
def api_golden():
    """保真跑分:对黄金题集跑召回,算 Hit@k / 弃答率 / 缺口闭合。
    POST + 写头:每次跑会对每题调一次嵌入 API,防跨源烧 token。默认返回缓存,refresh=1 重跑。"""
    require_write()
    if not _alibaba_key():
        return jsonify({"ok": False, "need_key": True,
                        "message": "跑分需要嵌入 API Key(把查询向量化)。请在「设置」里配好。"}), 400
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("refresh") and _GOLDEN_CACHE["data"]:
        return jsonify({"ok": True, "cached": True, "ts": _GOLDEN_CACHE["ts"], **_GOLDEN_CACHE["data"]})
    try:
        sys.path.insert(0, os.path.join(HUB, "eval"))
        import run_golden
        res = run_golden.score(DB)
    except FileNotFoundError:
        return jsonify({"ok": False, "message": "未找到黄金题集 eval/golden.jsonl。"}), 500
    except Exception as e:
        return jsonify({"ok": False, "message": f"跑分失败:{str(e)[:160]}"}), 500
    _GOLDEN_CACHE.update({"data": res, "ts": time.strftime("%Y-%m-%d %H:%M")})
    return jsonify({"ok": True, "cached": False, "ts": _GOLDEN_CACHE["ts"], **res})


if __name__ == "__main__":
    print(f"MemoryHub 面板 → http://{HOST}:{PORT}")
    threading.Thread(target=_autosync_loop, daemon=True).start()   # 自动增量同步(默认关,设置见「导入」页)
    for _attempt in range(20):
        try:
            app.run(host=HOST, port=PORT, debug=False)
            break
        except (SystemExit, OSError):        # 一键更新交接:旧进程退位瞬间端口可能未释放
            if _attempt == 19:
                raise
            time.sleep(0.5)
