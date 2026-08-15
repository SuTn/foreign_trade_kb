# tests/storage/test_summary_store.py
import time
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Message


def _msg(mid, chat, from_me, body, ts):
    return Message(mid, "a1", chat, from_me, None, ts, "chat", body, bool(body), int(time.time()))


def test_upsert_and_get_customer_summary(tmp_data):
    store = SqliteStore()
    store.upsert_customer_summary("cust1", {"overview": "o", "intent_vehicle": "LED-100"}, last_ts=100)
    s = store.get_customer_summary("cust1")
    assert s["intent_vehicle"] == "LED-100"
    assert s["last_ts"] == 100
    assert s["updated_at"] > 0


def test_upsert_customer_summary_preserves_last_ts_when_omitted(tmp_data):
    store = SqliteStore()
    store.upsert_customer_summary("cust1", {"overview": "v1"}, last_ts=100)
    # 未传 last_ts → 保留原游标
    store.upsert_customer_summary("cust1", {"overview": "v2"})
    s = store.get_customer_summary("cust1")
    assert s["overview"] == "v2"
    assert s["last_ts"] == 100


def test_get_customer_summary_last_ts_defaults_zero(tmp_data):
    store = SqliteStore()
    assert store.get_customer_summary_last_ts("cust1") == 0
    store.upsert_customer_summary("cust1", {"overview": "o"}, last_ts=200)
    assert store.get_customer_summary_last_ts("cust1") == 200


def test_list_messages_after(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("1", "c1", False, "old", 100))
    store.upsert_message(_msg("2", "c1", False, "mid", 200))
    store.upsert_message(_msg("3", "c1", False, "new", 300))
    after = store.list_messages_after("c1", 150)
    assert [m.body for m in after] == ["mid", "new"]  # 时间正序, 只取 ts>150
    assert store.list_messages_after("c1", 300) == []  # 无 ts>300


def test_summary_task_lifecycle(tmp_data):
    store = SqliteStore()
    tid = store.create_summary_task("cust1")
    assert isinstance(tid, str) and tid
    t = store.get_summary_task(tid)
    assert t["status"] == "pending"
    assert t["customer_id"] == "cust1"
    assert store.next_pending_summary_task()["id"] == tid
    store.update_summary_task(tid, status="running")
    assert store.get_summary_task(tid)["status"] == "running"
    assert store.next_pending_summary_task() is None  # running 不再 pending
    store.update_summary_task(tid, status="done", result='{"overview": "o"}')
    assert store.get_summary_task(tid)["result"] == '{"overview": "o"}'


def test_summary_task_priority_order(tmp_data):
    store = SqliteStore()
    t1 = store.create_summary_task("cust1")
    t2 = store.create_summary_task("cust2")
    assert store.next_pending_summary_task()["id"] == t1  # 最早 pending 优先


def test_mark_legacy_summary_tasks_failed(tmp_data):
    store = SqliteStore()
    store.create_summary_task("cust1")
    store.conn.execute("UPDATE summary_tasks SET status='running'")
    store.conn.commit()
    store.mark_legacy_summary_tasks_failed()
    rows = store.conn.execute("SELECT status FROM summary_tasks").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"