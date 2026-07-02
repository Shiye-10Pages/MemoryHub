"""写端点:守卫、.env 注入回归、config 往返、无 key 导入、zip-slip、召回降级、更新守卫、批准。"""
import io
import json
import os
import sqlite3
import zipfile

import review_queue
import server
from conftest import H, HW, conn


def test_write_guard_403_without_header(client):
    for path in ("/api/config", "/api/import", "/api/queue/bulk"):
        assert client.post(path, headers=H).status_code == 403, path


def test_config_env_line_injection_blocked(client, env):
    r = client.post("/api/config", headers=HW,
                    json={"provider": "dashscope", "api_key": "EVIL\nHACK=1\nX=y"})
    assert r.status_code == 200
    lines = open(os.path.join(str(env), ".env"), encoding="utf-8").read().splitlines()
    assert not any(line.startswith(("HACK=", "X=")) for line in lines)   # 控制符已剥离,未产生新行


def test_config_roundtrip_has_key(client):
    assert client.get("/api/config", headers=H).get_json()["has_key"] is False
    r = client.post("/api/config", headers=HW,
                    json={"provider": "dashscope", "api_key": "sk-test-123"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert client.get("/api/config", headers=H).get_json()["has_key"] is True


def test_import_without_key_400_actionable(client):
    data = {"file": (io.BytesIO(b"{}"), "memories.json")}
    r = client.post("/api/import", headers=HW, data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 400
    d = r.get_json()
    assert d["need_key"] and "设置" in d["message"]


def _with_key(monkeypatch):
    monkeypatch.setattr(server, "_alibaba_key", lambda: "sk-test")


def test_import_zip_slip_rejected(client, monkeypatch):
    _with_key(monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil/memories.json", "{}")     # 穿越路径条目
        zf.writestr("/abs/memories.json", "{}")        # 绝对路径条目
    buf.seek(0)
    r = client.post("/api/import", headers=HW,
                    data={"file": (buf, "export.zip")},
                    content_type="multipart/form-data")
    assert r.status_code == 400                        # 拒绝,而非提取


def test_import_garbage_json_400_not_500(client, monkeypatch):
    _with_key(monkeypatch)
    r = client.post("/api/import", headers=HW,
                    data={"file": (io.BytesIO(b"not json at all"), "upload.json")},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    assert "没认出" in r.get_json()["message"]


def test_recall_degrades_to_fts(client, env, monkeypatch):
    c = conn(env)
    c.execute("INSERT INTO memory_item(id,type,claim,evidence,sources,confidence,valid_from,status) "
              "VALUES('t1','认知','关于定价策略的记忆','定价证据原文','[]',0.8,date('now'),'待核')")
    c.commit(); c.close()

    def boom(q, k):
        raise RuntimeError("no key")

    monkeypatch.setattr(server.recall_mod, "recall", boom)
    r = client.post("/api/recall", headers=H, json={"query": "定价策略", "topk": 5})
    assert r.status_code == 200
    d = r.get_json()
    assert d["degraded"] is True and len(d["hits"]) >= 1
    assert d["hits"][0]["claim"] == "关于定价策略的记忆"


def test_update_apply_refuses_non_git(client):
    r = client.post("/api/update-apply", headers=HW)   # HUB=tmp,无 .git
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is False and "git" in d["message"]


def test_queue_approve_writes_memory(client, env, monkeypatch):
    monkeypatch.setattr(review_queue, "embed_texts",
                        lambda texts, **kw: [[0.0] * review_queue.DIM for _ in texts])
    cand = {"id": "cafe01", "type": "认知", "claim": "测试待确认条目",
            "evidence": "证据文本", "sources": [{"source": "t", "conv_id": "c1", "ts": "2026-01-01"}],
            "confidence": 0.6}
    c = conn(env)
    c.execute("INSERT INTO human_queue(id,candidate,reason) VALUES('q_cafe01',?, 'review')",
              (json.dumps(cand, ensure_ascii=False),))
    c.commit(); c.close()

    r = client.post("/api/queue/q_cafe01", headers=HW, json={"action": "approve"})
    assert r.status_code == 200

    c = conn(env)
    row = c.execute("SELECT status FROM memory_item WHERE id='cafe01'").fetchone()
    emb = c.execute("SELECT count(*) FROM memory_embedding WHERE memory_item_id='cafe01'").fetchone()[0]
    left = c.execute("SELECT count(*) FROM human_queue").fetchone()[0]
    c.close()
    assert row["status"] == "已确认" and emb == 1 and left == 0
