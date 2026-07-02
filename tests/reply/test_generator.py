# tests/reply/test_generator.py
from app.reply.generator import generate_reply
from app.rag.pipeline import RagPipeline
from app.rag.reranker import FakeReranker
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.llm.interfaces import LLM

class FakeLLM(LLM):
    def generate(self, s, u, max_tokens=1024):
        return "建议回复: 感谢询价, LED灯报价 $5/个"

def fake_embed(text): return [1.0]*8

def test_generate_reply_returns_reply_and_sources(tmp_data):
    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), FakeLLM())
    r = generate_reply(pipe, "cust1", "c1", "LED灯多少钱?")
    assert "LED" in r["reply"]
    assert isinstance(r["sources"], list)
