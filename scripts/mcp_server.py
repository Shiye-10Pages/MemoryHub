#!/usr/bin/env python3
"""MemoryHub · Step 7 — 零依赖 MCP 服务器(stdio / JSON-RPC 2.0)

暴露一个工具 recall_memory,让任何 MCP 客户端(Claude Code 终端 / 桌面客户端)
召回长期记忆。复用 recall.py 引擎。无第三方依赖(纯标准库)。

注册示例(claude_desktop_config.json 或 ~/.claude.json 的 mcpServers):
  "memoryhub": {"command": "python3", "args": ["/path/to/MemoryHub/scripts/mcp_server.py"]}
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recall import recall  # noqa: E402

try:
    _VERSION = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "VERSION"), encoding="utf-8").read().strip()
except Exception:
    _VERSION = "0.1.0"

TOOL = {
    "name": "recall_memory",
    "description": ("从 MemoryHub 召回相关的长期记忆(方法论/决策/经验/SOP/认知/反馈/偏好等),"
                    "返回带【逐字证据 + 置信度 + 来源】的记忆原子。"
                    "在需要回顾过往决策、避免重复决策、了解用户业务背景与既定原则时调用。"),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "自然语言问题或主题"},
            "topk": {"type": "integer", "description": "返回条数,默认 6"},
        },
        "required": ["query"],
    },
}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def fmt(hits):
    if not hits:
        return "（未召回到相关记忆）"
    lines = []
    for i, h in enumerate(hits, 1):
        ctx = (f"情境: {h.get('context')}\n" if h.get("context") else "")
        lines.append(
            f"[{i}] 【{h['type']}】(置信 {h['confidence']}, 来源 {','.join(h['sources'])})\n"
            f"{ctx}"
            f"结论: {h['claim']}\n"
            f"证据(逐字): {h['evidence']}")
    return "\n\n".join(lines)


def handle(msg):
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "memoryhub", "version": _VERSION}}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [TOOL]}})
    elif method == "tools/call":
        p = msg.get("params", {})
        name = p.get("name")
        if name == "recall_memory":
            a = p.get("arguments", {})
            try:
                hits = recall(a["query"], int(a.get("topk", 6)))
                text = fmt(hits)
            except Exception as e:
                text = f"召回失败: {e}"
            send({"jsonrpc": "2.0", "id": mid,
                  "result": {"content": [{"type": "text", "text": text}]}})
        else:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32602, "message": f"unknown tool {name}"}})
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid,
              "error": {"code": -32601, "message": f"method not found: {method}"}})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except Exception:
            continue


if __name__ == "__main__":
    main()
