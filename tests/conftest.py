"""测试基座:临时库 + 路径隔离,全离线(不打任何真实 API、不碰真实 .env / memory.db)。"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "web"))

import provider          # noqa: E402
import review_queue      # noqa: E402
import server            # noqa: E402

H = {"Host": "127.0.0.1:7788"}                       # 过 Host 白名单
HW = {"Host": "127.0.0.1:7788", "X-MemoryHub": "1"}  # 写端点还需自定义头


def make_db(path):
    ddl = open(os.path.join(ROOT, "scripts", "schema.sql"), encoding="utf-8").read()
    con = sqlite3.connect(path)
    con.executescript(ddl)
    con.commit()
    con.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把 server/provider 的所有落盘路径指到 tmp:DB、.env、VERSION、缓存(HOME)。"""
    monkeypatch.setenv("HOME", str(tmp_path))         # update-check 缓存落 tmp
    for k in ("ALIBABA_KEY", "LLM_API_KEY", "EMBED_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)          # 进程环境不得泄入
    db = tmp_path / "memory.db"
    make_db(str(db))
    (tmp_path / "VERSION").write_text("9.9.9\n")
    monkeypatch.setattr(server, "DB", str(db))
    monkeypatch.setattr(server, "HUB", str(tmp_path))
    monkeypatch.setattr(provider, "HUB", str(tmp_path))
    return tmp_path


@pytest.fixture()
def client(env):
    server.app.config["TESTING"] = True
    return server.app.test_client()


def conn(env):
    c = sqlite3.connect(os.path.join(str(env), "memory.db"))
    c.row_factory = sqlite3.Row
    return c
