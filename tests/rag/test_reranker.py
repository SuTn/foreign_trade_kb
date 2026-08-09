# tests/rag/test_reranker.py
from app.rag.reranker import FakeReranker


def test_fake_reranker_orders_by_length():
    r = FakeReranker()
    cands = [{"text": "a"}, {"text": "aaa"}, {"text": "aa"}]
    ranked = r.rerank("q", cands, top_k=2)
    assert len(ranked) == 2
    assert ranked[0]["text"] == "aaa"


def test_ollama_reranker_parses_results(monkeypatch):
    import httpx
    from app.rag.reranker import OllamaReranker

    calls = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.5}]}

    def fake_post(url, json, timeout):
        calls["url"] = url
        calls["json"] = json
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    r = OllamaReranker(model="m", api_base="http://localhost:11434/v1")
    cands = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    ranked = r.rerank("q", cands, top_k=2)
    assert calls["url"] == "http://localhost:11434/v1/rerank"
    assert calls["json"]["model"] == "m"
    assert calls["json"]["documents"] == ["a", "b", "c"]
    assert ranked[0]["text"] == "c"
    assert ranked[0]["score"] == 0.9
