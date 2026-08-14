# tests/reply/test_worker_tiering.py
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
        return '{"intent_level": "A", "tags": "已购"}'


def _make_app(store, llm):
    app = create_app()
    app.state.sqlite_store = store
    app.state.chroma_store = ChromaStore(embedding_fn=lambda t: [1.0] * 8)
    app.state.reranker = FakeReranker()
    app.state.llm = llm
    return app


def test_execute_tiering_task_marks_done(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch1", "c1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch1", "a1", "ch1", "A", "single", 0))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m1", "a1", "ch1", 0, "x", 1, "chat", "want LED", 1, 0, None))
    store.conn.commit()
    llm = FakeLLM()
    app = _make_app(store, llm)
    tid = store.create_tiering_task(["c1"])
    worker._execute_tiering_task(app, store, store.get_tiering_task(tid))
    done = store.get_tiering_task(tid)
    assert done["status"] == "done"
    assert done["progress"] == 1
    assert "tiered" in done["result"]
    assert len(store.get_tier_history("c1")) == 1


def test_execute_tiering_failure_marks_failed(tmp_data):
    class BoomLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            raise RuntimeError("LLM 挂了")

    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch1", "c1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch1", "a1", "ch1", "A", "single", 0))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m1", "a1", "ch1", 0, "x", 100, "chat", "hi", 1, 0, None))
    store.conn.commit()
    app = _make_app(store, BoomLLM())
    tid = store.create_tiering_task(["c1"])
    worker._execute_tiering_task(app, store, store.get_tiering_task(tid))
    t = store.get_tiering_task(tid)
    assert t["status"] == "failed"
    assert "LLM 挂了" in t["error"]


def test_tiering_consumes_pending_reply_first(tmp_data):
    """D7 (F5): 分层期间存在 pending 回复任务 → 回复优先消费, 不阻塞回复。"""
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch1", "c1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch1", "a1", "ch1", "A", "single", 0))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m1", "a1", "ch1", 0, "x", 1, "chat", "want LED", 1, 0, None))
    store.conn.commit()
    llm = FakeLLM()
    app = _make_app(store, llm)
    sid = store.find_or_create_reply_session("c1", "ch1")
    rtid = store.create_reply_task("c1", "ch1", "hi", "default", sid, "generate")
    tid = store.create_tiering_task(["c1"])
    worker._execute_tiering_task(app, store, store.get_tiering_task(tid))
    assert store.get_reply_task(rtid)["status"] == "done"
    assert store.get_tiering_task(tid)["status"] == "done"
    assert store.next_pending_reply_task() is None


def test_execute_tiering_result_aggregates_real_counts(tmp_data):
    """worker 结果聚合真实 tiered/untiered (而非 total)。"""
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch1", "c1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch1", "a1", "ch1", "A", "single", 0))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m1", "a1", "ch1", 0, "x", 1, "chat", "want LED", 1, 0, None))
    store.conn.commit()
    llm = FakeLLM()
    app = _make_app(store, llm)
    tid = store.create_tiering_task(["c1", "c-nodata"])  # c-nodata 无消息 → untiered
    worker._execute_tiering_task(app, store, store.get_tiering_task(tid))
    result = json.loads(store.get_tiering_task(tid)["result"])
    assert result == {"tiered": 1, "untiered": 1}
