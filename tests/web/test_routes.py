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
    assert client.get("/workspace").status_code == 200


def test_customers_page_has_search_data(tmp_data):
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",("c1","Alice","10086","ACME","USA",0,"/avatars/c1.png"))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",("c1","country","USA","auto",0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/workspace").text
    assert "data-search" in html and "ACME" in html
    assert 'src="/avatars/c1.png"' in html


def test_customers_filter_dropdowns_from_profiles(tmp_data):
    """工作台左栏等级筛选下拉存在 (intent_level 来源 profiles)。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "10086", None, None, 0, None))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)", ("c1", "intent_level", "A", "auto", 0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/workspace").text
    assert 'id="ws-tier"' in html
    assert '<option value="A">' in html


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


def test_upload_bad_file_marks_failed(tmp_data):
    from fastapi.testclient import TestClient
    from app.web.app import create_app

    with TestClient(create_app()) as client:
        r = client.post("/api/knowledge/upload",
                        files={"file": ("bad.bin", b"\x00\x01\x02", "application/octet-stream")},
                        data={"filename": "bad.bin"})
        assert r.status_code != 500
        # 无残留 processing 行（failed 或不存在）
        store = client.app.state.sqlite_store
        rows = store.conn.execute("SELECT status FROM documents").fetchall()
        assert all(row["status"] != "processing" for row in rows)


def test_upload_empty_text_marks_done(tmp_data):
    from fastapi.testclient import TestClient
    from app.web.app import create_app

    with TestClient(create_app()) as client:
        r = client.post("/api/knowledge/upload",
                        files={"file": ("empty.txt", b"", "text/plain")},
                        data={"filename": "empty.txt"})
        assert r.status_code in (200, 201, 422)
        store = client.app.state.sqlite_store
        rows = store.conn.execute("SELECT status FROM documents").fetchall()
        assert rows and all(row["status"] == "done" for row in rows)


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


def test_customer_summarize_endpoint(tmp_data, monkeypatch):
    """customer-summary: /customers/{id}/summarize 创建任务并返回轮询片段。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())  # 无 with → worker 不启动, 只验证提交侧
    r = client.post("/customers/cust1/summarize")
    assert r.status_code == 200
    assert "正在生成摘要" in r.text
    assert "every 1s" in r.text
    import re
    m = re.search(r"/api/summary/status/([0-9a-f]+)", r.text)
    assert m, "未找到 task_id"
    tid = m.group(1)
    row = SqliteStore().conn.execute("SELECT * FROM summary_tasks WHERE id=?", (tid,)).fetchone()
    assert row["status"] == "pending"
    assert row["customer_id"] == "cust1"
    r2 = client.get(f"/api/summary/status/{tid}")
    assert r2.status_code == 200
    assert "正在生成摘要" in r2.text


def test_customer_summarize_full_lifecycle(tmp_data, monkeypatch):
    """customer-summary: 提交→worker 生成→轮询 done, 展示结构化摘要。"""
    from app.web import routes
    from app.storage.sqlite_store import SqliteStore

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            return '{"overview": "客户想买 LED-100", "intent_vehicle": "LED-100", ' \
                   '"budget_range": "3万", "target_country": "俄罗斯", ' \
                   '"concerns": "物流", "follow_up": "确认库存"}'

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
    store.upsert_message(Message("m1", "a1", "c1", False, None, 1, "chat", "want LED-100", True, 0))
    with TestClient(create_app()) as client:
        r = client.post("/customers/cust1/summarize")
        assert r.status_code == 200
        import re
        m = re.search(r"/api/summary/status/([0-9a-f]+)", r.text)
        assert m, "未找到 task_id"
        tid = m.group(1)
        # 轮询直到 done
        import time as _time
        deadline = _time.time() + 8
        done = None
        while _time.time() < deadline:
            done = client.get(f"/api/summary/status/{tid}")
            if "正在生成摘要" not in done.text:
                break
            _time.sleep(0.2)
        assert done is not None and "正在生成摘要" not in done.text
        assert "LED-100" in done.text
        assert "意向车型" in done.text
        assert "俄罗斯" in done.text
        # 摘要已写入 customer_summaries
        s = store.get_customer_summary("cust1")
        assert s["intent_vehicle"] == "LED-100"


