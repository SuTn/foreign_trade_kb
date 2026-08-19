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


def test_ollama_reranker_network_failure_returns_original(monkeypatch):
    """OllamaReranker 网络失败回退原序候选, 不抛异常。"""
    import httpx
    from app.rag.reranker import OllamaReranker

    def boom_post(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom_post)
    r = OllamaReranker(model="m", api_base="http://localhost:11434/v1")
    cands = [{"text": "a"}, {"text": "b"}]
    ranked = r.rerank("q", cands, top_k=2)
    assert [c["text"] for c in ranked] == ["a", "b"]


def test_cloud_reranker_aliyun_parses_results(monkeypatch):
    """CloudReranker (aliyun) 解析 qwen3-rerank 响应, 按 index 映射回候选。"""
    import httpx
    from app.rag.rerank_cloud import CloudReranker

    calls = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "object": "list",
                "results": [
                    {"index": 0, "relevance_score": 0.9334521178273196},
                    {"index": 2, "relevance_score": 0.34100082626411193},
                ],
                "model": "qwen3-rerank",
                "id": "test-id",
                "usage": {"total_tokens": 79},
            }

    def fake_post(url, headers, json, timeout):
        calls["url"] = url
        calls["headers"] = headers
        calls["json"] = json
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    r = CloudReranker(provider="aliyun", model="qwen3-rerank",
                      api_base="https://example.maas.aliyuncs.com", api_key="sk-test")
    cands = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    ranked = r.rerank("q", cands, top_k=2)
    assert calls["url"] == "https://example.maas.aliyuncs.com/compatible-api/v1/reranks"
    assert calls["headers"]["Authorization"] == "Bearer sk-test"
    assert calls["json"]["model"] == "qwen3-rerank"
    assert calls["json"]["documents"] == ["a", "b", "c"]
    assert calls["json"]["query"] == "q"
    assert calls["json"]["top_n"] == 2
    assert ranked[0]["text"] == "a"
    assert ranked[0]["score"] == 0.9334521178273196
    assert ranked[1]["text"] == "c"


def test_cloud_reranker_aliyun_network_failure_returns_original(monkeypatch):
    """CloudReranker 网络失败回退原序候选, 不抛异常。"""
    import httpx
    from app.rag.rerank_cloud import CloudReranker

    def boom_post(url, headers, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom_post)
    r = CloudReranker(provider="aliyun", model="qwen3-rerank",
                      api_base="https://xxx.maas.aliyuncs.com", api_key="sk-test")
    cands = [{"text": "a"}, {"text": "b"}]
    ranked = r.rerank("q", cands, top_k=2)
    assert [c["text"] for c in ranked] == ["a", "b"]


def test_cloud_reranker_empty_candidates_returns_empty():
    """空候选直接返回空列表, 不发请求。"""
    from app.rag.rerank_cloud import CloudReranker
    r = CloudReranker(provider="aliyun", model="x",
                      api_base="https://xxx.maas.aliyuncs.com", api_key="sk-test")
    assert r.rerank("q", [], top_k=2) == []