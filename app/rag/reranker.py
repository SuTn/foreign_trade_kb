# app/rag/reranker.py
"""Reranker: BGE-reranker-v2-m3 重排 + 测试用 FakeReranker。

模块级 `rerank` 函数保留以兼容 pipeline.py 的 `from app.rag.reranker import rerank`
导入 (该导入实际未使用，pipeline.run 走 self.reranker.rerank 实例方法)；
此处委托给 FakeReranker 以保持可导入且无副作用。
"""
from abc import ABC, abstractmethod
import logging
from typing import Any

from app.config import settings


class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, candidates: list[dict], top_k: int = 8) -> list[dict]: ...


class BgeReranker(Reranker):
    """FlagEmbedding 本地重排。模型按 model 名全局复用, 避免每次实例化重载。"""

    _model_cache: dict[str, Any] = {}

    def __init__(self, model=None):
        self._model = None
        self._name = model or settings.reranker_model

    def _ensure(self):
        if self._model is None:
            key = self._name
            if key not in BgeReranker._model_cache:
                from FlagEmbedding import FlagReranker
                BgeReranker._model_cache[key] = FlagReranker(key, use_fp16=True)
            self._model = BgeReranker._model_cache[key]

    def rerank(self, query, candidates, top_k=8):
        if not candidates:
            return []
        self._ensure()
        scores = self._score([[query, c.get("text", "")] for c in candidates])
        ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [{**c, "score": s} for c, s in ranked[:top_k]]

    def _score(self, pairs):
        """用 tokenizer + model 手动打分, 绕过 FlagEmbedding compute_score
        (其内部调 prepare_for_model, transformers>=5 已移除该方法)。"""
        import torch
        m = self._model
        enc = m.tokenizer(
            pairs, padding=True, truncation=True, max_length=m.max_length,
            return_tensors="pt", return_token_type_ids=True)
        enc = {k: v.to(m.target_devices[0]) for k, v in enc.items()}
        with torch.no_grad():
            logits = m.model(**enc).logits
        return logits[:, 0].float().cpu().tolist()


class OllamaReranker(Reranker):
    """OpenAI 兼容接口重排 (可指向 Ollama /v1 或第三方网关)。"""

    def __init__(self, model=None, api_base=None):
        self._name = model or settings.reranker_model
        self.api_base = (api_base or settings.reranker_api_base or "http://localhost:11434/v1").rstrip("/")

    def rerank(self, query, candidates, top_k=8):
        if not candidates:
            return []
        import httpx
        try:
            resp = httpx.post(
                f"{self.api_base}/rerank",
                json={"model": self._name, "query": query,
                      "documents": [c.get("text", "") for c in candidates]},
                timeout=60,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            ranked = sorted(results, key=lambda r: r.get("relevance_score", 0.0), reverse=True)
            out = []
            for r in ranked[:top_k]:
                idx = r.get("index", 0)
                if 0 <= idx < len(candidates):
                    out.append({**candidates[idx], "score": r.get("relevance_score")})
            return out
        except Exception as e:
            logging.getLogger(__name__).warning(
                f"reranker unavailable, fallback to original order: {e}")
            return candidates[:top_k]  # 原序回退


class FakeReranker(Reranker):
    """测试用: 按文本长度排序, 不加载模型。"""

    def rerank(self, query, candidates, top_k=8):
        ranked = sorted(candidates, key=lambda c: len(c.get("text", "")), reverse=True)
        return ranked[:top_k]


def get_reranker() -> Reranker:
    """按 settings.reranker_provider 返回重排实现 (local | ollama)。"""
    if settings.reranker_provider == "ollama":
        return OllamaReranker()
    return BgeReranker()


def rerank(query: str, candidates: list[dict], top_k: int = 8) -> list[dict]:
    """模块级便捷函数: 委托给 FakeReranker。

    保留以兼容 pipeline.py 的 `from app.rag.reranker import rerank` 导入
    (该导入在 pipeline.run 中未实际使用，run 走 self.reranker.rerank)。
    """
    return FakeReranker().rerank(query, candidates, top_k=top_k)
