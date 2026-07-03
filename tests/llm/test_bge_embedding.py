# tests/llm/test_bge_embedding.py
from app.llm.interfaces import Embedding, LLM
from app.llm.bge_embedding import BgeEmbedding, OpenAIEmbedding, get_embedding
from app.llm.cloud_llm import CloudLLM

def test_embedding_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        Embedding()

def test_llm_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        LLM()

def test_bge_dim_without_loading():
    # dim 不应触发模型加载
    e = BgeEmbedding()
    assert e.dim() == 1024
    assert e._model is None  # 未加载

def test_openai_embedding_carries_api_base(monkeypatch):
    """OpenAIEmbedding 保留 api_base (可与 LLM 分开配置), dim 不触发网络。"""
    from app import config
    monkeypatch.setattr(config.settings, "embedding_provider", "openai")
    monkeypatch.setattr(config.settings, "embedding_api_base", "https://embed.example.com/v1")
    monkeypatch.setattr(config.settings, "embedding_model", "text-embedding-3-small")
    monkeypatch.setattr(config.settings, "embedding_dim", 1536)
    e = OpenAIEmbedding()
    assert e.api_base == "https://embed.example.com/v1"
    assert e._model_name == "text-embedding-3-small"
    assert e.dim() == 1536  # 不触发网络

def test_get_embedding_factory(monkeypatch):
    from app import config
    monkeypatch.setattr(config.settings, "embedding_provider", "openai")
    assert isinstance(get_embedding(), OpenAIEmbedding)
    monkeypatch.setattr(config.settings, "embedding_provider", "local")
    assert isinstance(get_embedding(), BgeEmbedding)

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
