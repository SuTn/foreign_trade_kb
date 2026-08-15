from fastapi.testclient import TestClient
from app.web.app import create_app
from tests.conftest import reply_task_id, wait_reply_done


def test_reply_post_returns_polling_fragment_and_pending_task(tmp_data):
    """2.3/2.4 提交侧: POST 插任务返回轮询片段, 任务初始 pending, status 返回处理中。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())  # 无 with → worker 不启动, 只验证提交侧
    r = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "hi"})
    assert r.status_code == 200
    assert "正在生成回复" in r.text
    assert "every 1s" in r.text
    tid = reply_task_id(r.text)
    row = SqliteStore().conn.execute("SELECT * FROM reply_tasks WHERE id=?", (tid,)).fetchone()
    assert row["status"] == "pending"
    assert row["mode"] == "generate"
    r2 = client.get(f"/api/reply/status/{tid}")
    assert r2.status_code == 200
    assert "正在生成回复" in r2.text


def test_stale_tasks_marked_failed_on_startup(tmp_data):
    """D7: 启动时遗留 pending/running 任务置 failed。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    store.conn.execute("UPDATE reply_tasks SET status='running'")
    store.conn.commit()
    with TestClient(create_app()) as client:
        rows = SqliteStore().conn.execute("SELECT status FROM reply_tasks").fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert client.app.state.llm is not None  # D3 单例已建
        assert client.app.state.reply_worker is not None


def test_reply_full_lifecycle(tmp_data, monkeypatch):
    """2.6: 提交→轮询→done, 结果渲染复制按钮, session_id 透传。"""
    from app.web import routes

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            return "建议回复: 异步"

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
        tid = reply_task_id(r.text)
        done = wait_reply_done(client, tid)
        assert "建议回复: 异步" in done.text
        assert "data-copy" in done.text


def test_reply_polling_template_has_every_1s(tmp_data):
    """2.5: 轮询片段含 every 1s 触发与 outerHTML 交换。"""
    from fastapi.templating import Jinja2Templates
    from pathlib import Path
    from app.web.app import create_app
    client = TestClient(create_app())
    t = Jinja2Templates(directory=str(Path("app/web/templates")))
    html = t.TemplateResponse({}, "reply_polling.html", {"task_id": "abc123"}).body.decode()
    assert "/api/reply/status/abc123" in html
    assert "every 1s" in html
    assert 'hx-swap="outerHTML"' in html


def test_reply_result_has_copy_button_and_session(tmp_data, monkeypatch):
    """4.1/4.3: done 结果含 data-copy 按钮与 session_id 透传。"""
    from app.web import routes
    from app.web.app import create_app

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            return "可复制的回复"

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
        done = wait_reply_done(client, reply_task_id(r.text))
        assert "data-copy" in done.text
        # regenerate 按钮透传 session_id (uuid hex 形式, 隐藏域 value 属性)
        import re
        assert re.search(r'name="session_id" value="[0-9a-f]+"', done.text)


def test_chat_page_passes_session_id(tmp_data):
    """3.5: 聊天页渲染 session_id, 生成回复按钮透传。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    from app.storage.interfaces import Message
    store.upsert_message(Message("m1", "a1", "c1", False, "x@w", 1, "chat", "hello", True, 0))
    client = TestClient(create_app())
    html = client.get("/workspace/customer/cust1/chat").text
    sid = store.find_or_create_reply_session("cust1", "c1")
    assert sid in html  # 页面携带 session_id
    assert "session_id" in html  # 回复面板透传 session_id


def test_reply_post_persists_generation_params(tmp_data):
    """multilingual-copy: POST /api/reply 解析 language/scenario/formality 并持久化。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "hi",
                                        "language": "en", "scenario": "inquiry", "formality": "formal"})
    assert r.status_code == 200
    tid = reply_task_id(r.text)
    row = SqliteStore().conn.execute("SELECT * FROM reply_tasks WHERE id=?", (tid,)).fetchone()
    assert row["language"] == "en"
    assert row["scenario"] == "inquiry"
    assert row["formality"] == "formal"


def test_chat_page_has_generation_dimension_selects(tmp_data):
    """multilingual-copy: 聊天页含语种/场景/语气选择器。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    from app.storage.interfaces import Message
    store.upsert_message(Message("m1", "a1", "c1", False, "x@w", 1, "chat", "hello", True, 0))
    client = TestClient(create_app())
    html = client.get("/workspace/customer/cust1/chat").text
    assert 'name="language"' in html
    assert 'name="scenario"' in html
    assert 'name="formality"' in html
    assert "Русский" in html


def test_reply_result_shows_generation_dimensions(tmp_data, monkeypatch):
    """multilingual-copy: 结果卡片展示语种/场景标签。"""
    from app.web import routes
    from app.web.app import create_app

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            return "Официальный ответ"  # 俄语正式回复

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
        r = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "hi",
                                            "language": "ru", "scenario": "payment", "formality": "formal"})
        done = wait_reply_done(client, reply_task_id(r.text))
        assert "Официальный ответ" in done.text
        assert "俄语" in done.text
        assert "付款" in done.text
        # 重生成按钮隐藏域回传原始码值 (F2 改为 hx-include 隐藏域, 规避 JSON 转义问题)
        assert 'name="language" value="ru"' in done.text
        assert 'name="scenario" value="payment"' in done.text
        assert 'name="formality" value="formal"' in done.text


def test_reply_regenerate_persists_generation_params(tmp_data):
    """multilingual-reply-generation: POST /api/reply/regenerate 解析语言/场景/语气并持久化。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    sid = store.find_or_create_reply_session("cust1", "c1")
    client = TestClient(create_app())
    r = client.post("/api/reply/regenerate",
                    data={"customer_id": "cust1", "chat_id": "c1", "message": "hi",
                          "style": "default", "session_id": sid,
                          "language": "ru", "scenario": "payment", "formality": "formal"})
    assert r.status_code == 200
    tid = reply_task_id(r.text)
    row = SqliteStore().conn.execute("SELECT * FROM reply_tasks WHERE id=?", (tid,)).fetchone()
    assert row["mode"] == "regenerate"
    assert row["session_id"] == sid
    assert row["language"] == "ru"
    assert row["scenario"] == "payment"
    assert row["formality"] == "formal"


def test_legacy_result_renders_without_dimension_tags(tmp_data):
    """multilingual-reply-generation: 旧结果 (无 language/scenario/formality) 正常渲染且 regenerate 提交空维度。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    store.update_reply_task(tid, status="done",
                            result='{"reply": "旧回复", "sources": [], "style": "default"}')
    client = TestClient(create_app())
    done = client.get(f"/api/reply/status/{tid}")
    assert done.status_code == 200
    assert "旧回复" in done.text
    assert "语种:" not in done.text
    assert "场景:" not in done.text
    assert "正式语气" not in done.text
    assert 'name="language" value=""' in done.text
    assert 'name="scenario" value=""' in done.text
    assert 'name="formality" value=""' in done.text
