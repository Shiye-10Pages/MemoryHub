#!/usr/bin/env python3
"""MemoryHub · 多源连接器 — claude.ai 网页聊天

解析 Claude 官方数据导出里的 conversations.json(扁平 chat_messages 结构,
区别于 ChatGPT 的树状 mapping),抽 human/assistant 消息 →
raw_event(source/project='claude-web')→ 归档 raw/claude-web/。
之后用 `distill.py --project claude-web` 走同一条提纯/保真/入库管线。

【怎么拿数据】claude.ai 网页 → Settings → Privacy/Account → Export data →
邮件收到下载链接 → 解压取 conversations.json,放到默认路径或 --file 指定:
  默认: ~/MemoryHub/imports/claude-web/conversations.json

用法:
  python3 ingest_claude_web.py [--file <conversations.json>] [--dry-run]
  之后: python3 distill.py --project claude-web && python3 gate.py --near 0.88 && python3 project.py --commit
"""
import hashlib
import json
import os
import sqlite3
import sys

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")
RAW_DIR = os.path.join(HUB, "raw", "claude-web")
DEFAULT_FILE = os.path.join(HUB, "imports", "claude-web", "conversations.json")

ROLE = {"human": "user", "assistant": "assistant", "user": "user"}


def text_of(m):
    """兼容 m['text'] 与新版 m['content']=[{type:text,text:..}]。"""
    if isinstance(m.get("text"), str) and m["text"].strip():
        return m["text"].strip()
    chunks = []
    for c in (m.get("content") or []):
        if isinstance(c, dict) and isinstance(c.get("text"), str):
            chunks.append(c["text"])
    return "\n".join(chunks).strip()


def main():
    dry = "--dry-run" in sys.argv
    f = sys.argv[sys.argv.index("--file") + 1] if "--file" in sys.argv else DEFAULT_FILE
    if not os.path.exists(f):
        print(f"未找到导出文件: {f}\n"
              f"→ claude.ai 设置导出数据,解压后把 conversations.json 放到该路径(或 --file 指定)。")
        return
    os.makedirs(RAW_DIR, exist_ok=True)
    con = sqlite3.connect(DB)
    seen = {r[0] for r in con.execute("SELECT id FROM raw_event WHERE source='claude-web'")}
    raw_h = None if dry else open(os.path.join(RAW_DIR, "messages.jsonl"), "a", encoding="utf-8")
    st = {"convs": 0, "messages": 0, "raw_new": 0}

    data = json.load(open(f, encoding="utf-8"))
    convs = data if isinstance(data, list) else data.get("conversations", [])
    for conv in convs:
        cid = conv.get("uuid") or conv.get("id") or \
            hashlib.sha256((conv.get("name", "") + str(conv.get("created_at"))).encode()).hexdigest()[:16]
        st["convs"] += 1
        for m in (conv.get("chat_messages") or conv.get("messages") or []):
            role = ROLE.get(m.get("sender") or m.get("role"))
            if role not in ("user", "assistant"):
                continue
            t = text_of(m)
            # 救回上传文档正文:attachments[].extracted_content(claude 已提取的文本)
            atts = [(a.get("extracted_content") or "").strip()
                    for a in (m.get("attachments") or []) if (a.get("extracted_content") or "").strip()]
            if atts:
                blob = "\n\n".join(atts)
                t = (t + "\n\n[上传附件正文]\n" + blob).strip() if t else blob
            if not t:
                continue
            st["messages"] += 1
            msg_id = m.get("uuid") or hashlib.sha256(t.encode()).hexdigest()[:16]
            eid = "cw_" + hashlib.sha256(f"{cid}|{msg_id}".encode()).hexdigest()[:20]
            if eid in seen or dry:
                continue
            # file 引用(图片等二进制不在导出内,只留 uuid/名,知道"这里曾有文件")
            files = [{"uuid": fo.get("file_uuid"), "name": fo.get("file_name")}
                     for fo in (m.get("files") or []) if fo.get("file_uuid") or fo.get("file_name")]
            att_names = [a.get("file_name") for a in (m.get("attachments") or []) if a.get("file_name")]
            meta = {"title": conv.get("name")}
            if files:
                meta["files"] = files
            if att_names:
                meta["attachments"] = att_names
            ts = (m.get("created_at") or conv.get("created_at") or "")[:19]
            raw_h.write(json.dumps({"conv": cid, "msg": msg_id, "role": role, "ts": ts, "text": t},
                                   ensure_ascii=False) + "\n")
            con.execute(
                "INSERT OR IGNORE INTO raw_event(id,source,project,conv_id,ts,role,text,meta,raw_path) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (eid, "claude-web", "claude-web", cid, ts, role, t,
                 json.dumps(meta, ensure_ascii=False),
                 os.path.join(RAW_DIR, "messages.jsonl")))
            seen.add(eid)
            st["raw_new"] += 1

    if not dry:
        raw_h.close()
        con.commit()
    con.close()
    print(("[dry-run] " if dry else "") + "claude-web 采集: " + json.dumps(st, ensure_ascii=False))
    if not dry and st["raw_new"]:
        print("→ 下一步: python3 scripts/distill.py --project claude-web && "
              "python3 scripts/gate.py --near 0.88 && python3 scripts/project.py --commit")


if __name__ == "__main__":
    main()
