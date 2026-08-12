from fastapi.testclient import TestClient
from app.web.app import create_app
from tests.conftest import reply_task_id


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
