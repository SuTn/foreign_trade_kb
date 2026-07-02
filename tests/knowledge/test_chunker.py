from app.knowledge.chunker import chunk_text

def test_chunk_overlap():
    text = "a" * 1000
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    assert chunks[0]["parent_chunk_id"] == 0
    assert chunks[1]["parent_chunk_id"] == 0  # 前4个同父
