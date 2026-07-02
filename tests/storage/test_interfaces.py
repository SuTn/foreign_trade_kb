# tests/storage/test_interfaces.py
import pytest
from app.storage.interfaces import StructuredStore, VectorStore

def test_structured_store_is_abstract():
    with pytest.raises(TypeError):
        StructuredStore()

def test_vector_store_is_abstract():
    with pytest.raises(TypeError):
        VectorStore()
