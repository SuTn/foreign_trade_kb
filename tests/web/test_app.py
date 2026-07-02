# tests/web/test_app.py
from fastapi.testclient import TestClient
from app.web.app import create_app

def test_index():
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 200

def test_collector_status_endpoint():
    client = TestClient(create_app())
    r = client.get("/api/collector/status")
    assert r.status_code == 200
    assert "alive" in r.json()
