# tests/rag/test_retrievers.py
from app.rag.retrievers import retrieve_multi, retrieve_bm25
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.storage.interfaces import Message
import time

def fake_embed(text): return [float(len(text) % 5)] * 8

def test_retrieve_multi_4_paths(tmp_data):
    store = SqliteStore()
    store.upsert_message(Message("m1","a1","c1",False,"x",1,"chat","invoice 123",True,int(time.time())))
    store.upsert_profile_field("cust1", "country", "USA", "manual")
    vs = ChromaStore(embedding_fn=fake_embed)
    vs.upsert_chunks([{"id":"c1","text":"product spec","metadata":{"doc_id":"d1","chunk_idx":0}}])
    res = retrieve_multi(store, vs, "invoice", customer_id="cust1", chat_id="c1")
    sources = {r["source"] for r in res}
    assert "profile" in sources
    assert "bm25_msg" in sources


def test_retrieve_multi_degrades_when_vector_store_fails(tmp_data):
    """向量库查询抛错 (如 ChromaDB 'Error finding id') 时降级为 BM25, 不阻塞回复生成。"""
    store = SqliteStore()
    store.upsert_message(Message("m1","a1","c1",False,"x",1,"chat","invoice 123",True,int(time.time())))

    class BoomVectorStore:
        def query_messages(self, *a, **k):
            raise RuntimeError("Error executing plan: Internal error: Error finding id")
        def query_chunks(self, *a, **k):
            raise RuntimeError("Error executing plan: Internal error: Error finding id")

    res = retrieve_multi(store, BoomVectorStore(), "invoice", customer_id="cust1", chat_id="c1")
    sources = {r["source"] for r in res}
    assert "bm25_msg" in sources  # BM25 仍提供关键词上下文
    assert "chunk_vector" not in sources
