# app/knowledge/chunker.py
from app.config import settings

def chunk_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[dict]:
    """按 chunk_size/overlap 切分, 支持父子块 (parent_chunk_id)。"""
    cs = chunk_size or settings.chunk_size
    ov = overlap or settings.chunk_overlap
    chunks = []
    i = 0
    idx = 0
    while i < len(text):
        piece = text[i:i+cs]
        chunks.append({"chunk_idx": idx, "text": piece, "parent_chunk_id": None})
        i += cs - ov
        idx += 1
    # 父子块: 每 4 个子块归一个父块 (简化策略)
    for i, c in enumerate(chunks):
        c["parent_chunk_id"] = i // 4
    return chunks
