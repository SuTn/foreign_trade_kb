# tests/storage/test_tiering_store.py
import time
from app.storage.sqlite_store import SqliteStore


def test_tier_history_roundtrip(tmp_data):
    store = SqliteStore()
    store.add_tier_history("cust1", "A", "已购,议价中", "auto")
    store.add_tier_history("cust1", "B", "待跟进", "manual")
    hist = store.get_tier_history("cust1")
    assert len(hist) == 2
    assert hist[0]["intent_level"] == "A"
    assert hist[0]["source"] == "auto"
    assert hist[1]["intent_level"] == "B"
    assert hist[1]["source"] == "manual"
    assert hist[0]["tags"] == "已购,议价中"


def test_tier_history_empty(tmp_data):
    store = SqliteStore()
    assert store.get_tier_history("nobody") == []


def test_tiering_task_lifecycle(tmp_data):
    store = SqliteStore()
    tid = store.create_tiering_task(["cust1", "cust2"])
    t = store.get_tiering_task(tid)
    assert t["status"] == "pending"
    assert t["customer_ids"] == ["cust1", "cust2"]
    assert store.next_pending_tiering_task()["id"] == tid
    store.update_tiering_task(tid, status="running", progress=1)
    store.update_tiering_task(tid, status="done", progress=2,
                              result='{"tiered": 2}')
    done = store.get_tiering_task(tid)
    assert done["status"] == "done"
    assert done["progress"] == 2
    assert done["result"] == '{"tiered": 2}'
    assert store.next_pending_tiering_task() is None


def test_recent_active_customers(tmp_data):
    store = SqliteStore()
    now = int(time.time())
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "A", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c2", "B", "2", None, None, 0, None))
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c3", "C", "3", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch1", "c1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch2", "c2", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch1", "a1", "ch1", "A", "single", 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch2", "a1", "ch2", "B", "single", 0))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m1", "a1", "ch1", 0, "x", now - 100, "chat", "hi", 1, 0, None))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m2", "a1", "ch2", 0, "x", now - 100 * 86400, "chat", "old", 1, 0, None))
    store.conn.commit()
    active = store.list_recent_active_customers(days=30)
    assert active == ["c1"]  # c1 近期活跃; c2 超 30 天; c3 无消息


def test_tiering_task_corrupt_customer_ids_guarded(tmp_data):
    """损坏的 customer_ids JSON 不抛错, 返回 [] 避免 worker 无限重试。"""
    store = SqliteStore()
    tid = store.create_tiering_task(["c1"])
    store.conn.execute("UPDATE tiering_tasks SET customer_ids=? WHERE id=?",
                       ("{{{corrupt", tid))
    store.conn.commit()
    assert store.get_tiering_task(tid)["customer_ids"] == []
    t = store.next_pending_tiering_task()
    assert t is not None
    assert t["customer_ids"] == []