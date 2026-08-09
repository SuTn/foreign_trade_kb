# app/storage/chroma_store.py
import chromadb
from app.storage.interfaces import VectorStore
from app.config import settings

class ChromaStore(VectorStore):
    def __init__(self, embedding_fn, path=None):
        self.embedding_fn = embedding_fn  # callable(text)->list[float]
        self.client = chromadb.PersistentClient(path=str(path or settings.chroma_dir))
        self.msg_col = self.client.get_or_create_collection("message_vectors")
        self.chunk_col = self.client.get_or_create_collection("knowledge_chunks")

    def upsert_message_vector(self, key, text, metadata):
        self.msg_col.upsert(ids=[key], embeddings=[self.embedding_fn(text)], documents=[text], metadatas=[metadata])

    def upsert_chunks(self, chunks):
        self.chunk_col.upsert(
            ids=[c["id"] for c in chunks],
            embeddings=[self.embedding_fn(c["text"]) for c in chunks],
            documents=[c["text"] for c in chunks],
            metadatas=[c["metadata"] for c in chunks])

    def query_messages(self, text, chat_id=None, top_k=5):
        where = {"chat_id": chat_id} if chat_id else None
        r = self.msg_col.query(query_embeddings=[self.embedding_fn(text)], n_results=top_k, where=where)
        return self._fmt(r)

    def query_chunks(self, text, top_k=5):
        r = self.chunk_col.query(query_embeddings=[self.embedding_fn(text)], n_results=top_k)
        return self._fmt(r)

    def delete_chunks(self, doc_id):
        """删除某文档的全部向量 chunk。"""
        self.chunk_col.delete(where={"doc_id": doc_id})

    def _fmt(self, r):
        return [{"id": i, "text": d, "metadata": m, "distance": dist}
                for i, d, m, dist in zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0])]
