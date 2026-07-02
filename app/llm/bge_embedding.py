# app/llm/bge_embedding.py
from FlagEmbedding import BGEM3FlagModel
from app.llm.interfaces import Embedding
from app.config import settings

class BgeEmbedding(Embedding):
    def __init__(self, model=None):
        self._model = None
        self._model_name = model or settings.embedding_model

    def _ensure(self):
        if self._model is None:
            self._model = BGEM3FlagModel(self._model_name, use_fp16=True)

    def embed(self, text: str) -> list[float]:
        self._ensure()
        out = self._model.encode([text], batch_size=1, return_dense=True)["dense_vecs"][0]
        return out.tolist()

    def dim(self) -> int:
        return 1024
