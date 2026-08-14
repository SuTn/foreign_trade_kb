# tests/reply/test_worker.py
import json

from app.web.app import create_app
from app.web import worker
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.rag.reranker import FakeReranker
from app.llm.interfaces import LLM


class FakeLLM(LLM):
    def __init__(self):
        self.prompts = []

    def generate(self, s, u, max_tokens=1024):
        self.prompts.append(s)
        return "worker回复"


def _make_app(store, llm):
    app = create_app()
    app.state.sqlite_store = store
    app.state.chroma_store = ChromaStore(embedding_fn=lambda t: [1.0] * 8)
    app.state.reranker = FakeReranker()
    app.state.llm = llm
    return app


def test_execute_generate_appends_history(tmp_data):
    """主 generate: 追加 user+assistant 到会话历史 (D4)。"""
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    llm = FakeLLM()
    app = _make_app(store, llm)
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    worker._execute_reply_task(app, store, store.get_reply_task(tid))
    done = store.get_reply_task(tid)
    assert done["status"] == "done"
    assert "worker回复" in done["result"]
    msgs = store.conn.execute("SELECT * FROM reply_session_messages ORDER BY ts").fetchall()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hi"


def test_execute_regenerate_does_not_append(tmp_data):
    """regenerate: 只读历史做上下文, 不追加 (替代候选不污染历史)。"""
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    store.append_session_message(sid, "user", "hi")
    store.append_session_message(sid, "assistant", "旧回复")
    llm = FakeLLM()
    app = _make_app(store, llm)
    tid = store.create_reply_task("cust1", "c1", "hi", "concise", sid, "regenerate")
    worker._execute_reply_task(app, store, store.get_reply_task(tid))
    assert store.get_reply_task(tid)["status"] == "done"
    msgs = store.conn.execute("SELECT * FROM reply_session_messages").fetchall()
    assert len(msgs) == 2  # 仍只有 generate 追加的 2 条
    assert "旧回复" in llm.prompts[0]  # 历史作为上下文传入


def test_execute_failure_marks_failed(tmp_data):
    """执行异常 → 任务 failed, error 截断可读。"""
    class BoomLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            raise RuntimeError("LLM 挂了")

    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    app = _make_app(store, BoomLLM())
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    worker._execute_reply_task(app, store, store.get_reply_task(tid))
    t = store.get_reply_task(tid)
    assert t["status"] == "failed"
    assert "LLM 挂了" in t["error"]


def test_execute_reply_passes_generation_params(tmp_data):
    """multilingual-copy: worker 将 language/scenario/formality 传给 generate_reply。"""
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    llm = FakeLLM()
    app = _make_app(store, llm)
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate",
                                  language="ru", scenario="payment", formality="formal")
    worker._execute_reply_task(app, store, store.get_reply_task(tid))
    assert "俄语" in llm.prompts[0]
    assert "付款" in llm.prompts[0]
    assert "正式" in llm.prompts[0]
    done = store.get_reply_task(tid)
    assert done["status"] == "done"
    result = json.loads(done["result"])
    assert result["language"] == "ru" and result["scenario"] == "payment" and result["formality"] == "formal"
