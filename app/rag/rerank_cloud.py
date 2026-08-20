# app/rag/rerank_cloud.py
"""云重排适配器: 统一阿里/百度/字节的 rerank 接口到项目内部 Reranker 接口。

当前实现阿里云 (qwen3-rerank), 预留百度/字节扩展。
失败时回退原序候选, 不阻塞回复生成。
"""
import httpx
import logging
from app.rag.reranker import Reranker
from app.config import settings

log = logging.getLogger(__name__)


class CloudReranker(Reranker):
    """按 provider 分发到对应云厂商的 rerank API。"""

    def __init__(self, provider="aliyun", model=None, api_base=None, api_key=None):
        self.provider = provider
        self._name = model or settings.reranker_model
        base = api_base or settings.reranker_api_base
        self.api_base = base.rstrip("/") if base else None
        self.api_key = api_key or settings.reranker_api_key

    def rerank(self, query, candidates, top_k=8):
        if not candidates:
            return []
        try:
            if self.provider == "aliyun":
                return self._rerank_aliyun(query, candidates, top_k)
            # 预留: baidu / volcengine
        except Exception as e:
            log.warning("reranker unavailable, fallback to original order: %s", e)
        return candidates[:top_k]  # 失败回退原序

    def _rerank_aliyun(self, query, candidates, top_k):
        """阿里云 qwen3-rerank: POST /compatible-api/v1/reranks

        请求体: {model, documents, query, top_n} (instruct 可选, 不传默认问答检索)
        响应:   {results: [{index, relevance_score}], ...} 已按分数降序
        """
        resp = httpx.post(
            f"{self.api_base}/compatible-api/v1/reranks",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": self._name,
                "documents": [c.get("text", "") for c in candidates],
                "query": query,
                "top_n": top_k,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        out = []
        for r in results[:top_k]:
            idx = r.get("index", 0)
            if 0 <= idx < len(candidates):
                out.append({**candidates[idx], "score": r.get("relevance_score")})
        return out or candidates[:top_k]