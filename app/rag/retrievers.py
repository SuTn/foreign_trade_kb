# app/rag/retrievers.py
"""多路召回: 客户画像 + 历史聊天向量 + 产品知识向量 + BM25(FTS5)。"""
from app.storage.interfaces import StructuredStore, VectorStore

def retrieve_profile(store: StructuredStore, customer_id: str) -> list[dict]:
    return [{"text": f"{p.field}: {p.value}", "source": "profile", "metadata": {"field": p.field}}
            for p in store.get_profile(customer_id)]

def retrieve_message_vector(vector_store: VectorStore, query: str, chat_id: str | None, top_k=5) -> list[dict]:
    return [{**r, "source": "message_vector"} for r in vector_store.query_messages(query, chat_id=chat_id, top_k=top_k)]

def retrieve_chunk_vector(vector_store: VectorStore, query: str, top_k=5) -> list[dict]:
    return [{**r, "source": "chunk_vector"} for r in vector_store.query_chunks(query, top_k=top_k)]

def retrieve_bm25(store: StructuredStore, query: str, top_k=5) -> list[dict]:
    """BM25 关键词召回: messages_fts + doc_chunks_fts 两路并行。"""
    msgs = store.search_fts("messages", query, top_k)
    chunks = store.search_fts("doc_chunks", query, top_k)
    return [{"text": m.get("body", ""), "source": "bm25_msg"} for m in msgs] + \
           [{"text": c.get("text", ""), "source": "bm25_chunk"} for c in chunks]

def retrieve_multi(store, vector_store, query, customer_id=None, chat_id=None, top_k=5) -> list[dict]:
    """4 路并行召回合并。"""
    results = []
    if customer_id:
        results += retrieve_profile(store, customer_id)
    results += retrieve_message_vector(vector_store, query, chat_id, top_k)
    results += retrieve_chunk_vector(vector_store, query, top_k)
    results += retrieve_bm25(store, query, top_k)
    return results
