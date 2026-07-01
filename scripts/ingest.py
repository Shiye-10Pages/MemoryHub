#!/usr/bin/env python3
"""MemoryHub · Stage 1 · Step 2 — ingest

把 ~/.claude/projects/*/*.jsonl 里的真实对话轮次归一为 raw_event,
并把原始行追加进 raw/claude-code/{yyyy-mm}.jsonl 月度归档(append-only)。

原则:
- 只收真实对话:user 的字符串内容 / assistant 的 text 块。
  跳过 tool_use / tool_result / thinking / 元数据(isMeta、system、attachment…)。
- 幂等 + 增量:ingest_cursor 记录每文件处理偏移;event id 哈希去重。
- raw 层保持完整/不可变,信号筛选留给 distill(Step 3)。

用法:
    python3 ingest.py [--dry-run] [--limit N]
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
RAW_DIR = os.path.join(HUB, "raw", "claude-code")
# 单层 glob:只取主会话 transcript(projects/<dir>/<file>.jsonl)。
# 故意排除 <session>/subagents/agent-*.jsonl 子代理 sidechain(低价值,蓝图边界)。
SRC_GLOB = os.path.expanduser("~/.claude/projects/*/*.jsonl")


def event_id(conv_id, seq, role, text):
    payload = f"claude-code|{conv_id}|{seq}|{role}|{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def extract_turn(o):
    """对话轮次 → (role, text);非对话/空 → None。"""
    t = o.get("type")
    if t not in ("user", "assistant") or o.get("isMeta"):
        return None
    content = (o.get("message") or {}).get("content")
    if t == "user":
        if isinstance(content, str) and content.strip():
            return ("user", content.strip())
        return None
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(p for p in parts if p).strip()
        return ("assistant", text) if text else None
    return None


def month_of(ts):
    return ts[:7] if ts and len(ts) >= 7 else "unknown"


def main():
    dry = "--dry-run" in sys.argv
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    os.makedirs(RAW_DIR, exist_ok=True)
    con = sqlite3.connect(DB)
    seen = {r[0] for r in con.execute("SELECT id FROM raw_event")}
    handles = {}
    st = {"files_scanned": 0, "files_new": 0, "files_skipped": 0,
          "new": 0, "user": 0, "assistant": 0}

    for fp in sorted(glob.glob(SRC_GLOB)):
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
            conv = o.get("sessionId") or os.path.splitext(os.path.basename(fp))[0]
            eid = event_id(conv, i, role, text)
            if eid in seen:
                continue
            seen.add(eid)
            month = month_of(o.get("timestamp"))
            if not dry:
                if month not in handles:
                    handles[month] = open(os.path.join(RAW_DIR, f"{month}.jsonl"), "a", encoding="utf-8")
                handles[month].write(lines[i] + "\n")
                meta = json.dumps({
                    "uuid": o.get("uuid"), "parentUuid": o.get("parentUuid"),
                    "gitBranch": o.get("gitBranch"), "version": o.get("version"),
                    "isSidechain": o.get("isSidechain"),
                    "model": (o.get("message") or {}).get("model"),
                }, ensure_ascii=False)
                con.execute(
                    "INSERT OR IGNORE INTO raw_event"
                    "(id,source,project,conv_id,seq,ts,role,text,meta,raw_path) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (eid, "claude-code", o.get("cwd"), conv, i, o.get("timestamp"),
                     role, text, meta, os.path.join(RAW_DIR, f"{month}.jsonl")))
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
    print(("[dry-run] " if dry else "") + "ingest 完成: " + json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
