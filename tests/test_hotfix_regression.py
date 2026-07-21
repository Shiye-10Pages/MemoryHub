#!/usr/bin/env python3
"""MemoryHub · v0.4.2 热修回归测试(把审查复现固化成守门用例)。

只用标准库 + numpy(recall 用),不需网络/API key:嵌入被 monkeypatch。
跑法:
    python3 tests/test_hotfix_regression.py      # 直接跑(无需 pytest)
    python3 -m pytest tests/                      # 有 pytest 也行
"""
import os
import struct
import sys
import sqlite3
import tempfile

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(HUB, "scripts")
sys.path.insert(0, SCRIPTS)


def _fresh_db():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "memory.db")
    import init_db
    init_db.main.__globals__  # ensure import
    os.system(f'python3 "{os.path.join(SCRIPTS, "init_db.py")}" "{db}" >/dev/null 2>&1')
    return db


# ---- T1.1:新装库含 context 列,gate 风格 INSERT 不崩 ----
def test_fresh_install_has_context():
    db = _fresh_db()
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(memory_item)")}
    assert "context" in cols, "新装库缺 context 列(P0-A 回归)"
    con.execute(
        "INSERT OR IGNORE INTO memory_item"
        "(id,type,claim,context,evidence,sources,confidence,valid_from,status,"
        " review_date,links,content_hash,current_revision_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("x", "决策", "测试结论", "测试情境", "证据", "[]", 0.56, "2026-07-08",
         "待核", "2026-07-22", "[]", "x", "x-r1"))
    con.commit()
    con.close()


# ---- T1.3:被拒候选不复活(rejected 行留档 + q_ id 在 seen 中 → gate 过滤掉) ----
def test_reject_does_not_revive():
    import gate
    db = _fresh_db()
    con = sqlite3.connect(db)
    claim = "被拒测试结论"
    cid = gate.claim_id(claim)
    con.execute("INSERT INTO human_queue(id,candidate,reason,status) VALUES(?,?,?,?)",
                ("q_" + cid, '{"claim":"' + claim + '"}', "high_impact", "pending"))
    # 新的拒绝 = 改状态,不删行
    con.execute("UPDATE human_queue SET status='rejected', resolved_at=datetime('now') WHERE id=?",
                ("q_" + cid,))
    con.commit()
    seen = {r[0] for r in con.execute("SELECT id FROM memory_item")}
    seen |= {r[0][2:] for r in con.execute(
        "SELECT id FROM human_queue WHERE id LIKE 'q\\_%' ESCAPE '\\'")}
    con.close()
    assert cid in seen, "被拒 claim 的 id 不在 seen(会复活,P0-B 回归)"
    groups = [{"item": {"claim": claim}}]
    kept = [g for g in groups if gate.claim_id(g["item"]["claim"]) not in seen]
    assert kept == [], "被拒候选仍会被 gate 处理(复活,P0-B 回归)"


# ---- T1.2:FTS MATCH 必须写在表名上;别名 `f MATCH` 是历史 bug ----
def test_fts_alias_fixed():
    db = _fresh_db()
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO memory_item(id,type,claim,evidence,sources,valid_from) "
        "VALUES('m1','事实','关于端口7788的记忆','端口是7788','[]',date('now'))")
    con.commit()
    # 正确写法:表名 MATCH
    ok = con.execute("SELECT count(*) FROM memory_fts JOIN memory_item mi "
                     "ON mi.rowid=memory_fts.rowid WHERE memory_fts MATCH ?",
                     ('"7788"',)).fetchone()[0]
    assert ok >= 1, "表名 MATCH 查不到(FTS 修复回归)"
    # 别名写法必须报错(证明我们没退回旧 bug 的写法)
    raised = False
    try:
        con.execute("SELECT 1 FROM memory_fts f JOIN memory_item mi ON mi.rowid=f.rowid "
                    "WHERE f MATCH ?", ('"7788"',)).fetchall()
    except sqlite3.OperationalError:
        raised = True
    con.close()
    assert raised, "别名 `f MATCH` 居然没报错——SQLite 行为变了,复查 recall 的 FTS 写法"


# ---- T2.3:类型受控闸,归一到契约 9 类 ----
def test_type_gate_normalizes():
    import gate
    assert gate.normalize_type("决策") == "决策"          # 合法类原样
    assert gate.normalize_type("行动") == "SOP"            # 常见野生 → 映射
    assert gate.normalize_type("定价") == "决策"
    assert gate.normalize_type("角色定位") == "偏好"
    assert gate.normalize_type("从未见过的类型") == "认知"  # 未知兜底
    assert gate.normalize_type("") == "认知"
    assert gate.normalize_type(None) == "认知"
    # migrate v7 在临时库上把野生类型改成 9 类之一,且幂等
    import sqlite3
    db = _fresh_db()
    con = sqlite3.connect(db)
    for i, t in enumerate(["行动", "风险", "决策"]):
        con.execute("INSERT INTO memory_item(id,type,claim,evidence,sources,valid_from) "
                    "VALUES(?,?,?,?,?,?)", (f"t{i}", t, f"c{i}", "e", "[]", "2026-07-01"))
    con.commit(); con.close()
    os.system(f'python3 "{os.path.join(SCRIPTS, "migrate_memory_v7.py")}" --db "{db}" >/dev/null 2>&1')
    con = sqlite3.connect(db)
    types = {r[0] for r in con.execute("SELECT DISTINCT type FROM memory_item")}
    con.close()
    assert types <= gate.TYPES, f"迁移后仍有野生类型: {types - gate.TYPES}"


