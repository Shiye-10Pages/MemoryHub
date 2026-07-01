"""LLM + 嵌入 provider 抽象。

支持两种 API 形态:
- **dashscope 原生**(默认;向后兼容:只填 ALIBABA_KEY 即可,行为与旧版一致)
- **openai 兼容**(OpenAI / DeepSeek / 智谱GLM / Kimi / SiliconFlow / DashScope 兼容模式 等,
  凡提供 `/chat/completions` 与 `/embeddings` 的服务都能用)

配置读 `.env`(每次现读,面板改完即时生效、无需重启)。字段见 `.env.example`。
"""
import os
import time

import requests

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# provider 预设:base_url, 默认对话模型, 默认嵌入模型, 嵌入维度, API 形态
PRESETS = {
    "dashscope":   ("https://dashscope.aliyuncs.com", "qwen3-max", "text-embedding-v4", 1024, "dashscope"),
    "openai":      ("https://api.openai.com/v1", "gpt-4.1-mini", "text-embedding-3-small", 1536, "openai"),
    "deepseek":    ("https://api.deepseek.com/v1", "deepseek-chat", "", 0, "openai"),          # 无嵌入,需另配 EMBED_*
    "zhipu":       ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash", "embedding-3", 2048, "openai"),
    "moonshot":    ("https://api.moonshot.cn/v1", "moonshot-v1-8k", "", 0, "openai"),          # 无嵌入
    "siliconflow": ("https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-72B-Instruct", "BAAI/bge-m3", 1024, "openai"),
    "custom":      ("", "", "", 1024, "openai"),
}


def _env(key, default=""):
    """现读 .env(优先),再退进程环境变量。"""
    p = os.path.join(HUB, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line.startswith(key + "="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    return os.environ.get(key, default)


def resolve():
    """返回生效配置。向后兼容:LLM_API_KEY 未填时用旧的 ALIBABA_KEY,并按 dashscope 处理。"""
    prov = (_env("LLM_PROVIDER") or "dashscope").lower()
    if prov not in PRESETS:
        prov = "custom"
    base_d, chat_d, emb_d, dim_d, fmt = PRESETS[prov]
    key = _env("LLM_API_KEY") or _env("ALIBABA_KEY")
    base = _env("LLM_BASE_URL") or base_d
    chat_model = _env("LLM_MODEL") or chat_d
    emb_key = _env("EMBED_API_KEY") or key
    emb_base = _env("EMBED_BASE_URL") or base
    emb_model = _env("EMBED_MODEL") or emb_d
    emb_dim = int(_env("EMBED_DIM") or dim_d or 1024)
    return {"provider": prov, "format": fmt, "key": key, "base": base, "chat_model": chat_model,
            "embed_key": emb_key, "embed_base": emb_base, "embed_model": emb_model,
            "embed_dim": emb_dim, "embed_format": fmt}


def chat(prompt, model=None, temperature=0.1, timeout=180):
    c = resolve()
    if not c["key"]:
        raise RuntimeError("缺少 LLM API Key:请在面板「设置」或 .env 配置(LLM_API_KEY 或旧的 ALIBABA_KEY)")
    m = model or c["chat_model"]
    if c["format"] == "dashscope":
        r = requests.post(
            c["base"].rstrip("/") + "/api/v1/services/aigc/text-generation/generation",
            headers={"Authorization": f"Bearer {c['key']}", "Content-Type": "application/json"},
            json={"model": m, "input": {"messages": [{"role": "user", "content": prompt}]},
                  "parameters": {"temperature": temperature, "result_format": "message"}}, timeout=timeout)
        if r.status_code != 200:
            raise Exception(f"{r.status_code}: {r.text[:200]}")
        return r.json()["output"]["choices"][0]["message"]["content"]
    r = requests.post(                                     # openai 兼容
        c["base"].rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {c['key']}", "Content-Type": "application/json"},
        json={"model": m, "messages": [{"role": "user", "content": prompt}], "temperature": temperature},
        timeout=timeout)
    if r.status_code != 200:
        raise Exception(f"{r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


def embed_dim():
    return resolve()["embed_dim"]


def embed(texts, text_type="document", batch=10):
    c = resolve()
    if not c["embed_key"]:
        raise RuntimeError("缺少嵌入 API Key:请在面板「设置」或 .env 配置")
    if not c["embed_model"]:
        raise RuntimeError(f"provider={c['provider']} 未内置嵌入模型:请在 .env 配 EMBED_PROVIDER/EMBED_MODEL(如用 openai 的 text-embedding-3-small)")
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        for attempt in range(3):
            try:
                if c["embed_format"] == "dashscope":
                    r = requests.post(
                        c["embed_base"].rstrip("/") + "/api/v1/services/embeddings/text-embedding/text-embedding",
                        headers={"Authorization": f"Bearer {c['embed_key']}", "Content-Type": "application/json"},
                        json={"model": c["embed_model"], "input": {"texts": chunk},
                              "parameters": {"dimension": c["embed_dim"], "text_type": text_type}}, timeout=60)
                    if r.status_code != 200:
                        raise Exception(f"{r.status_code}: {r.text[:200]}")
                    embs = sorted(r.json()["output"]["embeddings"], key=lambda e: e["text_index"])
                    out.extend(e["embedding"] for e in embs)
                else:                                      # openai 兼容
                    r = requests.post(
                        c["embed_base"].rstrip("/") + "/embeddings",
                        headers={"Authorization": f"Bearer {c['embed_key']}", "Content-Type": "application/json"},
                        json={"model": c["embed_model"], "input": chunk}, timeout=60)
                    if r.status_code != 200:
                        raise Exception(f"{r.status_code}: {r.text[:200]}")
                    data = sorted(r.json()["data"], key=lambda e: e["index"])
                    out.extend(e["embedding"] for e in data)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        time.sleep(0.2)
    return out


def test_connectivity():
    """轻量测活:嵌一条 + 说一句。返回 (ok, detail)。"""
    c = resolve()
    try:
        v = embed(["ping"])
        dim = len(v[0]) if v else 0
        chat("说'ok'两个字", timeout=30)
        return True, f"provider={c['provider']} · 对话={c['chat_model']} · 嵌入={c['embed_model']}({dim}维) ✓"
    except Exception as e:
        return False, f"{c['provider']} 连通失败:{e}"
