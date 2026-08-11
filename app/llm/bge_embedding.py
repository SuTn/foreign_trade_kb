# app/llm/bge_embedding.py
from typing import Any
from app.llm.interfaces import Embedding
from app.config import settings
from app.llm.device_utils import use_fp16


class BgeEmbedding(Embedding):
    """本地 bge-m3 嵌入 (默认)。模型按 model 名全局复用, 避免每次实例化重载。"""

    _model_cache: dict[str, Any] = {}

    def __init__(self, model=None):
        self._model = None
        self._model_name = model or settings.embedding_model

    def _ensure(self):
        if self._model is None:
            key = self._model_name
            if key not in BgeEmbedding._model_cache:
                from FlagEmbedding import BGEM3FlagModel
                BgeEmbedding._model_cache[key] = BGEM3FlagModel(key, use_fp16=use_fp16())
            self._model = BgeEmbedding._model_cache[key]

    def embed(self, text: str) -> list[float]:
        self._ensure()
        out = self._model.encode([text], batch_size=1, return_dense=True)["dense_vecs"][0]
        return out.tolist()

    def dim(self) -> int:
        return settings.embedding_dim


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
        resp = self._client().embeddings.create(model=self._model_name, input=text)
        return resp.data[0].embedding

    def dim(self) -> int:
        return settings.embedding_dim


def get_embedding() -> Embedding:
    """按 settings.embedding_provider 返回嵌入实现 (local | openai)。"""
    if settings.embedding_provider == "openai":
        return OpenAIEmbedding()
    return BgeEmbedding()
