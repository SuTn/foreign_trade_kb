# tests/web/test_send_api.py
from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore
from app.storage.runtime_settings import RuntimeSettings


def test_send_rejected_when_disabled(tmp_data):
    client = TestClient(create_app())
    r = client.post("/api/send", json={"chat_id": "c1", "text": "hi"})
    assert r.status_code == 403


def test_send_creates_task_when_enabled(tmp_data):
    store = SqliteStore()
    RuntimeSettings(store).set("send_enabled", "true")
    client = TestClient(create_app())
    r = client.post("/api/send", json={"chat_id": "c1", "text": "hi"})
    assert r.status_code == 200
    import re
    m = re.search(r"/api/send/status/(\d+)", r.text)
    assert m, f"未找到 task_id: {r.text[:200]}"
    rid = int(m.group(1))
    row = SqliteStore().get_send_request(rid)
    assert row["status"] == "pending" and row["text"] == "hi"


def test_send_status_endpoint(tmp_data):
    store = SqliteStore()
    rid = store.create_send_request("c1", "hi")
    client = TestClient(create_app())
    assert "发送中" in client.get(f"/api/send/status/{rid}").text
    store.mark_send_request_done(rid)
    assert "已发送" in client.get(f"/api/send/status/{rid}").text
