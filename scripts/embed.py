"""DashScope text-embedding-v4 嵌入助手(1024 维),批量 + 重试。"""
import os
import time

import requests

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
MODEL = "text-embedding-v4"
DIM = 1024
_KEY = None


def _alibaba_key():
    """优先 MemoryHub/.env,再读环境变量。"""
    global _KEY
    if _KEY:
        return _KEY
    envp = os.path.join(HUB, ".env")
    if os.path.exists(envp):
        for line in open(envp, encoding="utf-8"):
            line = line.strip()
            if line.startswith("ALIBABA_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    _KEY = v
                    return _KEY
    from config import config
    _KEY = config.ALIBABA_KEY
    if not _KEY:
        raise RuntimeError("缺少 ALIBABA_KEY: 请在 MemoryHub/.env 中配置")
    return _KEY


def embed_texts(texts, text_type="document", batch=10):
    """texts: List[str] → List[List[float]](顺序对齐)。"""
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        for attempt in range(3):
            try:
                r = requests.post(
                    ENDPOINT,
                    headers={"Authorization": f"Bearer {_alibaba_key()}",
                             "Content-Type": "application/json"},
                    json={"model": MODEL, "input": {"texts": chunk},
                          "parameters": {"dimension": DIM, "text_type": text_type}},
                    timeout=60)
                if r.status_code != 200:
                    raise Exception(f"{r.status_code}: {r.text[:200]}")
                embs = sorted(r.json()["output"]["embeddings"],
                              key=lambda e: e["text_index"])
                out.extend(e["embedding"] for e in embs)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        time.sleep(0.2)
    return out
