"""管线闭环:ingest → gate(有嵌入/无嵌入降级)→ 人工闸。全部在临时库上进行。"""
import json
import os
import sqlite3
import sys

import gate
import ingest_claude_memories as icm
import ingest_codex as icx
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


def _codex_rollout(tmp_path):
    lines = [
        {"timestamp": "2026-06-01T10:00:00", "type": "session_meta",
         "payload": {"id": "sess-1", "cwd": "/tmp/projx"}},
        {"timestamp": "2026-06-01T10:00:01", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "<environment_context>噪声</environment_context>"}]}},
        {"timestamp": "2026-06-01T10:00:02", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "帮我定个发布节奏"}]}},
        {"timestamp": "2026-06-01T10:00:03", "type": "response_item",
         "payload": {"type": "reasoning", "summary": []}},
        {"timestamp": "2026-06-01T10:00:04", "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "建议每周三发布。"}]}},
        {"timestamp": "2026-06-01T10:00:05", "type": "response_item",
         "payload": {"type": "function_call", "name": "shell"}},
    ]
    d = tmp_path / "sessions" / "2026" / "06" / "01"
    d.mkdir(parents=True)
    f = d / "rollout-2026-06-01T10-00-00-sess-1.jsonl"
    f.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    return str(tmp_path / "sessions" / "*" / "*" / "*" / "rollout-*.jsonl")


def test_ingest_codex_extracts_real_turns_only(tmp_path, monkeypatch):
    db = str(tmp_path / "memory.db")
    make_db(db)
    monkeypatch.setattr(icx, "DB", db)
    monkeypatch.setattr(icx, "RAW_DIR", str(tmp_path / "raw" / "codex"))
    monkeypatch.setattr(icx, "SRC_GLOB", _codex_rollout(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ingest_codex.py"])
    icx.main()
    con = sqlite3.connect(db)
    rows = con.execute("SELECT role,text,conv_id,project FROM raw_event WHERE source='codex' ORDER BY seq").fetchall()
    con.close()
    assert [(r[0], r[1]) for r in rows] == [("user", "帮我定个发布节奏"), ("assistant", "建议每周三发布。")]
    assert rows[0][2] == "sess-1" and rows[0][3] == "/tmp/projx"   # 会话 id / 项目目录来自 session_meta


def test_ingest_codex_idempotent(tmp_path, monkeypatch):
    db = str(tmp_path / "memory.db")
    make_db(db)
    monkeypatch.setattr(icx, "DB", db)
    monkeypatch.setattr(icx, "RAW_DIR", str(tmp_path / "raw" / "codex"))
    monkeypatch.setattr(icx, "SRC_GLOB", _codex_rollout(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ingest_codex.py"])
    icx.main()
    icx.main()                                          # 重跑:mtime 未变 → 全跳过
    con = sqlite3.connect(db)
    n = con.execute("SELECT count(*) FROM raw_event WHERE source='codex'").fetchone()[0]
    con.close()
    assert n == 2


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
