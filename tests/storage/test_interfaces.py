# tests/storage/test_interfaces.py
import pytest
from app.storage.interfaces import StructuredStore, VectorStore

def test_structured_store_is_abstract():
    with pytest.raises(TypeError):
        StructuredStore()

def test_vector_store_is_abstract():
    with pytest.raises(TypeError):
        VectorStore()


def test_chroma_store_implements_vector_store(tmp_data):
    from app.storage.chroma_store import ChromaStore
    s = ChromaStore(embedding_fn=lambda t: [1.0] * 8)
    assert isinstance(s, VectorStore)
