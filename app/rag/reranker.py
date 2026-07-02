# app/rag/reranker.py
"""Reranker 占位符 (Task 15 将替换为真实实现)。

Task 14 的 pipeline.py 按 brief 导入 `from app.rag.reranker import rerank`，
但 Task 14 的测试只测 retrievers (不测 pipeline.run)，因此这里提供一个
最小可导入的函数占位，避免 import 失败。Task 15 会用真正的 BGE reranker
实现覆盖本文件。
"""
from typing import Any


def rerank(query: str, candidates: list[dict], top_k: int = 8) -> list[dict]:
    """占位 rerank: 原样返回前 top_k 条候选 (无实际重排)。

    Task 15 将替换为基于 BGE-reranker-v2-m3 的真实重排实现。
    """
    return candidates[:top_k]


class Reranker:
    """占位 Reranker 类 (Task 15 将替换)。

    提供 rerank 方法以匹配 pipeline.py 中 `self.reranker.rerank(...)` 调用。
    """

    def rerank(self, query: str, candidates: list[dict], top_k: int = 8) -> list[dict]:
        return candidates[:top_k]
