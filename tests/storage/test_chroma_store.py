from app.storage.chroma_store import ChromaStore

def fake_embed(text):
    # 简单确定性伪向量, 长度8
    return [float(len(text) % 7)] * 8

def test_upsert_and_query_chunks(tmp_data):
    s = ChromaStore(embedding_fn=fake_embed)
    s.upsert_chunks([{"id": "c1", "text": "product spec sheet", "metadata": {"doc_id": "d1", "chunk_idx": 0}}])
    res = s.query_chunks("product spec", top_k=1)
    assert len(res) == 1
    assert res[0]["id"] == "c1"

def test_message_vector_metadata(tmp_data):
    s = ChromaStore(embedding_fn=fake_embed)
    s.upsert_message_vector("c1:2026-07-01", "hello customer", {"chat_id": "c1", "day": "2026-07-01"})
    res = s.query_messages("hello", chat_id="c1", top_k=1)
    assert res[0]["metadata"]["chat_id"] == "c1"
