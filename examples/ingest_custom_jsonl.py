#!/usr/bin/env python3
"""MemoryHub · 自定义源连接器模板(规则直采,不调 LLM)

演示如何把「你自己的、一行一条 JSONL 的日志 / 反馈 / 笔记」采成记忆候选:
- 你的原话即 claim 即 evidence(逐字、零编造 —— 保真铁律)。
- 第一方人类记录 + 规则抽取 → 置信度最高档(sr=0.95, em=1.0)。
- 琐碎行("ok / 没问题 / 可以了"…)过滤掉。
原始行归档到 raw/custom/,并入 raw_event;实质条目追加到 staging/candidates.jsonl,
之后由 gate.py 统一去重 / 置信度 / 人工闸 / 写库(与对话提纯同一出口)。

改用于你自己的源:改下面标了 TODO 的三处(SRC 路径、SOURCE 名、正文字段名)即可。
用法: python3 examples/ingest_custom_jsonl.py [--dry-run]
"""
import hashlib
import json
import os
import re
import sqlite3
import sys

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")
SOURCE = "custom"                                        # TODO: 给你的源起个名
RAW_DIR = os.path.join(HUB, "raw", SOURCE)
STAGING = os.path.join(HUB, "staging")
SRC = os.path.expanduser("~/my-notes/feedback.jsonl")   # TODO: 改成你自己的源文件

TRIVIAL = {"ok", "好", "好的", "行", "可以", "可以了", "没问题", "继续", "搞定", "嗯", "对"}
BUSINESS_KW = ["定价", "价格", "收入", "营收", "销售", "客单", "单价", "商业模式", "变现",
               "方向", "放弃", "战略", "融资", "招聘", "离职", "课程", "付费", "订阅"]


def norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()


def substantive(note):
    n = norm(note)
    return len(n) >= 12 and n not in TRIVIAL


def main():
    dry = "--dry-run" in sys.argv
    if not os.path.exists(SRC):
        print(f"无 {SRC}(把 SRC 改成你自己的 JSONL 源)")
        return
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(STAGING, exist_ok=True)
    con = sqlite3.connect(DB)
    seen = {r[0] for r in con.execute("SELECT id FROM raw_event WHERE source=?", (SOURCE,))}
    out = None if dry else open(os.path.join(STAGING, "candidates.jsonl"), "a", encoding="utf-8")
    raw_h = {}
    st = {"total": 0, "trivial_skip": 0, "candidates": 0, "raw_new": 0}

    for line in open(SRC, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        st["total"] += 1
        note = (o.get("note") or "").strip()              # TODO: 换成你 JSON 里的正文字段名
        ts = o.get("ts", "")
        eid = hashlib.sha256(f"{SOURCE}|{ts}|{note}".encode("utf-8")).hexdigest()[:24]
        month = ts[:7] if len(ts) >= 7 else "unknown"

        if eid not in seen and not dry:                       # 原始归档 + raw_event
            if month not in raw_h:
                raw_h[month] = open(os.path.join(RAW_DIR, f"{month}.jsonl"), "a", encoding="utf-8")
            raw_h[month].write(line + "\n")
            con.execute(
                "INSERT OR IGNORE INTO raw_event"
                "(id,source,project,conv_id,ts,role,text,meta,raw_path) VALUES(?,?,?,?,?,?,?,?,?)",
                (eid, SOURCE, None, "", ts, "user", note, json.dumps({}, ensure_ascii=False),
                 os.path.join(RAW_DIR, f"{month}.jsonl")))
            st["raw_new"] += 1

        if not substantive(note):                             # 琐碎确认不沉淀
            st["trivial_skip"] += 1
            continue

        cand = {
            "type": "反馈", "claim": note, "evidence": note,   # evidence 必须逐字来自源
            "filters": ["反馈"] + (["影响"] if any(k in note for k in BUSINESS_KW) else []),
            "impact": any(k in note for k in BUSINESS_KW),
            "sources": [{"source": SOURCE, "conv_id": "", "uri": SRC, "ts": ts, "project": SOURCE}],
            "sr": 0.95, "em": 1.0,                             # 第一方人类记录 + 规则抽取 → 最高档
            "extractor_model": "rule",
        }
        st["candidates"] += 1
        if not dry:
            out.write(json.dumps(cand, ensure_ascii=False) + "\n")

    if not dry:
        out.close()
        for h in raw_h.values():
            h.close()
        con.commit()
    con.close()
    print(("[dry-run] " if dry else "") + f"{SOURCE} 采集: " + json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
