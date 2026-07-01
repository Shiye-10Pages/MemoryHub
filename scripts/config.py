"""Local configuration loader for MemoryHub scripts."""
import os


HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


_load_env(os.path.join(HUB, ".env"))


class config:
    ALIBABA_KEY = os.environ.get("ALIBABA_KEY", "")
    ALIBABA_ENDPOINT = os.environ.get(
        "ALIBABA_ENDPOINT",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
    )
    MINIMAX_KEY = os.environ.get("MINIMAX_KEY", "")
    MINIMAX_ENDPOINT = os.environ.get(
        "MINIMAX_ENDPOINT",
        "https://api.minimax.io/v1/text/chatcompletion_v2",
    )
    # 提纯器角色名(可选,用于给记忆/预审 prompt 署名);留空则用中性角色。
    PERSONA_NAME = os.environ.get("PERSONA_NAME", "")
