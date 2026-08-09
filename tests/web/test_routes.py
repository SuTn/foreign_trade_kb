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


def test_backfill_endpoint_records_request(tmp_data):
    """3.7: /api/collector/backfill 记录回溯请求, 供采集器轮询执行。"""
    client = TestClient(create_app())
    r = client.post("/api/collector/backfill", json={"chat_id": "c1", "max_scrolls": 5})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    rows = store.conn.execute("SELECT * FROM backfill_requests WHERE done=0").fetchall()
    assert len(rows) == 1
    assert rows[0]["chat_id"] == "c1"
    assert rows[0]["max_scrolls"] == 5


def test_upload_succeeds_when_wiki_fails(tmp_data, monkeypatch):
    """4.11: Wiki 索引失败不影响 RAG 索引 — 上传仍成功, RAG chunks 已入库, 无 wiki_pages。"""
    from app.web import routes
    from app.storage.sqlite_store import SqliteStore

    class FakeEmbed:
        def embed(self, text):
            return [float(len(text) % 7)] * 8

    class FailingLLM:
        def generate(self, system, user, max_tokens=1024):
            raise RuntimeError("Wiki LLM 不可用")

    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    monkeypatch.setattr(routes, "CloudLLM", FailingLLM)
    monkeypatch.setattr(routes, "parse_document", lambda path: "LED 灯产品规格说明 " * 50)

    client = TestClient(create_app())
    r = client.post(
        "/api/knowledge/upload",
        files={"file": ("note.txt", b"hello", "text/plain")},
        data={"filename": "note.txt"},
    )
    assert r.status_code == 200
    doc_id = r.json()["doc_id"]

    store = SqliteStore()
    chunks = store.conn.execute("SELECT * FROM doc_chunks WHERE doc_id=?", (doc_id,)).fetchall()
    assert len(chunks) > 0  # RAG 索引成功
    wiki = store.conn.execute("SELECT * FROM wiki_pages").fetchall()
    assert len(wiki) == 0  # Wiki 失败, 未产生页面


def test_customer_analyze_endpoint(tmp_data, monkeypatch):
    """6.4: /customers/{id}/analyze 生成客户分析并展示。"""
    from app.web import routes
    from app.storage.sqlite_store import SqliteStore

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            return "兴趣:LED; 活跃:高; 建议:报价"

    monkeypatch.setattr(routes, "CloudLLM", FakeLLM)
    store = SqliteStore()
    store.conn.execute(
        "INSERT INTO customers VALUES(?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0))
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    from app.storage.interfaces import Message
    store.upsert_message(Message("m1", "a1", "c1", False, None, 1, "chat", "want LED", True, 0))
    client = TestClient(create_app())
    r = client.post("/customers/cust1/analyze")
    assert r.status_code == 200
    assert "LED" in r.text


def test_customer_refresh_profile_endpoint(tmp_data, monkeypatch):
    """6.2: /customers/{id}/refresh-profile 手动重抽画像。"""
    from app.web import routes
    from app.storage.sqlite_store import SqliteStore

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            return '{"country": "USA"}'

    monkeypatch.setattr(routes, "CloudLLM", FakeLLM)
    store = SqliteStore()
    store.conn.execute(
        "INSERT INTO customers VALUES(?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0))
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    from app.storage.interfaces import Message
    store.upsert_message(Message("m1", "a1", "c1", False, None, 1, "chat", "client from USA", True, 0))
    client = TestClient(create_app())
    r = client.post("/customers/cust1/refresh-profile")
    assert r.status_code == 200
    assert "USA" in r.text

