import time
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Chat, Message, WikiPage

def test_upsert_message_idempotent(tmp_data):
    s = SqliteStore()
    m = Message("m1", "a1", "c1", False, "x@w", 1000, "chat", "hello", True, int(time.time()))
    s.upsert_message(m)
    s.upsert_message(m)  # 重复 upsert
    rows = s.list_messages("c1")
    assert len(rows) == 1

def test_profile_manual_not_overwritten(tmp_data):
    s = SqliteStore()
    s.upsert_profile_field("cust1", "country", "USA", "manual")
    s.upsert_profile_field("cust1", "country", "China", "auto")  # auto 不覆盖 manual
    p = s.get_profile("cust1")
    assert p[0].value == "USA"
    assert p[0].source == "manual"

def test_profile_auto_overwrites_auto(tmp_data):
    s = SqliteStore()
    s.upsert_profile_field("cust1", "country", "USA", "auto")
    s.upsert_profile_field("cust1", "country", "China", "auto")
    assert s.get_profile("cust1")[0].value == "China"

def test_fts_search(tmp_data):
    s = SqliteStore()
    s.upsert_message(Message("m1", "a1", "c1", False, "x", 1, "chat", "invoice for order 123", True, 1))
    res = s.search_fts("messages", "invoice", 10)
    assert len(res) == 1


def test_list_documents_counts(tmp_data):
    s = SqliteStore()
    s.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                   ("d1", "a.md", "md", "docreader", "done", 1))
    s.conn.execute("INSERT INTO doc_chunks VALUES(?,?,?,?,?,?)",
                   ("c1", "d1", 0, "text-a", "0", "c1"))
    s.conn.execute("INSERT INTO doc_chunks VALUES(?,?,?,?,?,?)",
                   ("c2", "d1", 1, "text-b", "1", "c2"))
    s.conn.execute("INSERT INTO wiki_pages VALUES(?,?,?,?,?,?,?,?)",
                   ("w1", "LED", "led", "body", '{"source_docs": ["d1"]}', '["d1"]', "product", 1))
    s.conn.commit()
    docs = s.list_documents()
    assert len(docs) == 1
    assert docs[0]["chunk_count"] == 2
    assert docs[0]["wiki_count"] == 1


def test_delete_document_removes_chunks_and_wiki_ref(tmp_data):
    s = SqliteStore()
    s.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                   ("d1", "a.md", "md", "docreader", "done", 1))
    s.conn.execute("INSERT INTO doc_chunks VALUES(?,?,?,?,?,?)",
                   ("c1", "d1", 0, "text-a", "0", "c1"))
    s.conn.execute("INSERT INTO wiki_pages VALUES(?,?,?,?,?,?,?,?)",
                   ("w1", "LED", "led", "body", '{"source_docs": ["d1"]}', '["d1"]', "product", 1))
    s.conn.execute("INSERT INTO wiki_pages VALUES(?,?,?,?,?,?,?,?)",
                   ("w2", "Lamp", "lamp", "body", '{"source_docs": ["d1", "d2"]}', '["d1", "d2"]', "product", 1))
    s.conn.commit()
    assert s.delete_document("d1") is True
    assert s.conn.execute("SELECT COUNT(*) FROM documents WHERE id='d1'").fetchone()[0] == 0
    assert s.conn.execute("SELECT COUNT(*) FROM doc_chunks WHERE doc_id='d1'").fetchone()[0] == 0
    # w1 唯一来源被删 → 页面删除; w2 保留但移除 d1
    assert s.conn.execute("SELECT COUNT(*) FROM wiki_pages WHERE id='w1'").fetchone()[0] == 0
    w2 = s.conn.execute("SELECT source_doc_ids FROM wiki_pages WHERE id='w2'").fetchone()[0]
    import json
    assert json.loads(w2) == ["d2"]
    # 再次删除不存在文档 → False
    assert s.delete_document("nope") is False
