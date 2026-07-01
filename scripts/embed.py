"""嵌入助手:委托 provider(DashScope 原生 / OpenAI 兼容)。

保留 embed_texts / DIM / MODEL 名,兼容 gate.py、server.py、recall.py 的既有 import。
DIM / MODEL 在进程启动时按 .env 里的 provider 解析一次。
"""
import provider

_c = provider.resolve()
MODEL = _c["embed_model"]
DIM = _c["embed_dim"]


def embed_texts(texts, text_type="document", batch=10):
    return provider.embed(texts, text_type=text_type, batch=batch)
