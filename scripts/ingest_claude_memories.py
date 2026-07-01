#!/usr/bin/env python3
"""MemoryHub · 多源连接器 — Claude 云端记忆(memories.json)

把 Claude 导出里的 memories.json(它对你的 AI 推断记忆)拆成候选:
- conversations_memory 按 **小节标题** 切分;project_memories 每条一份。
- ⚠️ AI 推断 → 一律 force_review=True,经 gate 进【人工闸】等你确认,绝不自动入库
  (遵循你的原则:AI 判断只提名,人类确认才入库)。
- 源可信度低(0.5–0.6)。

默认从 claude-web 导出的 batch 目录自动找 memories.json;也可 --file 指定。
用法: python3 ingest_claude_memories.py [--file <memories.json>] [--dry-run]
      之后: python3 gate.py --near 0.88   # 候选进人工闸
            python3 review_queue.py        # 逐条确认
"""
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")
RAW_DIR = os.path.join(HUB, "raw", "claude-memory")
STAGING = os.path.join(HUB, "staging")


def find_file():
    if "--file" in sys.argv:
        return sys.argv[sys.argv.index("--file") + 1]
    hits = glob.glob(os.path.join(HUB, "imports", "claude-web", "**", "memories.json"), recursive=True)
    return hits[0] if hits else os.path.join(HUB, "imports", "claude-web", "memories.json")


def split_sections(text):
    """按顶层 **标题** 切分 → [(title, body)]。"""
    out, h, buf = [], None, []
    for line in text.split("\n"):
        m = re.match(r"^\*\*(.+?)\*\*$", line.strip())
        if m:
            if h and "\n".join(buf).strip():
                out.append((h, "\n".join(buf).strip()))
            h, buf = m.group(1), []
        else:
            buf.append(line)
    if h and "\n".join(buf).strip():
        out.append((h, "\n".join(buf).strip()))
    return out


def main():
    dry = "--dry-run" in sys.argv
    f = find_file()
    if not os.path.exists(f):
        print(f"未找到 memories.json: {f}")
        return
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(STAGING, exist_ok=True)
    con = sqlite3.connect(DB)
    seen = {r[0] for r in con.execute("SELECT id FROM raw_event WHERE source='claude-memory'")}
    out = None if dry else open(os.path.join(STAGING, "candidates.jsonl"), "a", encoding="utf-8")
    raw_h = None if dry else open(os.path.join(RAW_DIR, "memories.jsonl"), "a", encoding="utf-8")
    st = {"sections": 0, "project_memories": 0, "memory_files": 0, "candidates": 0, "raw_new": 0}

    m = json.load(open(f, encoding="utf-8"))
    m = m[0] if isinstance(m, list) else m

    units = []  # (kind, key, claim, text)
    for title, body in split_sections(m.get("conversations_memory") or ""):
        st["sections"] += 1
        units.append(("conv", title, f"Claude记忆 · {title}", body))
    for k, v in (m.get("project_memories") or {}).items():
        if isinstance(v, str) and v.strip():
            st["project_memories"] += 1
            units.append(("proj", k, f"Claude项目记忆 · {k[:8]}", v.strip()))
    # memory_files:claude 整理的 /areas/*.md 结构化记忆文件(导出实际字段,旧版漏采)
    for mf in (m.get("memory_files") or []):
        path = (mf.get("path") or "").strip()
        content = (mf.get("content") or "").strip()
        if not content:
            continue
        dm = re.search(r"^description:\s*(.+)$", content, re.M)
        name = (os.path.splitext(os.path.basename(path))[0] or path) if path else "memory_file"
        claim = f"Claude记忆文件 · {(dm.group(1).strip() if dm else name)}"
        st["memory_files"] += 1
        units.append(("file", path or name, claim, content))

    for kind, key, claim, text in units:
        eid = "cmem_" + hashlib.sha256(f"{kind}|{key}".encode()).hexdigest()[:18]
        if eid not in seen and not dry:
            raw_h.write(json.dumps({"kind": kind, "key": key, "text": text}, ensure_ascii=False) + "\n")
            con.execute(
                "INSERT OR IGNORE INTO raw_event(id,source,project,conv_id,ts,role,text,meta,raw_path) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (eid, "claude-memory", "claude-memory", key, "", "assistant", text,
                 json.dumps({"kind": kind, "claim": claim}, ensure_ascii=False),
                 os.path.join(RAW_DIR, "memories.jsonl")))
            st["raw_new"] += 1
        cand = {
            "type": "认知", "claim": claim, "evidence": text,
            "filters": ["认知"], "impact": True, "force_review": True,
            "sources": [{"source": "claude-memory", "conv_id": key,
                         "uri": "claude-web export / memories.json", "ts": "", "project": "claude-memory"}],
            "sr": 0.5 if kind == "proj" else 0.6, "em": 1.0,
            "extractor_model": "rule:claude-memory",
        }
        st["candidates"] += 1
        if not dry:
            out.write(json.dumps(cand, ensure_ascii=False) + "\n")

    if not dry:
        out.close()
        raw_h.close()
        con.commit()
    con.close()
    print(("[dry-run] " if dry else "") + "claude-memory 采集: " + json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
