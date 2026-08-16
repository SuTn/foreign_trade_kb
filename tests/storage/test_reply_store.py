# tests/storage/test_reply_store.py
import time
import sqlite3
from app.storage.sqlite_store import SqliteStore


def test_create_and_query_reply_task(tmp_data):
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    assert isinstance(sid, str) and sid
    assert store.find_or_create_reply_session("cust1", "c1") == sid  # find-or-create 幂等
    task_id = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    t = store.get_reply_task(task_id)
    assert t["status"] == "pending"
    assert t["mode"] == "generate"
    assert t["session_id"] == sid
    store.create_reply_task("cust1", "c1", "hi2", "concise", sid, "regenerate")
    assert store.next_pending_reply_task()["id"] == task_id  # 最早 pending 优先


def test_reply_task_status_transitions(tmp_data):
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    store.update_reply_task(tid, status="running")
    assert store.get_reply_task(tid)["status"] == "running"
    store.update_reply_task(tid, status="done", result='{"reply": "x"}')
    assert store.get_reply_task(tid)["result"] == '{"reply": "x"}'
    assert store.next_pending_reply_task() is None


def test_session_history_roundtrip_and_limit(tmp_data):
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    store.append_session_message(sid, "user", "m1")
    store.append_session_message(sid, "assistant", "a1")
    store.append_session_message(sid, "user", "m2")
    hist = store.get_session_history(sid, limit=10)
    assert [h["role"] for h in hist] == ["user", "assistant", "user"]
    # 超限取最新 10 条 (按 ts 控制唯一顺序)
    now = int(time.time())
    for i in range(12):
        store.conn.execute("INSERT INTO reply_session_messages VALUES(?,?,?,?,?)",
                           (f"x{i}", sid, "user", f"m{i}", now + 1 + i))
    store.conn.commit()
    hist2 = store.get_session_history(sid, limit=10)
    assert len(hist2) == 10
    assert hist2[0]["content"] == "m2"   # 最新 10 条, 正序
    assert hist2[-1]["content"] == "m11"


def test_legacy_tasks_marked_failed(tmp_data):
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    store.conn.execute("UPDATE reply_tasks SET status='running' WHERE id=?", (tid,))
    store.conn.commit()
    store.mark_legacy_reply_tasks_failed()
    assert store.get_reply_task(tid)["status"] == "failed"
    assert "清理" in store.get_reply_task(tid)["error"]


def test_stuck_running_tasks_marked_failed(tmp_data):
    """回复看门狗: 超时的 running 任务标记 failed, 让前端轮询退出「正在生成…」。"""
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    store.update_reply_task(tid, status="running")
    old = int(time.time()) - 200
    store.conn.execute("UPDATE reply_tasks SET updated_at=? WHERE id=?", (old, tid))
    store.conn.commit()
    n = store.mark_stuck_reply_tasks_failed(timeout_sec=180)
    assert n == 1
    t = store.get_reply_task(tid)
    assert t["status"] == "failed"
    assert "超时" in t["error"]


def test_stuck_watchdog_ignores_pending(tmp_data):
    """回复看门狗: pending (排队中) 任务不被误杀, 仅杀 running。"""
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    old = int(time.time()) - 1000
    store.conn.execute("UPDATE reply_tasks SET updated_at=? WHERE id=?", (old, tid))
    store.conn.commit()
    n = store.mark_stuck_reply_tasks_failed(timeout_sec=180)
    assert n == 0
    assert store.get_reply_task(tid)["status"] == "pending"


def test_create_reply_task_persists_generation_params(tmp_data):
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate",
                                  language="ru", scenario="payment", formality="formal")
    t = store.get_reply_task(tid)
    assert t["language"] == "ru"
    assert t["scenario"] == "payment"
    assert t["formality"] == "formal"


def test_create_reply_task_defaults_generation_params(tmp_data):
    """缺省语言/场景/语气为 NULL, 生成器侧回退默认 (zh/auto/casual)。"""
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    t = store.get_reply_task(tid)
    assert t["language"] is None
    assert t["scenario"] is None
    assert t["formality"] is None


def test_legacy_db_upgrade_adds_generation_columns(tmp_data):
    """multilingual-reply-generation: 旧库 (12 列 reply_tasks) 升级路径 — ALTER 补列后可读写。"""
    from app.config import settings
    conn = sqlite3.connect(str(settings.sqlite_path))
    conn.execute(
        "CREATE TABLE reply_tasks("
        "id TEXT PRIMARY KEY, customer_id TEXT, chat_id TEXT, message TEXT, style TEXT, "
        "session_id TEXT, mode TEXT, status TEXT, result TEXT, error TEXT, "
        "created_at INTEGER, updated_at INTEGER)")
    conn.commit()
    conn.close()
    store = SqliteStore()  # _init_schema: executescript (旧表跳过) + 3×ALTER 补列
    SqliteStore()          # 幂等: 再次初始化不抛错 (列已存在被捕获)
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(reply_tasks)").fetchall()}
    assert {"language", "scenario", "formality"} <= cols
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate", language="ru")
    t = store.get_reply_task(tid)
    assert t["language"] == "ru"
