# app/rag/pipeline.py
"""RAG 管线骨架 (插件式): query → 多路召回 → rerank → 上下文压缩 → 父子块展开 → 生成。
可选插件 (查询理解/改写) 默认不挂载。"""
from app.rag.retrievers import retrieve_multi
from app.rag.reranker import rerank
from app.config import settings

class RagPipeline:
    def __init__(self, store, vector_store, reranker, llm):
        self.store = store; self.vector_store = vector_store
        self.reranker = reranker; self.llm = llm
        self.plugins = []  # 可选插件 (查询理解/改写), MVP 默认空

    def run(self, query: str, customer_id=None, chat_id=None, system="", top_k=None) -> dict:
        top_k = top_k or settings.rerank_top_k
        # 1. 多路召回
        candidates = retrieve_multi(self.store, self.vector_store, query, customer_id, chat_id)
        # 2. rerank
        ranked = self.reranker.rerank(query, candidates, top_k=top_k)
        # 3. 上下文压缩/去重 + 父子块展开
        context = self._compress_and_expand(ranked)
        # 4. 生成
        answer = self.llm.generate(system, f"上下文:\n{context}\n\n问题/消息: {query}")
        return {"answer": answer, "sources": ranked}

    def _compress_and_expand(self, ranked: list[dict]) -> str:
        seen = set(); parts = []
        for r in ranked:
            t = r.get("text", "")
            if t in seen: continue
            seen.add(t)
            parts.append(t)
            # 父子块展开: 若有 parent_chunk_id, 补全父块
            pid = r.get("metadata", {}).get("parent_chunk_id")
            if pid:
                row = self.store.conn.execute("SELECT text FROM doc_chunks WHERE chunk_idx=? AND doc_id=?",
                    (pid, r["metadata"].get("doc_id"))).fetchone()
                if row and row["text"] not in seen:
                    parts.append(row["text"]); seen.add(row["text"])
        return "\n---\n".join(parts)[:settings.context_token_limit*4]
