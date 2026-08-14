# tests/web/test_tiering_api.py
from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore


def test_analyze_creates_task(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/tiering/analyze", json={"customer_ids": ["c1"]})
    assert r.status_code == 200
    assert "task_id" in r.json()
    task = store.get_tiering_task(r.json()["task_id"])
    assert task["status"] == "pending"
    assert task["customer_ids"] == ["c1"]


def test_analyze_defaults_to_recent_active(tmp_data):
    import time
    store = SqliteStore()
    now = int(time.time())
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch1", "c1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch1", "a1", "ch1", "A", "single", 0))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m1", "a1", "ch1", 0, "x", now - 1000, "chat", "hi", 1, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/tiering/analyze", json={})
    assert r.status_code == 200
    task = store.get_tiering_task(r.json()["task_id"])
    assert task["customer_ids"] == ["c1"]


def test_tiering_status_endpoint(tmp_data):
    store = SqliteStore()
    tid = store.create_tiering_task(["c1"])
    store.update_tiering_task(tid, status="done", progress=1, result='{"tiered": 1}')
    client = TestClient(create_app())
    r = client.get(f"/api/tiering/status/{tid}")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "done"
    assert j["progress"] == 1
    assert j["result"] == '{"tiered": 1}'


def test_tiering_history_endpoint(tmp_data):
    store = SqliteStore()
    store.add_tier_history("c1", "A", "已购", "auto")
    store.add_tier_history("c1", "B", "待跟进", "manual")
    client = TestClient(create_app())
    r = client.get("/api/tiering/history/c1")
    assert r.status_code == 200
    j = r.json()
    assert len(j["history"]) == 2
    assert j["history"][0]["intent_level"] == "A"


def test_customers_page_has_tiering_levels(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",
                       ("c1", "intent_level", "A", "auto", 0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/customers").text
    assert 'value="A"' in html  # 等级下拉含 A


def test_analyze_rejects_non_list_customer_ids(tmp_data):
    """F3: customer_ids 非空字符串数组校验 (字符串/空数组/非字符串元素 → 400)。"""
    client = TestClient(create_app())
    r = client.post("/api/tiering/analyze", json={"customer_ids": "c1"})
    assert r.status_code == 400
    r = client.post("/api/tiering/analyze", json={"customer_ids": []})
    assert r.status_code == 400
    r = client.post("/api/tiering/analyze", json={"customer_ids": [1, 2]})
    assert r.status_code == 400


def test_analyze_reports_dropped_over_max(tmp_data, monkeypatch):
    """F3: 超过 tiering_max_customers 截断并在响应中报告 dropped 数。"""
    from app.config import settings
    monkeypatch.setattr(settings, "tiering_max_customers", 2)
    store = SqliteStore()
    for i in range(5):
        store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                           (f"c{i}", f"C{i}", str(i), None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/tiering/analyze",
                    json={"customer_ids": [f"c{i}" for i in range(5)]})
    assert r.status_code == 200
    j = r.json()
    assert j["dropped"] == 3
    task = store.get_tiering_task(j["task_id"])
    assert task["customer_ids"] == ["c0", "c1"]
