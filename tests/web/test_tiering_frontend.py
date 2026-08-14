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


def test_profile_clear_intent_level_clears_and_hides_badge(tmp_data):
    """F1: 提交空 intent_level (=未分层) 清除等级, 卡片不渲染空徽章, 并记录 manual 历史。"""
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.upsert_profile_field("c1", "intent_level", "A", "auto")
    store.upsert_profile_field("c1", "tags", "已购", "auto")
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/customers/c1/profile", data={"field": "intent_level", "value": ""})
    assert r.status_code == 200
    prof = {p.field: p.value for p in store.get_profile("c1")}
    assert prof["intent_level"] == ""
    hist = store.get_tier_history("c1")
    assert hist[-1]["intent_level"] == ""
    assert hist[-1]["source"] == "manual"
    html = client.get("/customers").text
    assert "tier-badge" not in html
    assert 'class="tier-badge tier-"' not in html


def test_profile_clear_tags_posts_empty(tmp_data):
    """F1: 提交空 tags 清除标签。"""
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.upsert_profile_field("c1", "tags", "已购", "auto")
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/customers/c1/profile", data={"field": "tags", "value": ""})
    assert r.status_code == 200
    prof = {p.field: p.value for p in store.get_profile("c1")}
    assert prof["tags"] == ""


def test_profile_save_intent_level_appends_manual_history(tmp_data):
    """F2: 表单保存 intent_level 追加 manual 历史行。"""
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    client.post("/customers/c1/profile", data={"field": "intent_level", "value": "B"})
    hist = store.get_tier_history("c1")
    assert len(hist) == 1
    assert hist[0]["intent_level"] == "B"
    assert hist[0]["source"] == "manual"


def test_profile_save_tags_appends_manual_history(tmp_data):
    """F2: 表单保存 tags 追加 manual 历史行。"""
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    client.post("/customers/c1/profile", data={"field": "tags", "value": "已购,议价中"})
    hist = store.get_tier_history("c1")
    assert len(hist) == 1
    assert hist[0]["tags"] == "已购,议价中"
    assert hist[0]["source"] == "manual"
