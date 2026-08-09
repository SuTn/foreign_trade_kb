# app/knowledge/rag_index.py
import uuid
from app.knowledge.index_strategy import IndexStrategy
from app.knowledge.chunker import chunk_text
from app.storage.interfaces import StructuredStore, VectorStore

class RagIndex(IndexStrategy):
    def __init__(self, store: StructuredStore, vector_store: VectorStore):
        self.store = store
        self.vector_store = vector_store

    def index(self, doc_id: str, text: str) -> None:
        chunks = chunk_text(text)
        chunk_records = []
        for c in chunks:
            cid = str(uuid.uuid4())
            self.store.conn.execute(
                "INSERT OR REPLACE INTO doc_chunks VALUES(?,?,?,?,?,?)",
                (cid, doc_id, c["chunk_idx"], c["text"], str(c["parent_chunk_id"]), cid))
            # FTS5
            self.store.conn.execute(
                "INSERT OR REPLACE INTO doc_chunks_fts(rowid, text) VALUES((SELECT rowid FROM doc_chunks WHERE id=?), ?)",
                (cid, c["text"]))
            chunk_records.append({"id": cid, "text": c["text"],
                                  "metadata": {"doc_id": doc_id, "chunk_idx": c["chunk_idx"],
                                               "parent_chunk_id": c["parent_chunk_id"]}})
        self.store.conn.commit()
        self.vector_store.upsert_chunks(chunk_records)
