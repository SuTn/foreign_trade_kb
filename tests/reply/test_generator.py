# tests/reply/test_generator.py
from app.reply.generator import generate_reply, regenerate_reply
from app.rag.pipeline import RagPipeline
from app.rag.reranker import FakeReranker
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.llm.interfaces import LLM

class FakeLLM(LLM):
    def generate(self, s, u, max_tokens=1024):
        return "建议回复: 感谢询价, LED灯报价$5/个"

def fake_embed(text): return [1.0]*8

def test_generate_reply_returns_reply_and_sources(tmp_data):
    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), FakeLLM())
    r = generate_reply(pipe, "cust1", "c1", "LED灯多少钱?")
    assert "LED" in r["reply"]
    assert isinstance(r["sources"], list)


def test_generate_reply_style_passed_to_system(tmp_data):
    """reply-assist: style 参数应进入 system 提示词, 决定候选表达风格。"""
    seen = {}

    class CapturingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            seen["system"] = s
            return "回复"

    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), CapturingLLM())
    generate_reply(pipe, "cust1", "c1", "hi", style="concise")
    assert "简洁" in seen["system"]


def test_regenerate_produces_different_style(tmp_data):
    """reply-assist: 重新生成切换风格, 获得不同候选。"""
    styles = []

    class TrackingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            styles.append(s)
            return f"回复 #{len(styles)}"

    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), TrackingLLM())
    r1 = generate_reply(pipe, "cust1", "c1", "hi")
    assert r1["style"] == "default"
    r2 = regenerate_reply(pipe, "cust1", "c1", "hi", previous_style=r1["style"])
    assert r2["style"] != r1["style"]
    assert styles[1] != styles[0]  # 第二次提示词不同


def test_generate_reply_includes_session_history(tmp_data):
    """3.4/3.6: 会话历史作为额外 system 上下文传入 LLM。"""
    seen = {}

    class CapturingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            seen["system"] = s
            return "回复"

    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), CapturingLLM())
    history = [{"role": "user", "content": "LED价格?"},
               {"role": "assistant", "content": "报价$5"}]
    generate_reply(pipe, "cust1", "c1", "何时到货?", history=history)
    assert "本次会话最近对话历史" in seen["system"]
    assert "LED价格" in seen["system"]
    assert "报价$5" in seen["system"]
