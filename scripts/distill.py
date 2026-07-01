#!/usr/bin/env python3
"""MemoryHub · Stage 1 · Step 3 — distill

把 raw_event 按会话归一为 episode(canonical_document),分窗喂给 qwen3-max,
按 memory_item 契约抽取候选记忆原子;就地执行【溯源闸】(evidence 必须逐字命中,
否则丢弃),候选写入 staging/candidates.jsonl 供 Step 4(gate)处理。

用法:
    python3 distill.py [--limit N] [--conv ID] [--model qwen3-max] [--window 8000]
默认 --limit 2(试点);不传 --conv 则取 user 轮次最多的前 N 个会话。
"""
import json
import os
import sqlite3
import sys
import time

import requests

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")
STAGING = os.path.join(HUB, "staging")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import config            # noqa: E402
from memory_prompt import build_prompt  # noqa: E402


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def load_allowlist():
    for name in ("sources.json", "sources.json.example"):
        path = os.path.join(HUB, name)
        if os.path.exists(path):
            return json.load(open(path, encoding="utf-8")).get("approved_patterns", [])
    return []


def pick_convs(con, limit, conv, project):
    if conv:
        return [conv]
    where, params = "role='user'", []
    pats = [project] if project else load_allowlist()  # 无 --project 则用来源闸清单
    if pats:
        where += " AND (" + " OR ".join("project LIKE ?" for _ in pats) + ")"
        params = [f"%{p}%" for p in pats]
    q = (f"SELECT conv_id FROM raw_event WHERE {where} "
         "GROUP BY conv_id ORDER BY count(*) DESC")
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    return [r[0] for r in con.execute(q, params).fetchall()]


def assemble(con, conv):
    rows = con.execute(
        "SELECT role,text,ts,project,source FROM raw_event WHERE conv_id=? ORDER BY seq", (conv,)
    ).fetchall()
    project = rows[0][3] if rows else None
    source = rows[0][4] if rows else None
    ts_start, ts_end = (rows[0][2], rows[-1][2]) if rows else (None, None)
    text = "\n\n".join(("用户" if r[0] == "user" else "助手") + ": " + r[1] for r in rows)
    return text, project, source, ts_start, ts_end


def windows(text, size):
    if len(text) <= size:
        return [text]
    step = int(size * 0.92)  # 8% 重叠,够防切断又少重复
    return [text[i:i + size] for i in range(0, len(text), step)]


def call_qwen(model, prompt):
    if not config.ALIBABA_KEY:
        raise RuntimeError("缺少 ALIBABA_KEY: 请在 MemoryHub/.env 中配置")
    r = requests.post(config.ALIBABA_ENDPOINT,
                      headers={"Authorization": f"Bearer {config.ALIBABA_KEY}",
                               "Content-Type": "application/json"},
                      json={"model": model,
                            "input": {"messages": [{"role": "user", "content": prompt}]},
                            "parameters": {"temperature": 0.1, "result_format": "message"}},
                      timeout=180)
    if r.status_code != 200:
        raise Exception(f"{r.status_code}: {r.text[:200]}")
    return r.json()["output"]["choices"][0]["message"]["content"]


def parse(content):
    c = content.strip()
    for pre in ("```json", "```"):
        if c.startswith(pre):
            c = c[len(pre):]
    if c.endswith("```"):
        c = c[:-3]
    data = json.loads(c.strip())
    return data if isinstance(data, list) else [data]


def main():
    limit = int(arg("--limit")) if "--limit" in sys.argv else None  # 默认不限:来源闸内全部
    conv = arg("--conv")
    project = arg("--project")
    model = arg("--model", "qwen3-max")
    win = int(arg("--window", "8000"))
    force = "--force" in sys.argv
    os.makedirs(STAGING, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30.0)  # 面板并发写时礼让等待,不硬报 locked
    done = {r[0] for r in con.execute("SELECT conv_id FROM canonical_document")}
    convs = pick_convs(con, limit, conv, project)
    out = open(os.path.join(STAGING, "candidates.jsonl"), "a", encoding="utf-8")
    st = {"convs": 0, "skipped_done": 0, "windows": 0, "raw_items": 0,
          "kept": 0, "rejected_no_evidence": 0}

    for cid in convs:
        if cid in done and not force:        # 增量:已提纯会话跳过
            st["skipped_done"] += 1
            continue
        text, project_p, src, ts0, ts1 = assemble(con, cid)
        if not text.strip():
            continue
        project = project_p
        src = src or "claude-code"
        st["convs"] += 1
        doc_id = f"{src}:{cid}"
        for w in windows(text, win):
            st["windows"] += 1
            try:
                items = parse(call_qwen(model, build_prompt(w)))
            except Exception as e:
                print(f"  [{cid[:8]}] 窗口失败: {str(e)[:120]}")
                continue
            for it in items:
                st["raw_items"] += 1
                ev = (it.get("evidence") or "").strip()
                if not ev or ev not in w:          # 溯源闸:逐字未命中 → 拒收
                    st["rejected_no_evidence"] += 1
                    continue
                it["sources"] = [{"source": src, "conv_id": cid,
                                  "uri": doc_id, "ts": ts1, "project": project}]
                it["extractor_model"] = model
                out.write(json.dumps(it, ensure_ascii=False) + "\n")
                st["kept"] += 1
            time.sleep(0.3)
        # canonical_document 入库放到窗口处理完后:LLM 调用期间不持写锁,
        # 面板审批/写动作可并发;会话全部处理完才标记 done,崩溃可断点续跑。
        con.execute(
            "INSERT OR REPLACE INTO canonical_document"
            "(id,source,project,conv_id,title,text,lang,uri,ts_start,ts_end) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (doc_id, src, project, cid, None, text, "zh", doc_id, ts0, ts1))
        con.commit()

    out.close()
    con.close()
    print("distill 完成: " + json.dumps(st, ensure_ascii=False))
    print(f"候选已写入: {os.path.join(STAGING, 'candidates.jsonl')}")


if __name__ == "__main__":
    main()
