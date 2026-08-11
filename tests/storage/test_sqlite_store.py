import time
import sqlite3
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Chat, Message, WikiPage


def test_old_schema_gets_avatar_path_column(tmp_data):
    """旧 schema 库 (无 avatar_path) 打开后自动迁移出该列, 且幂等。"""
    store = SqliteStore()  # 新库已含列
    store.conn.close()
    # 模拟旧库: 重新建一个不含 avatar_path 的库
    p = tmp_data / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE customers(id TEXT PRIMARY KEY, display_name TEXT, phone TEXT, company TEXT, country TEXT, created_at INTEGER)")
    c.commit(); c.close()
    for _ in range(2):  # 同一旧库重复打开 → 列仍存在 (迁移幂等)
        store2 = SqliteStore(p)
        cols = [r[1] for r in store2.conn.execute("PRAGMA table_info(customers)").fetchall()]
        assert "avatar_path" in cols
        store2.conn.close()

def test_old_schema_gets_sender_name_column(tmp_data):
    """旧库 (messages 无 sender_name) 打开后自动迁移出该列, 且幂等。"""
    p = tmp_data / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE messages(id TEXT, account_id TEXT, chat_id TEXT, from_me INTEGER, "
              "sender_jid TEXT, ts INTEGER, type TEXT, body TEXT, body_present INTEGER, "
              "ingested_at INTEGER, PRIMARY KEY(id, account_id))")
    c.commit(); c.close()
    for _ in range(2):  # 同一旧库重复打开 → 迁移幂等
        store2 = SqliteStore(p)
        cols = [r[1] for r in store2.conn.execute("PRAGMA table_info(messages)").fetchall()]
        assert "sender_name" in cols
        store2.conn.close()

def test_upsert_message_roundtrips_sender_name(tmp_data):
    s = SqliteStore()
    m = Message("m1", "a1", "c1", False, "x@w", 1000, "chat", "hello", True,
                int(time.time()), "Sonya")
    s.upsert_message(m)
    rows = s.list_messages("c1")
    assert rows[0].sender_name == "Sonya"


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
