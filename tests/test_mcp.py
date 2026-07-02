"""MCP server:JSON-RPC 往返、版本一致性、工具面(公开版只许 recall_memory)。"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rpc(lines):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "mcp_server.py")],
                       input="\n".join(json.dumps(x) for x in lines) + "\n",
                       capture_output=True, text=True, timeout=30)
    return [json.loads(line) for line in p.stdout.splitlines() if line.strip()]


def test_initialize_version_matches_version_file():
    out = _rpc([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}])
    ver = open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
    assert out[0]["result"]["serverInfo"]["version"] == ver


def test_tools_list_only_recall_memory():
    out = _rpc([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}])
    tools = [t["name"] for t in out[1]["result"]["tools"]]
    assert tools == ["recall_memory"]                   # 素材线绝不出现在公开版
