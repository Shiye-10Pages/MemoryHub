"""provider:无 key 的报错语义(可操作)、resolve 默认值。"""
import pytest

import provider


def test_resolve_defaults_without_env(env):
    c = provider.resolve()
    assert c["provider"] == "dashscope"
    assert c["key"] == ""
    assert c["embed_dim"] == 1024


def test_chat_without_key_actionable_error(env):
    with pytest.raises(RuntimeError) as e:
        provider.chat("hi")
    assert "设置" in str(e.value) or ".env" in str(e.value)


def test_embed_without_key_actionable_error(env):
    with pytest.raises(RuntimeError) as e:
        provider.embed(["hi"])
    assert "设置" in str(e.value) or ".env" in str(e.value)
