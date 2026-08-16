# tests/storage/test_send_store.py
from app.storage.sqlite_store import SqliteStore


def test_send_request_lifecycle(tmp_data):
    store = SqliteStore()
    rid = store.create_send_request("c1", "hello")
    assert isinstance(rid, int) and rid > 0
    req = store.get_send_request(rid)
    assert req["status"] == "pending" and req["text"] == "hello"
    assert store.next_pending_send_request()["id"] == rid
    store.mark_send_request_running(rid)
    assert store.get_send_request(rid)["status"] == "running"
    store.mark_send_request_done(rid)
    assert store.get_send_request(rid)["status"] == "done"
    assert store.next_pending_send_request() is None


def test_send_request_retries_up_to_three(tmp_data):
    store = SqliteStore()
    rid = store.create_send_request("c1", "hi")
    store.bump_send_request_attempts(rid, "boom")
    store.bump_send_request_attempts(rid, "boom")
    assert store.next_pending_send_request()["id"] == rid  # 2 次失败仍可重试
    store.bump_send_request_attempts(rid, "boom")
    assert store.next_pending_send_request() is None  # 满 3 次不再取


def test_send_request_mark_failed_terminal(tmp_data):
    store = SqliteStore()
    rid = store.create_send_request("c1", "hi")
    store.mark_send_request_failed(rid, "发送功能未开启")
    r = store.get_send_request(rid)
    assert r["status"] == "failed" and r["done"] == 1
    assert store.next_pending_send_request() is None


def test_chat_previews_upsert_and_lookup(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,NULL,NULL,?,NULL)",
                       ("cust1", "Alice", "10086", 0))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    store.upsert_chat_previews([{"chat_id": "c1", "unread_count": 3, "preview": "need price"}])
    p = store.get_customers_chat_preview(["cust1"])
    assert p["cust1"]["unread"] == 3
    assert p["cust1"]["preview"] == "need price"
