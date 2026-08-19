# tests/llm/test_bge_embedding.py
from app.llm.interfaces import Embedding, LLM
from app.llm.bge_embedding import OpenAIEmbedding, get_embedding
from app.llm.cloud_llm import CloudLLM

def test_embedding_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        Embedding()

def test_llm_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        LLM()

def test_openai_embedding_carries_api_base(monkeypatch):
    """OpenAIEmbedding 保留 api_base (可与 LLM 分开配置), dim 不触发网络。"""
    from app import config
    monkeypatch.setattr(config.settings, "embedding_provider", "openai")
    monkeypatch.setattr(config.settings, "embedding_api_base", "https://embed.example.com/v1")
    monkeypatch.setattr(config.settings, "embedding_model", "qwen3.7-text-embedding")
    monkeypatch.setattr(config.settings, "embedding_dim", 1024)
    e = OpenAIEmbedding()
    assert e.api_base == "https://embed.example.com/v1"
    assert e._model_name == "qwen3.7-text-embedding"
    assert e.dim() == 1024  # 不触发网络

def test_get_embedding_factory(monkeypatch):
    """get_embedding 始终返回在线 OpenAIEmbedding (本地模型已移除)。"""
    from app import config
    monkeypatch.setattr(config.settings, "embedding_provider", "openai")
    assert isinstance(get_embedding(), OpenAIEmbedding)

def test_cloud_llm_carries_api_base(monkeypatch):
    """CloudLLM 保留 api_base, openai provider 走兼容接口。"""
    from app import config
    monkeypatch.setattr(config.settings, "llm_provider", "openai")
    monkeypatch.setattr(config.settings, "llm_api_base", "https://llm.example.com/v1")
    monkeypatch.setattr(config.settings, "llm_api_key", "sk-test")
    llm = CloudLLM()
    assert llm.provider == "openai"
    assert llm.api_base == "https://llm.example.com/v1"
    assert llm._resolve_key() == "sk-test"  # 不回退环境变量