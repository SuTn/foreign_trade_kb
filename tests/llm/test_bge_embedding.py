# tests/llm/test_bge_embedding.py
from app.llm.interfaces import Embedding, LLM
from app.llm.bge_embedding import BgeEmbedding
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