def test_customer_detail_shows_existing_summary(tmp_data):
    """customer-summary: 工作台右栏展示已生成的摘要。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute(
        "INSERT INTO customer_summaries VALUES(?,?,?,?,?,?,?,?,?)",
        ("cust1", "客户想买 LED-100", "LED-100", "3万", "俄罗斯", "物流", "确认库存", 0, 100))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/workspace/customer/cust1/side").text
    assert "对话摘要" in html
    assert "LED-100" in html
    assert "意向车型" in html


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
    """workspace-load-earlier: 工作台聊天加载更早消息。"""
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
    r = client.get("/workspace/customer/cust1/chat")
    assert r.status_code == 200
    assert "msg 1" in r.text and "msg 3" in r.text
    # 加载更早: 请求 before_ts=2 应只含更早消息
    r2 = client.get("/workspace/customer/cust1/chat/earlier?before_ts=2&chat_id=c1")
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
    """reply-assist: /api/reply 提交任务, 轮询 done 后 /api/reply/regenerate 返回不同风格候选。"""
    from app.web import routes
    from tests.conftest import reply_task_id, wait_reply_done

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
    with TestClient(create_app()) as client:
        r = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "hi"})
        assert r.status_code == 200
        assert "正在生成回复" in r.text
        done = wait_reply_done(client, reply_task_id(r.text))
        assert "建议回复内容" in done.text
        r2 = client.post("/api/reply/regenerate",
                         data={"customer_id": "cust1", "chat_id": "c1", "message": "hi", "style": "default"})
        assert r2.status_code == 200
        done2 = wait_reply_done(client, reply_task_id(r2.text))
        assert "concise" in done2.text  # regenerate 切到下一风格


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
    # first-run-onboarding: 首页含快速开始引导
    assert "快速开始" in html and "onboard-card" in html


def test_chat_page_group_renders_sender_name(tmp_data):
    """群聊聊天页在 meta 区渲染发送者名。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)", ("cust1","Alice","10086",None,None,0,None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)", ("a1","g1","cust1",0.9,0,0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)", ("g1","a1","g1","海外采购群","group",0))
    store.conn.commit()
    from app.storage.interfaces import Message
    store.upsert_message(Message("m1", "a1", "g1", False, "8615976909619@c.us", 1,
                                 "chat", "hello", True, 0, "Sonya"))
    client = TestClient(create_app())
    html = client.get("/workspace/customer/cust1/chat").text
    assert "Sonya ·" in html


def test_reply_llm_failure_degrades_with_error(tmp_data, monkeypatch):
    """4.1: /api/reply LLM 失败 → 任务 failed, 轮询返回可读错误 (200), 不抛 500。"""
    from app.web import routes
    from tests.conftest import reply_task_id, wait_reply_done

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            raise RuntimeError("LLM 不可用")

    class FakeRerank:
        def rerank(self, q, c, top_k=8):
            return c[:top_k]

    class FakeEmbed:
        def embed(self, text):
            return [1.0] * 8

    monkeypatch.setattr(routes, "CloudLLM", FakeLLM)
    monkeypatch.setattr(routes, "get_reranker", lambda: FakeRerank())
    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    with TestClient(create_app()) as client:
        r = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "hi"})
        assert r.status_code == 200
        done = wait_reply_done(client, reply_task_id(r.text))
        assert "LLM 不可用" in done.text


def test_regenerate_failure_degrades_with_error(tmp_data, monkeypatch):
    """4.1: /api/reply/regenerate 失败同样返回降级 (200), 不抛 500。"""
    from app.web import routes
    from tests.conftest import reply_task_id, wait_reply_done

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            raise RuntimeError("LLM 超时")

    class FakeRerank:
        def rerank(self, q, c, top_k=8):
            return c[:top_k]

    class FakeEmbed:
        def embed(self, text):
            return [1.0] * 8

    monkeypatch.setattr(routes, "CloudLLM", FakeLLM)
    monkeypatch.setattr(routes, "get_reranker", lambda: FakeRerank())
    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    with TestClient(create_app()) as client:
        r = client.post("/api/reply/regenerate",
                        data={"customer_id": "cust1", "chat_id": "c1", "message": "hi"})
        assert r.status_code == 200
        done = wait_reply_done(client, reply_task_id(r.text))
        assert "LLM 超时" in done.text


