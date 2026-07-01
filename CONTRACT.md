# MemoryHub 保真契约(Day1 冻结)

> 这是整个记忆系统的"宪法"。改字段=改地基,需谨慎并升版本号。当前 **v1**。

## memory_item 强制字段

| 字段 | 含义 | 约束 |
|---|---|---|
| `id` | 原子唯一 id | 必填 |
| `type` | 类型 | 方法论 / 决策 / 经验 / SOP / 认知 / 反馈 / 事实 / 偏好 / 关系 |
| `claim` | 一句话结论(自包含,脱离上下文也能懂) | 必填 |
| `evidence` | **逐字原文证据** | **缺失 → 拒收**(防幻觉硬闸门) |
| `sources` | 来源指针数组 `[{source,conv_id,uri,ts}]` | 必填,至少一条 |
| `confidence` | 复合置信度 0..1 | 见下 |
| `valid_from` | 何时开始为真 | 必填 |
| `valid_until` | 何时失效;null=现行 | 被取代时填 |
| `status` | 待验证 / 已应用 / 已归档 / 已失效 | 默认 待验证 |
| `review_date` | 再审日期(默认 +14 天) | |
| `links` | 关联原子 id 数组 | |

## 复合置信度公式

```
confidence = source_reliability × extraction_method × evidence_count
           × cross_source_agreement × freshness_decay × human_review_bonus
```
- **source_reliability**:第一方结构化源 > 自建网关日志 > 企业审计 API > 本地文档 > 网页抓取/RPA
- **extraction_method**:规则/结构化字段 > 模式匹配 > 工具解析 > LLM 推断
- 多源印证 ↑;只来自一次低置信抓取 ↓。

## 四道写入闸(gate.py 执行)

1. **溯源闸** — 无逐字证据 + 来源指针 → 拒收。
2. **矛盾闸** — 向量检索近义旧条;冲突则旧条 `valid_until=now / status=已失效`,新旧互链(不删,历史可追溯)。
3. **去重合并闸** — 精确哈希(content_hash)+ 近重复(SimHash/embedding)两层;同结论多源 → 合并、累计 sources、升 confidence。
4. **人工闸** — 命中影响过滤器(定价/方向/收入模型)或 confidence < 阈值 → 入 `human_queue` 等确认,其余直接入库。

## 四重提纯过滤器(distill.py,沿用 dialogue-sediment)

洞察(改变理解?) / 行动(指导决策?) / 复用(跨场景适用?) / **影响**(动资源/方向/收入?)。
前三任一为是 → 沉淀;**影响为是 → 即使其余皆否也必须沉淀**(且走人工闸)。

## 状态生命周期

`待验证` →(被验证有用)→ `已应用`;过 review_date 未验证 → 询问归档/修订/删除;被新结论取代 → `已失效`(保留+互链)。
