from fastapi.testclient import TestClient
from app.web.app import create_app


def test_customers_page():
    client = TestClient(create_app())
    assert client.get("/customers").status_code == 200


def test_export_vault_endpoint(tmp_data):
    client = TestClient(create_app())
    r = client.post("/api/knowledge/export-vault")
    assert r.status_code == 200
    assert "exported" in r.json()
