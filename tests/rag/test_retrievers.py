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
