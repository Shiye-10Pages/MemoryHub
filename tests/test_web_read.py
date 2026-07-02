"""只读端点:首页、stats、Host 白名单、更新检查(含缓存)、接入信息、空库地图、备份。"""
import io
import json
import sqlite3
import zipfile

from conftest import H, conn


def test_index_serves_brand_symbol(client):
    r = client.get("/", headers=H)
    assert r.status_code == 200
    assert 'id="mh"' in r.get_data(as_text=True)      # 品牌 symbol 内联存在


def test_stats_empty_db(client):
    r = client.get("/api/stats", headers=H)
    assert r.status_code == 200
    assert r.get_json()["memory_item"] == 0


def test_bad_host_rejected(client):
    # test_client 默认 Host=localhost(不带端口)→ 不在白名单 → 403(防 DNS rebinding)
    assert client.get("/api/stats").status_code == 403


def test_update_check_and_daily_cache(client, env, monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"tag_name": "v10.0.0", "html_url": "https://x"}).encode()

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    r1 = client.get("/api/update-check", headers=H).get_json()
    assert r1["current"] == "9.9.9"
    assert r1["latest"] == "10.0.0"
    assert r1["update_available"] is True
    r2 = client.get("/api/update-check", headers=H).get_json()   # 第二次走当日缓存
    assert calls["n"] == 1
    assert r2["update_available"] is True
    # 缓存命中期间版本变了(如刚一键更新)→ update_available 必须按缓存 latest 重算,红点不残留
    (env / "VERSION").write_text("10.0.0\n")
    r3 = client.get("/api/update-check", headers=H).get_json()
    assert calls["n"] == 1
    assert r3["current"] == "10.0.0"
    assert r3["update_available"] is False


def test_connect_info_absolute_paths(client):
    d = client.get("/api/connect-info", headers=H).get_json()
    args = d["mcp_json"]["mcpServers"]["memoryhub"]["args"]
    cmd = d["mcp_json"]["mcpServers"]["memoryhub"]["command"]
    assert cmd.startswith("/") and all(a.startswith("/") for a in args)
    assert "recall_memory" in d["instruction"]


def test_backup_zip_contains_consistent_db(client, env, tmp_path):
    c = conn(env)
    c.execute("INSERT INTO memory_item(id,type,claim,evidence,sources,confidence,valid_from,status) "
              "VALUES('b1','事实','备份测试条目','证据','[]',0.9,date('now'),'已确认')")
    c.commit(); c.close()
    r = client.get("/api/backup", headers=H)
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    assert set(zf.namelist()) == {"memory.db", "VERSION"}
    out = tmp_path / "restored.db"
    out.write_bytes(zf.read("memory.db"))
    rc = sqlite3.connect(str(out))
    assert rc.execute("SELECT count(*) FROM memory_item").fetchone()[0] == 1   # 快照可开、数据在
    rc.close()


def test_health_reports_db_size(client):
    d = client.get("/api/health", headers=H).get_json()
    assert isinstance(d["db_size_mb"], (int, float)) and d["db_size_mb"] >= 0


def test_map_empty_db_no_500(client):
    r = client.get("/api/map", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert not d.get("points") and d.get("note")       # 空库给 note 而非崩


def test_map_timeline_needs_map_first(client, monkeypatch):
    import server
    monkeypatch.setattr(server, "_MAP_CACHE", {"n": -1, "data": None})
    d = client.get("/api/map-timeline", headers=H).get_json()
    assert d["series"] == [] and "地图" in d["note"]    # 未聚类 → 引导先开地图


def test_map_timeline_aggregates_by_cluster_month(client, env, monkeypatch):
    import server
    c = conn(env)
    for mid, vf in (("m1", "2026-01-05"), ("m2", "2026-01-20"), ("m3", "2026-03-02"), ("m4", None)):
        c.execute("INSERT INTO memory_item(id,type,claim,evidence,sources,confidence,valid_from,status) "
                  "VALUES(?,?,?,?,'[]',0.8,?,'待核')", (mid, "认知", "c" + mid, "e", vf))
    c.commit(); c.close()
    monkeypatch.setattr(server, "_MAP_CACHE", {"n": 4, "data": {
        "points": [{"id": "m1", "cluster": 0}, {"id": "m2", "cluster": 0},
                   {"id": "m3", "cluster": 1}, {"id": "m4", "cluster": 0}],
        "clusters": [{"id": 0, "label": "主题A"}, {"id": 1, "label": "主题B"}]}})
    d = client.get("/api/map-timeline", headers=H).get_json()
    assert d["months"] == ["2026-01", "2026-03"]
    a = next(s for s in d["series"] if s["id"] == 0)
    b = next(s for s in d["series"] if s["id"] == 1)
    assert a["label"] == "主题A" and a["counts"] == [2, 0] and a["total"] == 2   # 无 valid_from 的不计
    assert b["counts"] == [0, 1]
    assert d["series"][0]["id"] == 0                    # 按 total 降序
