"""拖拽导入的接线与文案判据回归(2026-07-30 用户反馈的两个 bug)。

一、原始对话类的源(claude-web / chatgpt)只写 raw_event,候选要靠 distill 产出;
    导入端点漏了这一步 → gate 无米下锅 → 永远回"没有可导入的新记忆"。
二、成功与否原本只数待确认队列,而 gate 把有把握的候选直接写进 memory_item,
    于是"N 条已入库"被说成"没导入"。

测的是接线顺序与文案判据,子进程全部打桩,不打任何真实 API。
"""
import io
import json
import os
import sqlite3

import server
from conftest import HW

CLAUDE_WEB = json.dumps([{
    "uuid": "c1", "name": "选址", "created_at": "2026-07-20T08:00:00Z",
    "chat_messages": [{"uuid": "m1", "sender": "human", "created_at": "2026-07-20T08:00:00Z",
                       "text": "我打算在杭州西湖区开咖啡店,预算 45 万。"}],
}]).encode()

MEMORIES = json.dumps({"project_memories": {"x": "用户在杭州西湖区开咖啡店,预算 45 万。"}}).encode()


def _stub_pipeline(monkeypatch, env, calls, gate_writes=0):
    """管线子进程换成记账桩:记调用顺序,可选模拟 gate 直接写库(不进队列)。"""
    monkeypatch.setattr(server, "_alibaba_key", lambda: "k")
    monkeypatch.setattr(server, "PIPELINE_LOCK",
                        os.path.join(str(env), "staging", "pipeline.lock.d"))

    def fake_run_step(script, *args, timeout=1800):
        calls.append(script)
        if script == "gate.py" and gate_writes:
            c = sqlite3.connect(os.path.join(str(env), "memory.db"))
            for i in range(gate_writes):
                c.execute("INSERT INTO memory_item(id,type,claim,evidence,sources) "
                          "VALUES(?,?,?,?,?)",
                          (f"m{i}", "事实", "杭州开咖啡店", "预算 45 万", "[]"))
            c.commit()
            c.close()
        return True, ""

    monkeypatch.setattr(server, "_run_step", fake_run_step)


def _import(client, body, name="conversations.json"):
    return client.post("/api/import", headers=HW, content_type="multipart/form-data",
                       data={"file": (io.BytesIO(body), name)})


def test_claude_web_import_runs_distill_between_connector_and_gate(client, env, monkeypatch):
    calls = []
    _stub_pipeline(monkeypatch, env, calls)
    assert _import(client, CLAUDE_WEB).status_code == 200
    assert calls == ["ingest_claude_web.py", "distill.py", "gate.py"]


def test_memories_import_skips_distill(client, env, monkeypatch):
    """云端记忆已成型,连接器自己产候选 → 不该多花一次 LLM 提纯。"""
    calls = []
    _stub_pipeline(monkeypatch, env, calls)
    assert _import(client, MEMORIES, "memories.json").status_code == 200
    assert calls == ["ingest_claude_memories.py", "gate.py"]


def test_success_counts_library_writes_not_only_queue(client, env, monkeypatch):
    """gate 直接入库 3 条、队列为空:必须报"已入库",不能报"没有新增记忆"。"""
    _stub_pipeline(monkeypatch, env, [], gate_writes=3)
    d = _import(client, CLAUDE_WEB).get_json()
    assert d["ok"] and d["added"] == 3 and d["queued"] == 0
    assert "已入库" in d["message"] and "没有新增记忆" not in d["message"]


def test_no_new_memory_reports_honestly(client, env, monkeypatch):
    """确实没进任何东西时,才允许说"已导过"。"""
    _stub_pipeline(monkeypatch, env, [])
    d = _import(client, CLAUDE_WEB).get_json()
    assert d["ok"] and d["added"] == 0 and d["queued"] == 0
    assert "没有新增记忆" in d["message"]


def test_distill_failure_is_reported_not_swallowed(client, env, monkeypatch):
    calls = []
    _stub_pipeline(monkeypatch, env, calls)
    real = server._run_step

    def fail_on_distill(script, *args, timeout=1800):
        if script == "distill.py":
            return False, "boom"
        return real(script, *args, timeout=timeout)

    monkeypatch.setattr(server, "_run_step", fail_on_distill)
    r = _import(client, CLAUDE_WEB)
    assert r.status_code == 500
    d = r.get_json()
    assert d["ok"] is False and "提纯" in d["message"]
    assert "gate.py" not in calls          # 提纯没成,不该继续往下跑


def test_update_precheck_ignores_untracked_files(client, env, monkeypatch):
    """未跟踪文件(用户放进来的导出 zip / 笔记 / 库备份)不该拦住一键更新。"""
    seen = []

    def fake_git(args, timeout=60):
        seen.append(args)
        if args[0] == "status":
            return 0, " M scripts/web/server.py"      # 只剩被跟踪文件的改动
        return 0, ""

    monkeypatch.setattr(server, "_git", fake_git)
    os.makedirs(os.path.join(str(env), ".git"), exist_ok=True)   # 装成 git 安装
    d = client.post("/api/update-apply", headers=HW).get_json()
    assert "--untracked-files=no" in seen[0]          # 判据本身:不数 ?? 项
    assert d["ok"] is False and "scripts/web/server.py" in d["message"]   # 且指名道姓
