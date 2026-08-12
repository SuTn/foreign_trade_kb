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


def test_delete_chunks_by_doc(tmp_data):
    s = ChromaStore(embedding_fn=fake_embed)
    s.upsert_chunks([{"id": "c1", "text": "spec sheet", "metadata": {"doc_id": "d1"}},
                     {"id": "c2", "text": "price list", "metadata": {"doc_id": "d1"}},
                     {"id": "c3", "text": "other doc", "metadata": {"doc_id": "d2"}}])
    s.delete_chunks("d1")
    res = s.query_chunks("spec sheet", top_k=10)
    assert all(c["metadata"]["doc_id"] != "d1" for c in res)
    assert any(c["metadata"]["doc_id"] == "d2" for c in res)


def test_delete_message_vectors_by_chat(tmp_data):
    s = ChromaStore(embedding_fn=fake_embed)
    s.upsert_message_vector("k1", "hello c1", {"chat_id": "c1", "day": "2026-01-01"})
    s.upsert_message_vector("k2", "hello c2", {"chat_id": "c2", "day": "2026-01-01"})
    s.delete_message_vectors("c1")
    res = s.query_messages("hello", top_k=10)
    assert [m["metadata"]["chat_id"] for m in res] == ["c2"]  # c1 向量已删, c2 保留
