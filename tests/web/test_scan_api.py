from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore


def test_scan_accepted_then_busy(tmp_data):
    client = TestClient(create_app())
    r1 = client.post("/api/collector/scan")
    assert r1.status_code == 200
    assert r1.json()["accepted"] is True
    r2 = client.post("/api/collector/scan")   # pending 未消费 → busy
    assert r2.status_code == 409
    j = r2.json()
    assert j.get("busy") is True and "已有扫描" in j["error"]


def test_scan_inserts_row_for_collector(tmp_data):
    client = TestClient(create_app())
    client.post("/api/collector/scan")
    store = SqliteStore()
    rows = store.conn.execute("SELECT * FROM scan_requests WHERE done=0").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "pending"


def test_status_returns_scan_null_when_missing(tmp_data):
    client = TestClient(create_app())
    r = client.get("/api/collector/status")
    assert r.status_code == 200
    j = r.json()
    assert "scan" in j and j["scan"] is None
    assert "status" in j and "alive" in j


def test_status_passthrough_scan_when_present(tmp_data):
    from app.config import settings
    import json
    settings.status_path.write_text(json.dumps(
        {"state": "running", "scan": {"running": True, "current": 5, "total": 40, "ingested": 120}}),
        encoding="utf-8")
    client = TestClient(create_app())
    j = client.get("/api/collector/status").json()
    assert j["scan"] == {"running": True, "current": 5, "total": 40, "ingested": 120}
