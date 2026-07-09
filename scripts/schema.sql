-- MemoryHub · Stage 1 SQLite schema(控制面 + 全文索引)
-- 保真契约的物理落地。向量索引(sqlite-vec)待嵌入模型/维度确定后单独建(见文件末)。
-- 全部 IF NOT EXISTS,可重复执行(幂等)。

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ① 原始事件层:append-only、不可变。每条 = transcript 里的一条消息/事件。溯源锚点。
CREATE TABLE IF NOT EXISTS raw_event (
    id          TEXT PRIMARY KEY,   -- sha256(source|conv_id|seq|role|text) 截断,幂等去重
    source      TEXT NOT NULL,      -- 'claude-code'
    project     TEXT,               -- 解码后的 cwd(项目)
    conv_id     TEXT,               -- 会话/transcript id
    seq         INTEGER,            -- 会话内序号
    ts          TEXT,               -- ISO8601
    role        TEXT,               -- user|assistant|tool|system
    text        TEXT,               -- 原文(逐字,证据来源)
    meta        TEXT,               -- json:其它字段
    raw_path    TEXT,               -- 指向 raw/claude-code/*.jsonl
    ingested_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_raw_conv   ON raw_event(conv_id, seq);
CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_event(source, ts);

-- ② 规范化文档层:把一段对话归一为一个 episode(抽取/检索的输入单元)。
CREATE TABLE IF NOT EXISTS canonical_document (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    project       TEXT,
    conv_id       TEXT,
    title         TEXT,
    text          TEXT,             -- 归一后的全文(可含多轮)
    lang          TEXT,
    uri           TEXT,             -- 指针:conv_id / 文件路径
    ts_start      TEXT,
    ts_end        TEXT,
    raw_event_ids TEXT,             -- json array
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_doc_conv ON canonical_document(conv_id);

-- ③ 派生记忆层:提纯后的记忆原子(现行视图)。
CREATE TABLE IF NOT EXISTS memory_item (
    id                  TEXT PRIMARY KEY,
    type                TEXT NOT NULL,        -- 方法论|决策|经验|SOP|认知|反馈|事实|偏好|关系
    claim               TEXT NOT NULL,        -- 一句话结论(自包含)
    context             TEXT,                 -- 情境锚(在什么局面/关于什么主题下成立)
    evidence            TEXT NOT NULL,        -- 逐字原文证据(缺则不得入库)
    sources             TEXT NOT NULL,        -- json array: [{source,conv_id,uri,ts}]
    confidence          REAL,                 -- 复合置信度 0..1
    valid_from          TEXT,
    valid_until         TEXT,                 -- null=现行;被取代时填(双时态)
    status              TEXT DEFAULT '待核', -- 待核|已确认|休眠|已被取代
    review_date         TEXT,
    links               TEXT,                 -- json array of memory_item ids
    content_hash        TEXT,                 -- 精确去重
    current_revision_id TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mem_type  ON memory_item(type, status);
CREATE INDEX IF NOT EXISTS idx_mem_valid ON memory_item(valid_until);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_hash ON memory_item(content_hash);

-- 版本层:append-only 历史。矛盾闸/修订/合并都新增一条 revision,不覆盖。
CREATE TABLE IF NOT EXISTS memory_item_revision (
    id                 TEXT PRIMARY KEY,
    memory_item_id     TEXT NOT NULL,
    revision_num       INTEGER NOT NULL,
    claim              TEXT,
    evidence           TEXT,
    sources            TEXT,
    confidence         REAL,
    valid_from         TEXT,
    valid_until        TEXT,
    status             TEXT,
    change_reason      TEXT,                  -- create|supersede|merge|human_edit|decay
    parent_revision_id TEXT,
    created_at         TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rev_item ON memory_item_revision(memory_item_id, revision_num);

-- ④ 治理层:人工闸队列(高影响/低置信/矛盾 → 等人确认,不直接入库)。
CREATE TABLE IF NOT EXISTS human_queue (
    id          TEXT PRIMARY KEY,
    candidate   TEXT NOT NULL,        -- json:待入库的 memory_item 候选
    reason      TEXT,                 -- high_impact|low_confidence|contradiction
    status      TEXT DEFAULT 'pending', -- pending|approved|rejected
    created_at  TEXT DEFAULT (datetime('now')),
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON human_queue(status);

-- 摄取游标:每个 transcript 处理到哪行,支持幂等增量。
CREATE TABLE IF NOT EXISTS ingest_cursor (
    file_path   TEXT PRIMARY KEY,
    file_mtime  TEXT,
    last_offset INTEGER DEFAULT 0,    -- 已处理到的行号
    last_run    TEXT
);

-- 全文索引:FTS5 over memory_item(claim+evidence)。trigram 分词器=中文/混合子串可检索。
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    claim, evidence,
    content='memory_item', content_rowid='rowid',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memory_item BEGIN
  INSERT INTO memory_fts(rowid, claim, evidence) VALUES (new.rowid, new.claim, new.evidence);
END;
CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memory_item BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, claim, evidence) VALUES('delete', old.rowid, old.claim, old.evidence);
END;
CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE ON memory_item BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, claim, evidence) VALUES('delete', old.rowid, old.claim, old.evidence);
  INSERT INTO memory_fts(rowid, claim, evidence) VALUES (new.rowid, new.claim, new.evidence);
END;

-- ⑤ 向量存储:text-embedding-v4(1024 维)以 BLOB 存(struct.pack 小端 float)。
--   Stage 1 用 numpy/纯 python 算余弦;规模化后再迁 sqlite-vec/pgvector(同维度无缝)。
CREATE TABLE IF NOT EXISTS memory_embedding (
    memory_item_id TEXT PRIMARY KEY,
    model          TEXT,
    dim            INTEGER,
    vec            BLOB
);
