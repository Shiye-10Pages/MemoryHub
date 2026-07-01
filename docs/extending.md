# 扩展:接入你自己的记忆源

MemoryHub 的连接器都很薄:把任意来源的文本归一成 `raw_event`(或直接产出候选),
再走同一条 `distill → gate → project` 管线。写一个新连接器 ≈ 复制一个现成的照葫芦画瓢。

## 现成参考
- `scripts/ingest_claude_web.py` / `scripts/ingest_chatgpt.py` — 从官方数据导出(`conversations.json`)解析对话。
- `scripts/ingest_claude_memories.py` — 从 Claude 导出的 `memories.json` 拆记忆(走人工闸,人确认才入库)。
- `examples/ingest_custom_jsonl.py` — **参考模板**连接器:演示如何把「你自己的、一行一条 JSONL
  的日志 / 反馈 / 笔记」规则直采成候选(逐字 evidence、按来源赋置信度)。改三处 TODO 即可接你自己的源;
  不在夜间管线里运行,仅作示例。

## 写一个新连接器的套路
1. 读你的源,抽出 `{source, project, conv_id, ts, role, text}` 写入 `raw_event`(表结构见 `scripts/schema.sql`)。
2. 把原文归档到 `raw/<你的源>/`(append-only,便于溯源与重抽)。
3. 让 `distill.py` 覆盖到它:把项目名 / 路径片段加进 `sources.json`(参考 `sources.json.example`);
   或按 `examples/` 里的做法直接产候选写 `staging/candidates.jsonl`。
4. `gate.py` 会跑四道保真闸(溯源 / 矛盾 / 去重 / 人工);高影响或低置信的条目进人工队列等你确认。

## 保真铁律(所有连接器共用)
- **evidence 必须是来源里的逐字子串**——没有逐字证据的结论一律不入库(防幻觉硬闸)。
- AI 推断来的记忆一律 `force_review=True`,只提名、不自动入库;人确认才进记忆库。