def test_search_embedding_failure_degrades_to_bm25(tmp_data, monkeypatch):
    """4.2: 嵌入失败降级为 BM25-only + '向量检索不可用' 标记。"""
    from app.web import routes
    from app.storage.sqlite_store import SqliteStore

    class BoomEmbed:
        def embed(self, text):
            raise RuntimeError("embedding 模型不可用")

    monkeypatch.setattr(routes, "get_embedding", lambda: BoomEmbed())
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
    assert "向量检索不可用" in r.text
    assert "LED 产品规格说明" in r.text  # BM25 结果仍返回


def test_reply_degrades_when_embedding_warmup_times_out(tmp_data, monkeypatch):
    """3.3: 模型预热未就绪超时 → 回复任务 failed (200), 不抛 500。"""
    import threading
    from app.web import routes
    from tests.conftest import reply_task_id, wait_reply_done

    class FakeRerank:
        def rerank(self, q, c, top_k=8):
            return c[:top_k]

    monkeypatch.setattr(routes, "get_reranker", lambda: FakeRerank())
    monkeypatch.setattr(routes, "WARMUP_TIMEOUT_SEC", 0.0)
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    with TestClient(create_app()) as client:
        client.app.state.embedding_ready = threading.Event()  # 永不置位 → worker 超时置 failed
        r = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "hi"})
        assert r.status_code == 200
        done = wait_reply_done(client, reply_task_id(r.text))
        assert "预热超时" in done.text


def test_warmup_warms_embedding_and_reranker(monkeypatch):
    """3.3: 后台预热线程触发 embedding/reranker 加载并置位 ready。"""
    import threading
    from app.web import routes
    from app.web.app import _warmup_models

    calls = []

    class FakeEmbed:
        def embed(self, text):
            calls.append("embed")
            return [1.0] * 8

    class FakeRerank:
        def rerank(self, q, c, top_k=8):
            calls.append("rerank")
            return c[:top_k]

    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    monkeypatch.setattr(routes, "get_reranker", lambda: FakeRerank())
    monkeypatch.setattr("app.web.app._warmup_enabled", lambda: True)
    app = create_app()
    app.state.embedding_ready = threading.Event()
    _warmup_models(app)
    assert calls == ["embed", "rerank"]
    assert app.state.embedding_ready.is_set()
    assert app.state.embedding is not None


def test_lifespan_sets_embedding_ready(tmp_data, monkeypatch):
    """3.3: lifespan 启动创建 embedding_ready 事件 (测试环境预热被跳过但事件置位)。"""
    from app.web import routes

    class FakeEmbed:
        def embed(self, text):
            return [1.0] * 8

    class FakeRerank:
        def rerank(self, q, c, top_k=8):
            return c[:top_k]

    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    monkeypatch.setattr(routes, "get_reranker", lambda: FakeRerank())
    with TestClient(create_app()) as client:
        assert hasattr(client.app.state, "embedding_ready")
        assert client.app.state.embedding_ready.wait(5)


