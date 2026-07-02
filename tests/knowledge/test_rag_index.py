from app.knowledge.rag_index import RagIndex
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore

def fake_embed(text): return [float(len(text) % 5)] * 8

def test_rag_index_inserts_chunks(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO documents VALUES('d1','f.pdf','pdf','docreader','done',1)")
    store.conn.commit()
    vs = ChromaStore(embedding_fn=fake_embed)
    ri = RagIndex(store, vs)
    ri.index("d1", "product spec " * 50)
    rows = store.conn.execute("SELECT * FROM doc_chunks WHERE doc_id='d1'").fetchall()
    assert len(rows) > 0
