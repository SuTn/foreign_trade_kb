from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Message


def _seed_all_sources():
    """造四源都命中 'LED' 的数据。"""
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "ACME LED", "10086", "ACME", "USA", 0, None))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",
                       ("c1", "interest", "LED 灯带", "auto", 0))
    store.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                       ("d1", "a.md", "md", "docreader", "done", 1))
    store.conn.execute("INSERT INTO doc_chunks VALUES(?,?,?,?,?,?)",
                       ("ch1", "d1", 0, "LED 产品规格", "0", "ch1"))
    store.conn.execute("INSERT INTO doc_chunks_fts(rowid, text) "
                       "VALUES((SELECT rowid FROM doc_chunks WHERE id='ch1'), ?)",
                       ("LED 产品规格",))
    store.upsert_message(Message("m1", "a1", "c1", False, "x", 1000, "chat", "LED invoice", True, 1))
    store.conn.commit()


def test_api_search_groups_four_sources(tmp_data):
    _seed_all_sources()
    j = TestClient(create_app()).get("/api/search", params={"q": "LED"}).json()
    assert j["query"] == "LED"
    assert [c["id"] for c in j["customers"]] == ["c1"]
    assert j["messages"] == [{"chat_id": "c1", "ts": 1000, "body": "LED invoice"}]
    assert j["knowledge"] == [{"doc_id": "d1", "text": "LED 产品规格"}]
    assert j["profiles"] == [{"customer_id": "c1", "field": "interest", "value": "LED 灯带"}]


def test_api_search_empty_query_returns_empty_groups(tmp_data):
    j = TestClient(create_app()).get("/api/search", params={"q": ""}).json()
    assert j == {"query": "", "customers": [], "messages": [], "knowledge": [], "profiles": []}


def test_api_search_htmx_returns_rendered_partial(tmp_data):
    _seed_all_sources()
    r = TestClient(create_app()).get("/api/search", params={"q": "LED"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "客户 (1)" in r.text and "消息 (1)" in r.text
    assert "知识库 (1)" in r.text and "画像 (1)" in r.text


def test_api_search_degrades_on_error(tmp_data, monkeypatch):
    from app.storage.sqlite_store import SqliteStore as SS

    def boom(self, query, limit=20):
        raise RuntimeError("db 故障")

    monkeypatch.setattr(SS, "search_customers", boom)
    j = TestClient(create_app()).get("/api/search", params={"q": "LED"}).json()
    assert "error" in j
    assert j["customers"] == []


def test_search_page_renders(tmp_data):
    html = TestClient(create_app()).get("/search").text
    assert 'hx-get="/api/search"' in html
    assert "keyup changed delay:300ms" in html
    assert 'id="search-results"' in html
    assert '<a href="/search">搜索</a>' in html
