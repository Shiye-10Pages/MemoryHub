"""管线闭环:ingest → gate(有嵌入/无嵌入降级)→ 人工闸。全部在临时库上进行。"""
import json
import os
import sqlite3
import sys

import gate
import ingest_claude_memories as icm
from conftest import make_db

SAMPLE = {
    "conversations_memory": "**定价心得**\n定价锚点应放在旗舰版。\n**内容节奏**\n每周三更。",
    "project_memories": {"proj-abc": "该项目偏好极简依赖。"},
    "memory_files": [],
}


def _setup(tmp_path, monkeypatch):
    db = str(tmp_path / "memory.db")
    make_db(db)
    staging = str(tmp_path / "staging")
    raw = str(tmp_path / "raw" / "claude-memory")
    monkeypatch.setattr(icm, "DB", db)
    monkeypatch.setattr(icm, "RAW_DIR", raw)
    monkeypatch.setattr(icm, "STAGING", staging)
    monkeypatch.setattr(gate, "DB", db)
    monkeypatch.setattr(gate, "CAND", os.path.join(staging, "candidates.jsonl"))
    f = tmp_path / "memories.json"
    f.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
    return db, str(f)


def _run_ingest(path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ingest_claude_memories.py", "--file", path])
    icm.main()


def test_ingest_writes_raw_and_candidates(tmp_path, monkeypatch):
    db, f = _setup(tmp_path, monkeypatch)
    _run_ingest(f, monkeypatch)
    con = sqlite3.connect(db)
    raw_n = con.execute("SELECT count(*) FROM raw_event WHERE source='claude-memory'").fetchone()[0]
    con.close()
    assert raw_n == 3                                   # 2 小节 + 1 项目记忆
    cands = open(gate.CAND, encoding="utf-8").read().splitlines()
    assert len(cands) == 3
    assert all(json.loads(x)["force_review"] for x in cands)   # AI 侧记忆一律人工闸


def test_gate_no_embed_keeps_candidates(tmp_path, monkeypatch, capsys):
    db, f = _setup(tmp_path, monkeypatch)
    _run_ingest(f, monkeypatch)

    def boom(texts, **kw):
        raise RuntimeError("缺少嵌入 API Key")

    monkeypatch.setattr(gate, "embed_texts", boom)
    monkeypatch.setattr(sys, "argv", ["gate.py"])
    gate.main()                                         # 不抛异常 = 优雅降级
    out = capsys.readouterr().out
    assert "嵌入失败" in out
    assert os.path.exists(gate.CAND)                    # 候选保留,配 key 后可重跑
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM memory_item").fetchone()[0] == 0
    con.close()


def test_gate_with_embed_routes_to_queue(tmp_path, monkeypatch):
    db, f = _setup(tmp_path, monkeypatch)
    _run_ingest(f, monkeypatch)
    monkeypatch.setattr(gate, "embed_texts",
                        lambda texts, **kw: [[0.1] * 64 for _ in texts])
    monkeypatch.setattr(sys, "argv", ["gate.py"])
    gate.main()
    con = sqlite3.connect(db)
    qn = con.execute("SELECT count(*) FROM human_queue").fetchone()[0]
    con.close()
    assert qn == 3                                      # force_review 全部进人工闸,零自动入正库


def test_gate_idempotent_rerun(tmp_path, monkeypatch):
    db, f = _setup(tmp_path, monkeypatch)
    _run_ingest(f, monkeypatch)
    monkeypatch.setattr(gate, "embed_texts",
                        lambda texts, **kw: [[0.1] * 64 for _ in texts])
    monkeypatch.setattr(sys, "argv", ["gate.py"])
    gate.main()
    gate.main()                                         # 重跑不得重复入列
    con = sqlite3.connect(db)
    qn = con.execute("SELECT count(*) FROM human_queue").fetchone()[0]
    con.close()
    assert qn == 3
