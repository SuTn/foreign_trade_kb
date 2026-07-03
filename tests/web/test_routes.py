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

    monkeypatch.setattr(routes, "BgeEmbedding", FakeEmbed)
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