def test_chat_page_single_keeps_customer_label(tmp_data):
    """单聊聊天页保持 '客户' 标签。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)", ("cust1","Alice","10086",None,None,0,None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)", ("a1","c1","cust1",0.9,0,0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)", ("c1","a1","c1","Alice","single",0))
    store.conn.commit()
    from app.storage.interfaces import Message
    store.upsert_message(Message("m1", "a1", "c1", False, "x@w", 1,
                                 "chat", "hello", True, 0))
    client = TestClient(create_app())
    html = client.get("/workspace/customer/cust1/chat").text
    assert "客户 ·" in html


def test_reply_session_history_passed_on_second_generate(tmp_data, monkeypatch):
    """3.6: 同一 chat 二次生成时, 首次 user+assistant 出现在第二次 prompt。"""
    from app.web import routes
    from tests.conftest import reply_task_id, wait_reply_done

    prompts = []

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            prompts.append(s)
            return "第一轮回复"

    class FakeRerank:
        def rerank(self, q, c, top_k=8):
            return c[:top_k]

    class FakeEmbed:
        def embed(self, text):
            return [1.0] * 8

    monkeypatch.setattr(routes, "CloudLLM", FakeLLM)
    monkeypatch.setattr(routes, "get_reranker", lambda: FakeRerank())
    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    with TestClient(create_app()) as client:
        r1 = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "第一问"})
        wait_reply_done(client, reply_task_id(r1.text))
        r2 = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "第二问"})
        wait_reply_done(client, reply_task_id(r2.text))
    assert len(prompts) == 2
    assert "第一问" in prompts[1]     # 历史 user 进入上下文
    assert "第一轮回复" in prompts[1]  # 历史 assistant 进入上下文


def test_workspace_renders_customer_list(tmp_data):
    """workspace-layout: /workspace 渲染三栏骨架 + 左栏客户列表。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.get("/workspace")
    assert r.status_code == 200
    assert "ws-center" in r.text
    assert "ws-right" in r.text
    assert "Alice" in r.text
    assert "选择左侧客户" in r.text


