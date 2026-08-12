from fastapi.testclient import TestClient
from app.web.app import create_app
from tests.conftest import reply_task_id, wait_reply_done


def test_reply_post_returns_polling_fragment_and_pending_task(tmp_data):
    """2.3/2.4 提交侧: POST 插任务返回轮询片段, 任务初始 pending, status 返回处理中。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())  # 无 with → worker 不启动, 只验证提交侧
    r = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "hi"})
    assert r.status_code == 200
    assert "正在生成回复" in r.text
    assert "every 1s" in r.text
    tid = reply_task_id(r.text)
    row = SqliteStore().conn.execute("SELECT * FROM reply_tasks WHERE id=?", (tid,)).fetchone()
    assert row["status"] == "pending"
    assert row["mode"] == "generate"
    r2 = client.get(f"/api/reply/status/{tid}")
    assert r2.status_code == 200
    assert "正在生成回复" in r2.text


def test_stale_tasks_marked_failed_on_startup(tmp_data):
    """D7: 启动时遗留 pending/running 任务置 failed。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    store.conn.execute("UPDATE reply_tasks SET status='running'")
    store.conn.commit()
    with TestClient(create_app()) as client:
        rows = SqliteStore().conn.execute("SELECT status FROM reply_tasks").fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert client.app.state.llm is not None  # D3 单例已建
        assert client.app.state.reply_worker is not None


def test_reply_full_lifecycle(tmp_data, monkeypatch):
    """2.6: 提交→轮询→done, 结果渲染复制按钮, session_id 透传。"""
    from app.web import routes

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            return "建议回复: 异步"

    class FakeRerank:
        def rerank(self, q, c, top_k=8):
            return c[:top_k]

    class FakeEmbed:
        def embed(self, text):
            return [1.0] * 8

    monkeypatch.setattr(routes, "CloudLLM", FakeLLM)
    monkeypatch.setattr(routes, "get_reranker", lambda: FakeRerank())
    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    with TestClient(create_app()) as client:
        r = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "hi"})
        assert r.status_code == 200
        tid = reply_task_id(r.text)
        done = wait_reply_done(client, tid)
        assert "建议回复: 异步" in done.text
        assert "data-copy" in done.text
