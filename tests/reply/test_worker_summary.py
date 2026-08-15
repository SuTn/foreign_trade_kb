# tests/reply/test_worker_summary.py
import json
import time

from app.web.app import create_app
from app.web import worker
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Message
from app.llm.interfaces import LLM


class FakeLLM(LLM):
    def __init__(self, resp):
        self.resp = resp
        self.prompts = []

    def generate(self, s, u, max_tokens=1024):
        self.prompts.append(u)
        return self.resp


def _msg(mid, chat, from_me, body, ts):
    return Message(mid, "a1", chat, from_me, None, ts, "chat", body, bool(body), int(time.time()))


def _make_app(store, llm):
    app = create_app()
    app.state.sqlite_store = store
    app.state.llm = llm
    return app


def test_execute_summary_task_writes_summary(tmp_data):
    """worker 执行摘要任务: running → 生成 → done, 结果写入 customer_summaries。"""
    store = SqliteStore()
    store.upsert_message(_msg("1", "c1", False, "想买 LED-100", 100))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    llm = FakeLLM('{"overview": "o", "intent_vehicle": "LED-100"}')
    app = _make_app(store, llm)
    tid = store.create_summary_task("cust1")
    worker._execute_summary_task(app, store, store.get_summary_task(tid))
    t = store.get_summary_task(tid)
    assert t["status"] == "done"
    result = json.loads(t["result"])
    assert result["intent_vehicle"] == "LED-100"
    s = store.get_customer_summary("cust1")
    assert s["intent_vehicle"] == "LED-100"


def test_execute_summary_task_failure_marks_failed(tmp_data):
    """执行异常 → 任务 failed, error 可读。"""
    class BoomLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            raise RuntimeError("LLM 挂了")

    store = SqliteStore()
    store.upsert_message(_msg("1", "c1", False, "hello", 100))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    app = _make_app(store, BoomLLM())
    tid = store.create_summary_task("cust1")
    worker._execute_summary_task(app, store, store.get_summary_task(tid))
    t = store.get_summary_task(tid)
    assert t["status"] == "failed"
    assert "LLM 挂了" in t["error"]


def test_worker_loop_consumes_summary_task(tmp_data):
    """后台线程 _background_loop 消费 summary_tasks。"""
    store = SqliteStore()
    store.upsert_message(_msg("1", "c1", False, "想买 LED-100", 100))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    llm = FakeLLM('{"overview": "o", "intent_vehicle": "LED-100"}')
    app = _make_app(store, llm)
    tid = store.create_summary_task("cust1")
    # 用自定义异常终止无限循环 (与 test_resilience._StopLoop 同构)
    class _StopLoop(Exception):
        pass

    import app.web.worker as w
    real_sleep = w.time.sleep
    w.time.sleep = lambda s: (_ for _ in ()).throw(_StopLoop())
    try:
        try:
            w._background_loop(app)
        except _StopLoop:
            pass
    finally:
        w.time.sleep = real_sleep
    t = store.get_summary_task(tid)
    assert t["status"] == "done"
