# tests/web/test_tiering_frontend.py
from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore


def test_customer_card_shows_tier_badge(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",
                       ("c1", "intent_level", "A", "auto", 0))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",
                       ("c1", "tags", "已购,议价中", "auto", 0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/customers").text
    assert 'tier-badge' in html
    assert "A" in html
    assert "已购" in html


def test_customer_page_has_tier_filter_dropdown(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",
                       ("c1", "intent_level", "A", "auto", 0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/customers").text
    assert 'id="filter-tier"' in html
    assert 'value="A"' in html


def test_chat_page_has_tier_history_section(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.add_tier_history("c1", "A", "已购", "auto")
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/customers/c1").text
    assert "分层历史" in html
    assert "A" in html
    assert "已购" in html