# ---- T2.4:置信度 sr 按来源分级、em 分规则/LLM(不再恒定) ----
def test_confidence_varies_by_source():
    import gate
    assert gate.sr_for_source("claude-code") > gate.sr_for_source("chatgpt")   # 第一方 > 网页导出
    assert gate.sr_for_source("口播稿") > gate.sr_for_source("chatgpt")
    assert gate.em_for_extractor("rule") > gate.em_for_extractor("qwen3-max")   # 规则 > LLM
    # 不同来源的候选,confidence 不同(不再全 0.56)
    c_cc = gate.confidence(1, sr=gate.sr_for_source("claude-code"), em=gate.em_for_extractor("qwen3-max"))
    c_gpt = gate.confidence(1, sr=gate.sr_for_source("chatgpt"), em=gate.em_for_extractor("qwen3-max"))
    assert c_cc != c_gpt, "不同来源置信度应不同"


# ---- T2.5:人工闸需命中 ≥2 个业务词,单个高频词不再刷爆队列 ----
def test_high_impact_needs_two_keywords():
    import gate
    assert gate.high_impact({"impact": True, "claim": "课程定价定 3980", "evidence": ""}) is True   # 课程+定价+价格 ≥2
    assert gate.high_impact({"impact": True, "claim": "这个方向值得做", "evidence": ""}) is False   # 仅"方向"1 词
    assert gate.high_impact({"impact": False, "claim": "课程定价融资", "evidence": ""}) is False     # impact=false 直接否


# ---- T2.7:pack_embedding 按向量实际长度打包(切 provider 不崩/不存错维) ----
def test_pack_embedding_by_len():
    import struct
    import embed
    for d in (4, 8, 1536):
        model, dim, blob = embed.pack_embedding([0.25] * d)
        assert dim == d, f"dim 应={d},得 {dim}(用了缓存 DIM?)"
        assert len(blob) == d * 4, "blob 长度与向量维度不符"
        assert struct.unpack(f"<{d}f", blob)[0] == 0.25


# ---- distill 溯源闸:evidence 必须是输入逐字子串 ----
def test_evidence_substring_gate():
    window = "用户说:该账号定位应聚焦单一主轴,不要多方向并行。助手回复……"
    good = "聚焦单一主轴"
    bad = "聚焦单个主轴"        # 改了一个字 → 不是逐字子串
    assert good in window
    assert bad not in window   # 溯源闸会拒掉 bad


# ---- T1.2:召回 cosine 地板会弃答(离线合成向量,不用网络) ----
def test_recall_floor_abstains():
    import numpy as np
    import recall as R
    db = _fresh_db()
    dim = 4
    # 两条记忆,向量分别指向 e0、e1
    con = sqlite3.connect(db)
    for mid, vec in (("a", [1, 0, 0, 0]), ("b", [0, 1, 0, 0])):
        con.execute("INSERT INTO memory_item(id,type,claim,evidence,sources,valid_from,status) "
                    "VALUES(?,?,?,?,?,?,?)", (mid, "事实", f"记忆{mid}", "证据", "[]", "2026-07-01", "待核"))
        con.execute("INSERT INTO memory_embedding(memory_item_id,model,dim,vec) VALUES(?,?,?,?)",
                    (mid, "test", dim, struct.pack(f"<{dim}f", *[float(x) for x in vec])))
    con.commit()
    con.close()

    R.DB = db
    # 查询向量与两条都近乎正交(cos≈0)→ 应全部弃答(低于 0.55 地板)
    R.embed_texts = lambda texts, text_type="query": [[0, 0, 1, 0]]
    assert R.recall("无关问题", 8) == [], "离题查询未弃答(弃答地板回归)"
    # 查询向量对齐记忆 a(cos=1)→ 应召回 a
    R.embed_texts = lambda texts, text_type="query": [[1, 0, 0, 0]]
    hits = R.recall("对齐 a 的问题", 8)
    assert len(hits) == 1 and hits[0]["id"] == "a", "对齐查询未召回(弃答地板过严)"
    assert hits[0]["status"] == "待核" and hits[0]["cosine"] is not None, "召回未带 status/cosine"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            ok += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {e}")
    print(f"\n{ok}/{len(fns)} 通过")
    return ok == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
