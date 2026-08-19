# app/llm/bge_embedding.py
"""嵌入实现: OpenAI 兼容接口 (阿里云 qwen3.7-text-embedding 等)。

本地模型 (BgeEmbedding) 已移除, 全切在线。
"""
from app.llm.interfaces import Embedding
from app.config import settings


class OpenAIEmbedding(Embedding):
    """OpenAI 兼容接口嵌入 (可配 api_base 指向第三方/自建网关, 与 LLM 的 api_base 分开)。"""

    def __init__(self, model=None, api_base=None, api_key=None):
        self._model_name = model or settings.embedding_model
        self.api_base = api_base or settings.embedding_api_base
        self.api_key = api_key or settings.embedding_api_key

    def _client(self):
        import os, openai
        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        return openai.OpenAI(api_key=key, base_url=self.api_base)

    def embed(self, text: str) -> list[float]:
        resp = self._client().embeddings.create(
            model=self._model_name, input=text, dimensions=self.dim())
        return resp.data[0].embedding

    def dim(self) -> int:
        return settings.embedding_dim


def get_embedding() -> Embedding:
    """返回在线嵌入实现 (OpenAI 兼容接口)。本地模型已移除。"""
    return OpenAIEmbedding()