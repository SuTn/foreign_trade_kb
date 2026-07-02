# app/rag/reranker.py
"""Reranker: BGE-reranker-v2-m3 重排 + 测试用 FakeReranker。

模块级 `rerank` 函数保留以兼容 pipeline.py 的 `from app.rag.reranker import rerank`
导入 (该导入实际未使用，pipeline.run 走 self.reranker.rerank 实例方法)；
此处委托给 FakeReranker 以保持可导入且无副作用。
"""
from abc import ABC, abstractmethod

from app.config import settings


class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, candidates: list[dict], top_k: int = 8) -> list[dict]: ...


class BgeReranker(Reranker):
    def __init__(self, model=None):
        self._model = None
        self._name = model or settings.reranker_model

    def _ensure(self):
        if self._model is None:
            from FlagEmbedding import FlagReranker
            self._model = FlagReranker(self._name, use_fp16=True)

    def rerank(self, query, candidates, top_k=8):
        if not candidates:
            return []
        self._ensure()
        pairs = [[query, c.get("text", "")] for c in candidates]
        scores = self._model.compute_score(pairs, normalize=True)
        if isinstance(scores, float):
            scores = [scores]
        ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [{**c, "score": s} for c, s in ranked[:top_k]]


class FakeReranker(Reranker):
    """测试用: 按文本长度排序, 不加载模型。"""

    def rerank(self, query, candidates, top_k=8):
        ranked = sorted(candidates, key=lambda c: len(c.get("text", "")), reverse=True)
        return ranked[:top_k]


def rerank(query: str, candidates: list[dict], top_k: int = 8) -> list[dict]:
    """模块级便捷函数: 委托给 FakeReranker。

    保留以兼容 pipeline.py 的 `from app.rag.reranker import rerank` 导入
    (该导入在 pipeline.run 中未实际使用，run 走 self.reranker.rerank)。
    """
    return FakeReranker().rerank(query, candidates, top_k=top_k)
