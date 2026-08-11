from fastapi.testclient import TestClient
from app.web.app import create_app


def test_stats_endpoint(tmp_data):
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.executemany(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        [("c1","A","1",None,None,0,None),("c2","B","2",None,None,0,None)])
    store.conn.executemany(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [("m1","me","ch1",0,"x",1000,"chat","hi",1,0,None),
         ("m2","me","ch1",0,"x",2000,"chat","yo",1,0,None),
         ("m3","me","ch2",0,"y",1500,"chat","a",1,0,None)])
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",("ch1","me","ch1","Alice","single",0))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",("me","ch1","c1",0.9,0,0))
    store.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",("d1","a.pdf","pdf","docreader","done",0))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.get("/api/stats")
    assert r.status_code == 200
    j = r.json()
    assert j["customers"]["total"] == 2
    assert j["customers"]["with_profile"] == 0
    assert j["customers"]["linked_chats"] == 1
    assert j["knowledge"]["documents"] == 1
    assert j["knowledge"]["chunks"] == 0
    assert j["knowledge"]["wiki_pages"] == 0
    assert j["collector"]["alive"] is False
    assert j["collector"]["status"] == {}
    assert j["recent_chats"][0]["chat_id"] == "ch1"   # last_ts=2000 最新
    assert j["recent_chats"][0]["display_name"] == "Alice"


def test_customers_page():
    client = TestClient(create_app())
    assert client.get("/customers").status_code == 200


def test_customers_page_has_search_data(tmp_data):
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",("c1","Alice","10086","ACME","USA",0,"/avatars/c1.png"))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",("c1","country","USA","auto",0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/customers").text
    assert "data-search" in html and "ACME" in html
    assert 'src="/avatars/c1.png"' in html


def test_customers_filter_dropdowns_from_profiles(tmp_data):
    """筛选下拉取值来源 profiles 表 (customers.country/company 列为空时仍可选)。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "10086", None, None, 0, None))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)", ("c1", "country", "USA", "auto", 0))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)", ("c1", "company", "ACME", "auto", 0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/customers").text
    assert '<option value="USA">' in html
    assert '<option value="ACME">' in html


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
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0, None))
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
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0, None))
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


def test_profile_manual_edit_saved(tmp_data):
    """web-app: 画像页编辑某字段并保存 → 持久化并标记为 manual。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/customers/cust1/profile",
                    data={"field": "company", "value": "Acme Trading"})
    assert r.status_code == 200
    assert "Acme Trading" in r.text
    p = store.get_profile("cust1")
    assert any(f.field == "company" and f.value == "Acme Trading" and f.source == "manual"
               for f in p)


def test_chat_messages_pagination(tmp_data):
    """web-app: 聊天浏览页分页展示历史消息。"""
    from app.storage.sqlite_store import SqliteStore
    from app.storage.interfaces import Message
    store = SqliteStore()
    store.conn.execute(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    for i, ts in enumerate([1, 2, 3]):
        store.upsert_message(Message(f"m{i}", "a1", "c1", False, None, ts, "chat",
                                     f"msg {ts}", True, 0))
    client = TestClient(create_app())
    r = client.get("/customers/cust1/chat/c1")
    assert r.status_code == 200
    assert "msg 1" in r.text and "msg 3" in r.text
    # 分页: 请求 before_ts=2 应只含更早消息
    r2 = client.get("/customers/cust1/chat/c1?before_ts=2&partial=1")
    assert r2.status_code == 200
    assert "msg 1" in r2.text
    assert "msg 2" not in r2.text
    assert "msg 3" not in r2.text


def test_knowledge_list_and_delete(tmp_data):
    """knowledge-base: 文档列表 + 删除文档。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                       ("d1", "a.md", "md", "docreader", "done", 1))
    store.conn.execute("INSERT INTO doc_chunks VALUES(?,?,?,?,?,?)",
                       ("c1", "d1", 0, "text-a", "0", "c1"))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.get("/knowledge")
    assert r.status_code == 200
    assert "a.md" in r.text
    r2 = client.delete("/api/knowledge/d1")
    assert r2.status_code == 200
    assert r2.json()["deleted"] is True
    assert store.conn.execute("SELECT COUNT(*) FROM documents WHERE id='d1'").fetchone()[0] == 0


def test_knowledge_search_returns_results(tmp_data, monkeypatch):
    """knowledge-base: 检索测试返回来源片段。"""
    from app.web import routes
    from app.storage.sqlite_store import SqliteStore
    from app.storage.chroma_store import ChromaStore

    class FakeEmbed:
        def embed(self, text):
            return [float(len(text) % 7)] * 8

    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    store = SqliteStore()
    store.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                       ("d1", "a.md", "md", "docreader", "done", 1))
    store.conn.execute("INSERT INTO doc_chunks VALUES(?,?,?,?,?,?)",
                       ("c1", "d1", 0, "LED 产品规格说明", "0", "c1"))
    store.conn.execute("INSERT INTO doc_chunks_fts(rowid, text) VALUES((SELECT rowid FROM doc_chunks WHERE id='c1'), ?)",
                       ("LED 产品规格说明",))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/knowledge/search", data={"message": "LED"})
    assert r.status_code == 200
    assert "LED" in r.text


def test_reply_accepts_form_and_regenerate(tmp_data, monkeypatch):
    """reply-assist: /api/reply 支持表单, /api/reply/regenerate 返回不同风格候选。"""
    from app.web import routes

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            return "建议回复内容"

    class FakeReranker:
        def rerank(self, q, cands, top_k=8):
            return cands[:top_k]

    monkeypatch.setattr(routes, "CloudLLM", FakeLLM)
    monkeypatch.setattr(routes, "get_reranker", lambda: FakeReranker())

    class FakeEmbed:
        def embed(self, text):
            return [1.0] * 8

    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "hi"})
    assert r.status_code == 200
    assert "建议回复内容" in r.text
    r2 = client.post("/api/reply/regenerate",
                     data={"customer_id": "cust1", "chat_id": "c1", "message": "hi", "style": "default"})
    assert r2.status_code == 200
    assert "concise" in r2.text  # regenerate 切到下一风格


def test_home_shows_stats(tmp_data):
    """首页仪表盘渲染统计卡 + 近期活跃会话。"""
    from fastapi.testclient import TestClient
    from app.web.app import create_app
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",("c1","Alice","1",None,None,0,None))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",("m1","me","ch1",0,"x",1000,"chat","hi",1,0,None))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",("ch1","me","ch1","Alice","single",0))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",("me","ch1","c1",0.9,0,0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/").text
    assert "客户总数" in html and "近期活跃会话" in html and "Alice" in html

