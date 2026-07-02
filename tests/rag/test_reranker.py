# tests/rag/test_reranker.py
from app.rag.reranker import FakeReranker


def test_fake_reranker_orders_by_length():
    r = FakeReranker()
    cands = [{"text": "a"}, {"text": "aaa"}, {"text": "aa"}]
    ranked = r.rerank("q", cands, top_k=2)
    assert len(ranked) == 2
    assert ranked[0]["text"] == "aaa"
