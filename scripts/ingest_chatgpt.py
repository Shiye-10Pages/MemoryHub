#!/usr/bin/env python3
"""MemoryHub · 工作流/多源连接器 — ChatGPT 网页导出

解析 ChatGPT 官方数据导出里的 conversations.json(树状 mapping 结构),
抽出 user/assistant 消息 → raw_event(source/project='chatgpt')→ 归档 raw/chatgpt/。
之后用 `distill.py --project chatgpt` 走同一条提纯/保真/入库管线。

【怎么拿数据】ChatGPT 网页 → Settings → Data controls → Export data → 邮件收到 zip,
解压取 conversations.json,放到默认路径或用 --file 指定:
  默认: ~/MemoryHub/imports/chatgpt/conversations.json

用法:
  python3 ingest_chatgpt.py [--file <conversations.json>] [--dry-run]
  之后: python3 distill.py --project chatgpt && python3 gate.py --near 0.88 && python3 project.py --commit
"""
import datetime
import hashlib
import json
import os
import sqlite3
import sys

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")
RAW_DIR = os.path.join(HUB, "raw", "chatgpt")
DEFAULT_FILE = os.path.join(HUB, "imports", "chatgpt", "conversations.json")


def iso(ct):
    try:
        return datetime.datetime.utcfromtimestamp(float(ct)).isoformat()[:19] + "Z"
    except Exception:
        return ""


def text_of(msg):
    """从 message.content 取纯文本(兼容 text / multimodal / code)。"""
    c = (msg or {}).get("content") or {}
    parts = c.get("parts") or []
    chunks = [p for p in parts if isinstance(p, str) and p.strip()]
    return "\n".join(chunks).strip()


def messages(conv):
    """按 create_time 顺序产出 (msg_id, role, text, ts)。"""
    out = []
    for node in (conv.get("mapping") or {}).values():
        m = node.get("message")
        if not m:
            continue
        role = (m.get("author") or {}).get("role")
        if role not in ("user", "assistant"):
            continue
        t = text_of(m)
        if not t:
            continue
        out.append((m.get("id") or node.get("id"), role, t, m.get("create_time") or conv.get("create_time")))
    out.sort(key=lambda x: (x[3] or 0))
    return out


def main():
    dry = "--dry-run" in sys.argv
    import glob
    f = sys.argv[sys.argv.index("--file") + 1] if "--file" in sys.argv else DEFAULT_FILE
    # 单文件 / 目录(自动吃 conversations*.json 分片)/ 默认目录递归找分片
    if os.path.isdir(f):
        files = sorted(glob.glob(os.path.join(f, "conversations*.json")))
    elif os.path.exists(f):
        files = [f]
    else:
        files = sorted(glob.glob(os.path.join(os.path.dirname(f), "**", "conversations*.json"),
                                 recursive=True))
    if not files:
        print(f"未找到 conversations*.json(查了 {f} 及其目录)\n"
              f"→ 解压 ChatGPT 导出后,把文件夹或 conversations.json 放到 imports/chatgpt/。")
        return
    os.makedirs(RAW_DIR, exist_ok=True)
    con = sqlite3.connect(DB)
    seen = {r[0] for r in con.execute("SELECT id FROM raw_event WHERE source='chatgpt'")}
    raw_h = None if dry else open(os.path.join(RAW_DIR, "messages.jsonl"), "a", encoding="utf-8")
    st = {"convs": 0, "messages": 0, "raw_new": 0}

    convs = []
    for sf in files:
        sd = json.load(open(sf, encoding="utf-8"))
        convs += sd if isinstance(sd, list) else sd.get("conversations", [])
    print(f"  分片 {len(files)} 个,合计会话 {len(convs)}")
    for conv in convs:
        cid = conv.get("conversation_id") or conv.get("id") or \
            hashlib.sha256((conv.get("title", "") + str(conv.get("create_time"))).encode()).hexdigest()[:16]
        st["convs"] += 1
        for msg_id, role, text, ct in messages(conv):
            st["messages"] += 1
            eid = "gpt_" + hashlib.sha256(f"{cid}|{msg_id}".encode()).hexdigest()[:20]
            if eid in seen or dry:
                continue
            ts = iso(ct)
            raw_h.write(json.dumps({"conv": cid, "msg": msg_id, "role": role, "ts": ts, "text": text},
                                   ensure_ascii=False) + "\n")
            con.execute(
                "INSERT OR IGNORE INTO raw_event(id,source,project,conv_id,ts,role,text,meta,raw_path) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (eid, "chatgpt", "chatgpt", cid, ts, role, text,
                 json.dumps({"title": conv.get("title")}, ensure_ascii=False),
                 os.path.join(RAW_DIR, "messages.jsonl")))
            seen.add(eid)
            st["raw_new"] += 1

    if not dry:
        raw_h.close()
        con.commit()
    con.close()
    print(("[dry-run] " if dry else "") + "chatgpt 采集: " + json.dumps(st, ensure_ascii=False))
    if not dry and st["raw_new"]:
        print("→ 下一步: python3 scripts/distill.py --project chatgpt && "
              "python3 scripts/gate.py --near 0.88 && python3 scripts/project.py --commit")


if __name__ == "__main__":
    main()
