"""只读端点:首页、stats、Host 白名单、更新检查(含缓存)、接入信息、空库地图。"""
import json

from conftest import H


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


def test_update_check_and_daily_cache(client, monkeypatch):
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


def test_connect_info_absolute_paths(client):
    d = client.get("/api/connect-info", headers=H).get_json()
    args = d["mcp_json"]["mcpServers"]["memoryhub"]["args"]
    cmd = d["mcp_json"]["mcpServers"]["memoryhub"]["command"]
    assert cmd.startswith("/") and all(a.startswith("/") for a in args)
    assert "recall_memory" in d["instruction"]


def test_map_empty_db_no_500(client):
    r = client.get("/api/map", headers=H)
    assert r.status_code == 200
    d = r.get_json()
    assert not d.get("points") and d.get("note")       # 空库给 note 而非崩
