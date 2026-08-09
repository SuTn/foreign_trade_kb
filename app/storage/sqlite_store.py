# app/storage/sqlite_store.py
import sqlite3, time, json
from pathlib import Path
from app.storage.interfaces import (StructuredStore, Chat, Message, ProfileField, WikiPage)
from app.config import settings

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

class SqliteStore(StructuredStore):
    def __init__(self, path: Path | None = None):
        self.path = path or settings.sqlite_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=5.0)  # busy_timeout via timeout
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    def upsert_chat(self, chat: Chat):
        self.conn.execute(
            "INSERT INTO chats VALUES(?,?,?,?,?,?) ON CONFLICT(id,account_id) DO UPDATE SET "
            "display_name=excluded.display_name, kind=excluded.kind, last_synced_at=excluded.last_synced_at",
            (chat.id, chat.account_id, chat.jid, chat.display_name, chat.kind, chat.last_synced_at))
        self.conn.commit()

    def upsert_message(self, msg: Message):
        self.conn.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id,account_id) DO UPDATE SET "
            "from_me=excluded.from_me, sender_jid=excluded.sender_jid, ts=excluded.ts, type=excluded.type, "
            "body=COALESCE(excluded.body, body), body_present=excluded.body_present",
            (msg.id, msg.account_id, msg.chat_id, int(msg.from_me), msg.sender_jid,
             msg.ts, msg.type, msg.body, int(msg.body_present), msg.ingested_at))
        if msg.body:
            self.conn.execute("INSERT OR REPLACE INTO messages_fts(rowid, body) VALUES((SELECT rowid FROM messages WHERE id=? AND account_id=?), ?)",
                              (msg.id, msg.account_id, msg.body))
        self.conn.commit()

    def upsert_profile_field(self, customer_id, field, value, source):
        now = int(time.time())
        # 单行覆盖语义: source=manual 不被 auto 覆盖
        if source == "auto":
            row = self.conn.execute("SELECT source FROM profiles WHERE customer_id=? AND field=?",
                                    (customer_id, field)).fetchone()
            if row and row["source"] == "manual":
                return  # 跳过, 不覆盖人工值
        self.conn.execute(
            "INSERT INTO profiles VALUES(?,?,?,?,?) ON CONFLICT(customer_id,field) DO UPDATE SET "
            "value=excluded.value, source=excluded.source, updated_at=excluded.updated_at",
            (customer_id, field, value, source, now))
        self.conn.commit()

    def get_profile(self, customer_id):
        rows = self.conn.execute("SELECT * FROM profiles WHERE customer_id=?", (customer_id,)).fetchall()
        return [ProfileField(r["customer_id"], r["field"], r["value"], r["source"], r["updated_at"]) for r in rows]

    def list_messages(self, chat_id, limit=50, before_ts=None):
        if before_ts:
            rows = self.conn.execute("SELECT * FROM messages WHERE chat_id=? AND ts<? ORDER BY ts DESC LIMIT ?",
                                     (chat_id, before_ts, limit)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY ts DESC LIMIT ?",
                                     (chat_id, limit)).fetchall()
        return [self._row_to_msg(r) for r in rows]

    def search_fts(self, table, query, limit=20):
        fts = f"{table}_fts"
        col = "body" if table == "messages" else "text"
        # FTS5 把 ? * ( ) 等当操作符会抛 syntax error; 将每个空白分隔 token 用双引号包成 phrase,
        # 再 OR 连接, 既保留多词召回 (任一命中) 又避免特殊字符崩溃。空查询直接返回 []。
        if not query or not query.strip():
            return []
        toks = [t for t in query.replace('"', '""').split() if t]
        if not toks:
            return []
        expr = " OR ".join(f'"{t}"' for t in toks)
        rows = self.conn.execute(f"SELECT * FROM {fts} WHERE {col} MATCH ? LIMIT ?", (expr, limit)).fetchall()
        return [dict(r) for r in rows]

    def upsert_wiki_page(self, page: WikiPage):
        self.conn.execute(
            "INSERT INTO wiki_pages VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET "
            "title=excluded.title, body_md=excluded.body_md, frontmatter=excluded.frontmatter, "
            "source_doc_ids=excluded.source_doc_ids, entity_type=excluded.entity_type, updated_at=excluded.updated_at",
            (page.id, page.title, page.slug, page.body_md, json.dumps(page.frontmatter),
             json.dumps(page.source_doc_ids), page.entity_type, page.updated_at))
        self.conn.commit()

    def get_wiki_page(self, slug):
        r = self.conn.execute("SELECT * FROM wiki_pages WHERE slug=?", (slug,)).fetchone()
        if not r: return None
        return WikiPage(r["id"], r["title"], r["slug"], r["body_md"], json.loads(r["frontmatter"]),
                        json.loads(r["source_doc_ids"]), r["entity_type"], r["updated_at"])

    def list_documents(self):
        """返回全部文档及其 chunk/wiki 状态。"""
        rows = self.conn.execute("SELECT * FROM documents ORDER BY ingested_at DESC").fetchall()
        out = []
        for r in rows:
            chunk_count = self.conn.execute(
                "SELECT COUNT(*) FROM doc_chunks WHERE doc_id=?", (r["id"],)).fetchone()[0]
            wiki_count = self.conn.execute(
                "SELECT COUNT(*) FROM wiki_pages WHERE source_doc_ids LIKE ?",
                (f'%"{r["id"]}"%',)).fetchone()[0]
            d = dict(r)
            d["chunk_count"] = chunk_count
            d["wiki_count"] = wiki_count
            out.append(d)
        return out

    def delete_document(self, doc_id):
        """删除文档: 清空 documents/doc_chunks, 重建 doc_chunks_fts 索引,
        并从 wiki_pages.source_doc_ids 移除该 doc。
        返回是否删除了文档记录。"""
        self.conn.execute("DELETE FROM doc_chunks WHERE doc_id=?", (doc_id,))
        # FTS 外部内容表: 先删内容, 再 rebuild 索引 (直接 DELETE FTS 行在无索引条目时报 malformed)
        self.conn.execute("INSERT INTO doc_chunks_fts(doc_chunks_fts) VALUES('rebuild')")
        # 从 wiki 页面来源中移除该 doc
        for r in self.conn.execute("SELECT id, source_doc_ids FROM wiki_pages").fetchall():
            docs = json.loads(r["source_doc_ids"])
            if doc_id in docs:
                docs.remove(doc_id)
                if docs:
                    self.conn.execute("UPDATE wiki_pages SET source_doc_ids=? WHERE id=?",
                                      (json.dumps(docs), r["id"]))
                else:
                    self.conn.execute("DELETE FROM wiki_pages WHERE id=?", (r["id"],))
        cur = self.conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def _row_to_msg(self, r):
        return Message(r["id"], r["account_id"], r["chat_id"], bool(r["from_me"]), r["sender_jid"],
                       r["ts"], r["type"], r["body"], bool(r["body_present"]), r["ingested_at"])
