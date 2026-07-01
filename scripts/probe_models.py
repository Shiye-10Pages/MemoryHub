#!/usr/bin/env python3
"""保真探针 — 对候选模型做"逐字证据"保真对比。

做法:取一段真实对话 → 让每个候选模型按 memory_item 契约抽取 →
机器核验每条 evidence 是否为原文精确子串(编造则判失)→ 打分对比。
保真分 = 逐字命中的条目数 / 总条目数。

用法: python3 probe_models.py
依赖: MemoryHub/.env 密钥与端点。
"""
import json
import os
import sqlite3
import sys

import requests

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HUB, "memory.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import config  # noqa: E402

MEMORY_SYS = """你是记忆提纯器。从下面的对话里抽取"值得长期记住"的记忆原子。

只抽取满足以下至少一条过滤器的内容:
- 洞察:改变了对某事的理解
- 行动:能指导未来决策/操作
- 复用:适用于当前场景之外
- 影响:涉及定价/方向/收入等高影响决策(命中则必抽)

每条原子输出字段:
- type: 方法论|决策|经验|SOP|认知|反馈|事实|偏好|关系
- claim: 一句话结论,自包含
- evidence: 支撑该结论的【原文逐字片段】,必须是输入文本里的精确子串,原样复制,不得改写/概括/拼接
- filters: 命中的过滤器数组
- impact: 是否影响定价/方向/收入(true/false)

铁律:evidence 必须能在输入中逐字找到;找不到逐字依据的结论,宁可不输出。
只输出 JSON 数组,不要 markdown 代码块,不要任何解释。无可抽取则输出 []。"""


def pick_snippet(max_chars=3500):
    con = sqlite3.connect(DB)
    conv = con.execute(
        "SELECT conv_id FROM raw_event WHERE role='user' "
        "GROUP BY conv_id ORDER BY count(*) DESC LIMIT 1"
    ).fetchone()[0]
    rows = con.execute(
        "SELECT role,text FROM raw_event WHERE conv_id=? ORDER BY seq", (conv,)
    ).fetchall()
    con.close()
    buf = []
    for role, text in rows:
        buf.append(("用户" if role == "user" else "助手") + ": " + text)
    return conv, ("\n\n".join(buf))[:max_chars]


def parse_json(content):
    c = content.strip()
    if c.startswith("```json"):
        c = c[7:]
    if c.startswith("```"):
        c = c[3:]
    if c.endswith("```"):
        c = c[:-3]
    c = c.strip()
    data = json.loads(c)
    return data if isinstance(data, list) else [data]


def call_alibaba(model, prompt):
    if not config.ALIBABA_KEY:
        raise RuntimeError("缺少 ALIBABA_KEY: 请在 MemoryHub/.env 中配置")
    r = requests.post(config.ALIBABA_ENDPOINT,
                      headers={"Authorization": f"Bearer {config.ALIBABA_KEY}",
                               "Content-Type": "application/json"},
                      json={"model": model,
                            "input": {"messages": [{"role": "user", "content": prompt}]},
                            "parameters": {"temperature": 0.1, "result_format": "message"}},
                      timeout=120)
    if r.status_code != 200:
        raise Exception(f"{r.status_code}: {r.text[:200]}")
    return r.json()["output"]["choices"][0]["message"]["content"]


def call_minimax(model, prompt, endpoint):
    if not config.MINIMAX_KEY:
        raise RuntimeError("缺少 MINIMAX_KEY: 请在 MemoryHub/.env 中配置")
    r = requests.post(endpoint,
                      headers={"Authorization": f"Bearer {config.MINIMAX_KEY}",
                               "Content-Type": "application/json"},
                      json={"model": model,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.1},
                      timeout=120)
    if r.status_code != 200:
        raise Exception(f"{r.status_code}: {r.text[:200]}")
    j = r.json()
    return j["choices"][0]["message"]["content"]


def fidelity(items, source):
    if not items:
        return 0, 0, 0.0
    ok = 0
    for it in items:
        ev = (it.get("evidence") or "").strip()
        if ev and ev in source:
            ok += 1
    return ok, len(items), (ok / len(items) if items else 0.0)


def run(label, fn, source):
    prompt = MEMORY_SYS + "\n\n--- 对话 ---\n" + source
    try:
        content = fn(prompt)
        items = parse_json(content)
        ok, total, score = fidelity(items, source)
        print(f"\n### {label}")
        print(f"  返回条目: {total} | 逐字命中: {ok} | 保真分: {score:.0%}")
        for it in items[:3]:
            ev = (it.get('evidence') or '')[:40]
            hit = "✓" if (it.get('evidence') or '') in source else "✗编造"
            print(f"   [{it.get('type','?')}] {(it.get('claim') or '')[:36]} | 证据{hit}: {ev}")
        return (label, total, ok, score)
    except Exception as e:
        print(f"\n### {label}\n  失败: {e}")
        return (label, 0, 0, None)


def main():
    conv, source = pick_snippet()
    print(f"测试样本: conv={conv[:8]}… 长度={len(source)} 字")
    results = []
    # 候选:Qwen 3.7 旗舰 / MiniMax M3,各带一个上代基线对比
    for model in ["qwen3-max", "qwen3.7-max-preview", "qwen-plus"]:
        results.append(run(f"Alibaba/{model}", lambda p, m=model: call_alibaba(m, p), source))
    for model, ep in [("MiniMax-M3", "https://api.minimax.io/v1/text/chatcompletion_v2"),
                      ("MiniMax-M3", config.MINIMAX_ENDPOINT),
                      ("MiniMax-M2.5", config.MINIMAX_ENDPOINT)]:
        results.append(run(f"MiniMax/{model}@{ep.split('/v1')[1]}",
                           lambda p, m=model, e=ep: call_minimax(m, p, e), source))
    print("\n\n===== 保真排行(高→低)=====")
    ok = [r for r in results if r[3] is not None]
    for label, total, hit, score in sorted(ok, key=lambda x: (-x[3], -x[2])):
        print(f"  {score:.0%}  ({hit}/{total} 逐字)  {label}")


if __name__ == "__main__":
    main()
