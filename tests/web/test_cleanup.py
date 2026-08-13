import time
from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Message


def test_cleanup_by_chat_deletes_messages_and_vectors(tmp_data, monkeypatch):
    from app.web import routes
    from app.storage.chroma_store import ChromaStore

    class FakeEmbed:
        def embed(self, text):
            return [float(len(text) % 7)] * 8

    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    store = SqliteStore()
    store.upsert_message(Message("m1", "a1", "c1", False, "x", 1, "chat", "hi c1", True, 1))
    store.upsert_message(Message("m2", "a1", "c2", False, "y", 2, "chat", "hi c2", True, 2))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)", ("c1", "country", "USA", "auto", 0))
    store.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", ("d1", "a.md", "md", "docreader", "done", 1))
    store.conn.commit()
    vec = ChromaStore(embedding_fn=FakeEmbed().embed)
    vec.upsert_message_vector("v1", "hi c1", {"chat_id": "c1"})
    vec.upsert_message_vector("v2", "hi c2", {"chat_id": "c2"})
    client = TestClient(create_app())
    r = client.post("/api/cleanup", json={"mode": "chat", "chat_id": "c1"})
    assert r.status_code == 200
    assert r.json() == {"deleted_rows": 1, "affected_chats": ["c1"]}
    assert store.list_messages("c1") == []
    assert len(store.list_messages("c2")) == 1
    res = vec.query_messages("hi", top_k=10)
    assert [m["metadata"]["chat_id"] for m in res] == ["c2"]  # c1 向量已删
    assert len(store.get_profile("c1")) == 1   # 画像保留
    assert store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1  # 文档保留


def test_cleanup_by_days_deletes_old_messages(tmp_data, monkeypatch):
    from app.web import routes

    class FakeEmbed:
        def embed(self, text):
            return [1.0] * 8

    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    now = int(time.time())
    store = SqliteStore()
    store.upsert_message(Message("m1", "a1", "c1", False, "x", now - 3 * 86400, "chat", "old c1", True, 1))
    store.upsert_message(Message("m2", "a1", "c1", False, "x", now - 86400, "chat", "yesterday", True, 2))
    store.upsert_message(Message("m3", "a1", "c2", False, "y", now - 5 * 86400, "chat", "old c2", True, 3))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/cleanup", json={"mode": "days", "days": 2})
    assert r.status_code == 200
    j = r.json()
    assert j["deleted_rows"] == 2
    assert sorted(j["affected_chats"]) == ["c1", "c2"]
    assert [m.body for m in store.list_messages("c1")] == ["yesterday"]
    assert store.list_messages("c2") == []


def test_cleanup_validation_400(tmp_data):
    client = TestClient(create_app())
    assert client.post("/api/cleanup", json={"mode": "chat"}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "days"}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "days", "days": 0}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "days", "days": -3}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "days", "days": "abc"}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "weird"}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "days", "days": 2.5}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "days", "days": True}).status_code == 400


def test_cleanup_empty_json_body_400(tmp_data):
    """空/非法 JSON body 应降级为 400 而非 500 (清理不返回 500 约束)。"""
    client = TestClient(create_app())
    assert client.post("/api/cleanup", content=b"", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/cleanup", content=b"{not-json", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/cleanup", json=None).status_code == 400
    assert client.post("/api/cleanup", json=[1, 2]).status_code == 400
    assert client.post("/api/cleanup", json="abc").status_code == 400
    assert client.post("/api/cleanup", json={"mode": 123}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "chat", "chat_id": 123}).status_code == 400


def test_cleanup_degrades_when_vector_delete_fails(tmp_data, monkeypatch):
    from app.web import routes

    class BoomVS:
        def delete_message_vectors(self, chat_id):
            raise RuntimeError("chroma 故障")

    monkeypatch.setattr(routes, "_chroma_store", lambda request: BoomVS())
    store = SqliteStore()
    store.upsert_message(Message("m1", "a1", "c1", False, "x", 1, "chat", "hi", True, 1))
    client = TestClient(create_app())
    r = client.post("/api/cleanup", json={"mode": "chat", "chat_id": "c1"})
    assert r.status_code == 200
    j = r.json()
    assert j["deleted_rows"] == 1
    assert "向量" in j["error"]


def test_cleanup_page_renders(tmp_data):
    html = TestClient(create_app()).get("/cleanup").text
    assert 'hx-post="/api/cleanup"' in html
    assert "hx-confirm" in html
    assert '<a href="/cleanup">' in html
