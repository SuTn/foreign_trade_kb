# app/llm/interfaces.py
from abc import ABC, abstractmethod

class Embedding(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...
    @abstractmethod
    def dim(self) -> int: ...

class LLM(ABC):
    @abstractmethod
    def generate(self, system: str, user: str, max_tokens: int = 1024) -> str: ...
