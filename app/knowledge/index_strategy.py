# app/knowledge/index_strategy.py
from abc import ABC, abstractmethod

class IndexStrategy(ABC):
    """索引策略抽象: 可挂 RAG/Wiki 等, 可独立开关。"""
    @abstractmethod
    def index(self, doc_id: str, text: str) -> None: ...