def test_workspace_customers_fragment(tmp_data):
    """workspace-tiering: /workspace/customers 返回左栏客户列表片段 (供分层后刷新)。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.get("/workspace/customers")
    assert r.status_code == 200
    assert "ws-customer" in r.text
    assert "Alice" in r.text
    # 片段不应包含完整页面骨架 (左栏头部在 workspace.html, 不在片段)
    assert "ws-left-header" not in r.text


def test_workspace_chat_loads_messages(tmp_data):
    """workspace-layout: /workspace/customer/{id}/chat 加载中栏聊天。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("c1", "a1", "c1", "Alice", "single", 0))
    store.conn.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("m1", "a1", "c1", 0, None, 1000, "chat", "想买 LED-100", 1, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.get("/workspace/customer/cust1/chat")
    assert r.status_code == 200
    assert "ws-chat-messages" in r.text
    assert "想买 LED-100" in r.text
    assert "生成回复" in r.text


def test_workspace_side_loads_profile_and_summary(tmp_data):
    """workspace-layout: /workspace/customer/{id}/side 加载右栏画像+摘要。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute(
        "INSERT INTO customer_summaries VALUES(?,?,?,?,?,?,?,?,?)",
        ("cust1", "客户想买 LED-100", "LED-100", "3万", "俄罗斯", "物流", "确认库存", 0, 100))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.get("/workspace/customer/cust1/side")
    assert r.status_code == 200
    assert "对话摘要" in r.text
    assert "AI 建议" in r.text
    assert "画像" in r.text
    assert "LED-100" in r.text


def test_workspace_live_poll_returns_new_messages(tmp_data):
    """workspace-live-refresh: /chat/poll?after_ts= 增量拉取新消息。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("c1", "a1", "c1", "Alice", "single", 0))
    store.conn.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("m1", "a1", "c1", 0, None, 1000, "chat", "旧消息", 1, 0, None))
    store.conn.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("m2", "a1", "c1", 0, None, 2000, "chat", "新消息", 1, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    # after_ts=1000 → 只返回 ts>1000 的新消息
    r = client.get("/workspace/customer/cust1/chat/poll?after_ts=1000&chat_id=c1")
    assert r.status_code == 200
    assert "新消息" in r.text
    assert "旧消息" not in r.text
    # after_ts=2000 → 无新消息, 返回空
    r2 = client.get("/workspace/customer/cust1/chat/poll?after_ts=2000&chat_id=c1")
    assert r2.status_code == 200
    assert r2.text.strip() == ""


def test_workspace_orders_by_activity_and_unread(tmp_data):
    """workspace-live-refresh: /workspace 左栏按最近活跃降序 + 未读徽标。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust2", "Bob", "10087", None, None, 0, None))
    # cust1 有会话 + 新消息 (未读), cust2 无会话
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("c1", "a1", "c1", "Alice", "single", 0))
    store.conn.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("m1", "a1", "c1", 0, None, 2000, "chat", "客户新消息", 1, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.get("/workspace")
    assert r.status_code == 200
    # cust1 有未读徽标 (非我方消息且 ts>last_seen=0)
    assert "ws-unread-badge" in r.text
    # cust1 (有活跃) 排在 cust2 (无活跃) 之前
    assert r.text.index("Alice") < r.text.index("Bob")


def test_workspace_chat_sets_last_seen(tmp_data):
    """workspace-live-refresh: 打开聊天记录 last_seen, 未读清零。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("c1", "a1", "c1", "Alice", "single", 0))
    store.conn.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("m1", "a1", "c1", 0, None, 2000, "chat", "客户消息", 1, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    client.get("/workspace/customer/cust1/chat")
    # 打开后 last_seen 已更新, 未读应为 0
    act = store.get_customer_recent_activity("cust1")
    assert act["unread"] == 0
    assert act["last_ts"] == 2000


def test_workspace_has_tier_filter(tmp_data):
    """workspace-reply-profile: /workspace 左栏含意向等级筛选下拉。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.get("/workspace")
    assert r.status_code == 200
    assert 'id="ws-tier"' in r.text
    assert "全部等级" in r.text
    assert "未分层" in r.text


def test_customer_followup_endpoint(tmp_data, monkeypatch):
    """workspace-reply-profile: /customers/{id}/followup 生成结构化跟进建议。"""
    from app.web import routes
    from app.storage.sqlite_store import SqliteStore

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            return ('{"priority": "high", "next_action": "今天报价", '
                    '"suggested_message": "您好, 报价如下", "best_time": "今天下午", '
                    '"reason": "客户询价意向高"}')

    monkeypatch.setattr(routes, "CloudLLM", FakeLLM)
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    from app.storage.interfaces import Message
    store.upsert_message(Message("m1", "a1", "c1", False, None, 1, "chat", "want LED", True, 0))
    client = TestClient(create_app())
    r = client.post("/customers/cust1/followup")
    assert r.status_code == 200
    assert "跟进建议" in r.text
    assert "高优先级" in r.text
    assert "今天报价" in r.text
    assert "建议话术" in r.text


def test_followup_parse_fallback_on_bad_json():
    """workspace-reply-profile: LLM 输出非 JSON 时回退为文本展示。"""
    from app.profile.followup import _parse_followup
    d = _parse_followup("客户意向很高, 建议尽快跟进")
    assert d["priority"] == "medium"
    assert "客户意向很高" in d["reason"]


def test_followup_parse_json_with_surrounding_text():
    """workspace-reply-profile: LLM 输出带前后缀文字时仍能提取 JSON。"""
    from app.profile.followup import _parse_followup
    d = _parse_followup('好的, 以下是建议: {"priority": "high", "next_action": "报价", '
                        '"suggested_message": "您好", "best_time": "今天", "reason": "意向高"} 请查收')
    assert d["priority"] == "high"
    assert d["next_action"] == "报价"
    assert d["suggested_message"] == "您好"


def test_get_customers_recent_activity_batch(tmp_data):
    """workspace-live-refresh: 批量活跃查询返回各客户最近消息时间 + 未读数。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust2", "Bob", "10087", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("c1", "a1", "c1", "A", "single", 0))
    store.conn.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("m1", "a1", "c1", 0, None, 2000, "chat", "客户消息", 1, 0, None))
    store.conn.commit()
    act = store.get_customers_recent_activity(["cust1", "cust2"])
    assert act["cust1"]["last_ts"] == 2000
    assert act["cust1"]["unread"] == 1  # 非我方且 ts>last_seen=0
    assert act["cust2"]["last_ts"] == 0
    assert act["cust2"]["unread"] == 0

