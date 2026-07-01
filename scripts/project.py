#!/usr/bin/env python3
"""MemoryHub · Stage 1 · Step 6 — project

把 memory_item(现行、未失效)投影成 Obsidian markdown 卡片(frontmatter 契约),
写入 vault/cards/。可选 --commit 做一次 git 提交。

用法: python3 project.py [--commit]
"""
import json
import os
import re
import sqlite3
import subprocess
import sys

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")
VAULT = os.path.join(HUB, "vault")
CARDS = os.path.join(VAULT, "cards")


def slug(s):
    s = re.sub(r"[^\w一-鿿]+", "-", (s or "").strip())
    return s.strip("-")[:24] or "memory"


def card(row):
    (mid, typ, claim, evidence, sources, conf, vfrom, vuntil,
     status, review, links) = row
    srcs = json.loads(sources or "[]")
    src_line = "; ".join(f"{s.get('source')}:{(s.get('conv_id') or '')[:8]}"
                         f"({(s.get('project') or '').split('/')[-1]})" for s in srcs[:5])
    fm = [
        "---", f"id: {mid}", f"type: {typ}", f"confidence: {conf}",
        f"status: {status}", f"valid_from: {vfrom or ''}", f"valid_until: {vuntil or ''}",
        f"review_date: {review or ''}", f"source_count: {len(srcs)}",
        "---", "",
        f"# 【{typ}】{claim}", "",
        "## 证据(逐字)", f"> {(evidence or '').strip()}", "",
        "## 来源", src_line or "—", "",
    ]
    if links and links != "[]":
        fm += ["## 关联", " ".join(f"[[{x}]]" for x in json.loads(links)), ""]
    return "\n".join(fm)


def main():
    os.makedirs(CARDS, exist_ok=True)
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT id,type,claim,evidence,sources,confidence,valid_from,valid_until,"
        "status,review_date,links FROM memory_item "
        "WHERE valid_until IS NULL ORDER BY confidence DESC"
    ).fetchall()
    con.close()

    for r in rows:
        path = os.path.join(CARDS, f"{r[1]}-{slug(r[2])}-{r[0][:6]}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(card(r))
    print(f"已投影 {len(rows)} 张记忆卡片 → {CARDS}")

    if "--commit" in sys.argv:
        g = ["git", "-C", VAULT]
        subprocess.run(g + ["add", "cards", "README.md", ".gitignore"], check=False)
        msg = (f"feat: 首批 Claude Code 对话沉淀为 {len(rows)} 张记忆卡片(Stage 1)\n\n"
               "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>")
        r = subprocess.run(g + ["commit", "-m", msg], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())


if __name__ == "__main__":
    main()
