#!/usr/bin/env python3
"""MemoryHub · 连接器 — Codex CLI 本地会话直读(免导出)

把 ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl 里的真实对话轮次归一为 raw_event,
并把原始行追加进 raw/codex/{yyyy-mm}.jsonl 月度归档(append-only)。

行格式:{timestamp, type, payload}
- type=session_meta → payload.{id,cwd}:会话 id 与项目目录
- type=response_item + payload.type=message + role∈{user,assistant}
  → content=[{type:input_text|output_text, text}]
- 跳过 reasoning / function_call / developer(系统提示)等非对话项。

原则与 ingest.py(Claude Code)一致:幂等 + 增量(ingest_cursor 按文件
mtime/offset)、event id 哈希去重、raw 层完整不可变。

用法:
    python3 ingest_codex.py [--dry-run] [--limit N]
"""
import datetime
import glob
import hashlib
import json
import os
import sqlite3
import sys

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")
RAW_DIR = os.path.join(HUB, "raw", "codex")
SRC_GLOB = os.path.expanduser("~/.codex/sessions/*/*/*/rollout-*.jsonl")

# 用户消息里的合成包裹块(环境上下文等),不是人说的话 → 跳过
_WRAPPERS = ("<environment_context", "<user_instructions", "<turn_context",
             "<permissions", "<ide_context", "<system")


def event_id(conv_id, seq, role, text):
    payload = f"codex|{conv_id}|{seq}|{role}|{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def extract_turn(o):
    """rollout 行 → (role, text);非对话/空/合成块 → None。"""
    if o.get("type") != "response_item":
        return None
    p = o.get("payload") or {}
    if p.get("type") != "message" or p.get("role") not in ("user", "assistant"):
        return None
    parts = [b.get("text", "") for b in (p.get("content") or [])
             if isinstance(b, dict) and b.get("type") in ("input_text", "output_text")]
    text = "\n".join(x for x in parts if x).strip()
    if not text or text.startswith(_WRAPPERS):
        return None
    return (p["role"], text)


def month_of(ts):
    return ts[:7] if ts and len(ts) >= 7 else "unknown"


def main():
    dry = "--dry-run" in sys.argv
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    files = sorted(glob.glob(SRC_GLOB))
    if not files:
        print("未找到本机 Codex 会话(~/.codex/sessions 为空或未安装 Codex)。")
        return
    os.makedirs(RAW_DIR, exist_ok=True)
    con = sqlite3.connect(DB)
    seen = {r[0] for r in con.execute("SELECT id FROM raw_event")}
    handles = {}
    st = {"files_scanned": 0, "files_new": 0, "files_skipped": 0,
          "new": 0, "user": 0, "assistant": 0}

    for fp in files:
        st["files_scanned"] += 1
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fp)).isoformat()
        cur = con.execute(
            "SELECT file_mtime,last_offset FROM ingest_cursor WHERE file_path=?", (fp,)
        ).fetchone()
        if cur and cur[0] == mtime:
            st["files_skipped"] += 1
            continue
        start = cur[1] if cur else 0
        st["files_new"] += 1
        lines = open(fp, encoding="utf-8").read().splitlines()
        conv, cwd = os.path.basename(fp)[:-6], None      # 兜底:文件名当会话 id
        try:                                             # 首行 session_meta:会话 id + 项目目录
            head = json.loads(lines[0])
            if head.get("type") == "session_meta":
                meta_p = head.get("payload") or {}
                conv = meta_p.get("id") or conv
                cwd = meta_p.get("cwd")
        except Exception:
            pass
        processed_to = len(lines)
        for i in range(start, len(lines)):
            if limit and st["new"] >= limit:
                processed_to = i
                break
            try:
                o = json.loads(lines[i])
            except Exception:
                continue
            turn = extract_turn(o)
            if not turn:
                continue
            role, text = turn
            eid = event_id(conv, i, role, text)
            if eid in seen:
                continue
            seen.add(eid)
            month = month_of(o.get("timestamp"))
            if not dry:
                if month not in handles:
                    handles[month] = open(os.path.join(RAW_DIR, f"{month}.jsonl"), "a", encoding="utf-8")
                handles[month].write(lines[i] + "\n")
                con.execute(
                    "INSERT OR IGNORE INTO raw_event"
                    "(id,source,project,conv_id,seq,ts,role,text,meta,raw_path) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (eid, "codex", cwd, conv, i, o.get("timestamp"), role, text,
                     "{}", os.path.join(RAW_DIR, f"{month}.jsonl")))
            st["new"] += 1
            st[role] += 1
        if not dry:
            con.execute(
                "INSERT OR REPLACE INTO ingest_cursor"
                "(file_path,file_mtime,last_offset,last_run) VALUES(?,?,?,datetime('now'))",
                (fp, mtime, processed_to))
            con.commit()
        if limit and st["new"] >= limit:
            break

    for h in handles.values():
        h.close()
    con.close()
    print(("[dry-run] " if dry else "") + "codex ingest 完成: " + json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
