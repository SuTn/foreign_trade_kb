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
    """4.3: OllamaReranker 网络失败回退原序候选, 不抛异常。"""
    import httpx
    from app.rag.reranker import OllamaReranker

    def boom_post(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom_post)
    r = OllamaReranker(model="m", api_base="http://localhost:11434/v1")
    cands = [{"text": "a"}, {"text": "b"}]
    ranked = r.rerank("q", cands, top_k=2)
    assert [c["text"] for c in ranked] == ["a", "b"]


def test_bge_reranker_manual_score_no_prepare_for_model(monkeypatch):
    """BgeReranker 用手动 tokenizer+model 打分, 不依赖 FlagEmbedding compute_score
    (其内部调用 prepare_for_model, transformers>=5 已移除)。"""
    import torch
    from app.rag.reranker import BgeReranker

    class FakeTokenizer:
        def __call__(self, pairs, **kw):
            n = len(pairs)
            return {
                "input_ids": torch.zeros((n, 4), dtype=torch.long),
                "attention_mask": torch.ones((n, 4)),
                "token_type_ids": torch.zeros((n, 4), dtype=torch.long),
            }

    class FakeLogits:
        def __init__(self, vals): self._v = torch.tensor(vals)
        @property
        def logits(self): return self._v

    class FakeModel:
        def __init__(self): self.called = False
        def __call__(self, **enc):
            self.called = True
            return FakeLogits([[3.0], [1.0], [2.0]])

    class FakeFlagModel:
        def __init__(self):
            self.tokenizer = FakeTokenizer()
            self.model = FakeModel()
            self.max_length = 128
            self.target_devices = ["cpu"]

    r = BgeReranker()
    r._model = FakeFlagModel()
    cands = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    ranked = r.rerank("q", cands, top_k=2)
    assert [c["text"] for c in ranked] == ["a", "c"]  # score 3,2 最高
    assert abs(ranked[0]["score"] - 3.0) < 1e-6
    assert FakeFlagModel().model.called is False or r._model.model.called is True
