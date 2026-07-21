"""嵌入助手:委托 provider(DashScope 原生 / OpenAI 兼容)。

保留 embed_texts / DIM / MODEL 名,兼容 gate.py、server.py、recall.py 的既有 import。
DIM / MODEL 在进程启动时按 .env 里的 provider 解析一次。
"""
import struct

import provider

_c = provider.resolve()
MODEL = _c["embed_model"]
DIM = _c["embed_dim"]


def current_dim():
    """当前 provider 的嵌入维度(现读,不用进程启动时的缓存)。"""
    return provider.resolve()["embed_dim"]


def current_model():
    """当前 provider 的嵌入模型名(现读)。"""
    return provider.resolve()["embed_model"]


def pack_embedding(vec):
    """把向量打包成写库三元组 (model, dim, blob)。dim/blob 以【实际向量长度】为准,
    model 现读——避免长驻进程用启动时缓存的 DIM/MODEL 打包,导致切 provider 后
    struct 崩溃或存进错误维度(审查 P1-4)。"""
    d = len(vec)
    return current_model(), d, struct.pack(f"<{d}f", *vec)


def embed_texts(texts, text_type="document", batch=10):
    return provider.embed(texts, text_type=text_type, batch=batch)
