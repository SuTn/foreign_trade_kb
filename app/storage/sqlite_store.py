# app/storage/sqlite_store.py
import sqlite3, time, json, uuid, sys
from pathlib import Path
from app.storage.interfaces import (StructuredStore, Chat, Message, ProfileField, WikiPage)
from app.config import settings

# schema.sql 资源路径: 打包后为 sys._MEIPASS, 开发为 __file__ 所在目录
if getattr(sys, "frozen", False):
    SCHEMA_PATH = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "schema.sql"
else:
    SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# 版本化迁移 (S1): 每个 (user_version, [sql]) 表示一次 schema 变更。
# schema.sql 是"当前完整 schema" (新库直接建全表); 旧库经此列表升级到当前版本。
# 每个 ALTER 幂等 (列已存在时忽略), 用 PRAGMA user_version 记录已应用到的版本。
MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, ["ALTER TABLE customers ADD COLUMN avatar_path TEXT"]),
    (2, ["ALTER TABLE messages ADD COLUMN sender_name TEXT"]),
    (3, ["ALTER TABLE backfill_requests ADD COLUMN attempts INTEGER DEFAULT 0"]),
    (4, ["ALTER TABLE reply_tasks ADD COLUMN language TEXT"]),
    (5, ["ALTER TABLE reply_tasks ADD COLUMN scenario TEXT"]),
    (6, ["ALTER TABLE reply_tasks ADD COLUMN formality TEXT"]),
    (7, ["ALTER TABLE customer_summaries ADD COLUMN last_ts INTEGER"]),
]

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
        # 1. 建全表 (幂等; IF NOT EXISTS) — 新库直接得到当前完整 schema
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()
        # 2. 版本化迁移: 从当前 user_version 应用到最新 (旧库补列)
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for version, statements in MIGRATIONS:
            if version <= current:
                continue
            for stmt in statements:
                try:
                    self.conn.execute(stmt)
                    self.conn.commit()
                except sqlite3.OperationalError:
                    pass  # 列已存在 (新库 schema.sql 已含) — 幂等
            # version 为 int, f-string 无注入风险
            self.conn.execute(f"PRAGMA user_version = {version}")
            self.conn.commit()

    def upsert_chat(self, chat: Chat):
        # 显示名/类型缺省 (如纯 DOM 增量) 时保留已有值, 仅刷新同步时间
        self.conn.execute(
            "INSERT INTO chats VALUES(?,?,?,?,?,?) ON CONFLICT(id,account_id) DO UPDATE SET "
            "display_name=COALESCE(excluded.display_name, chats.display_name), "
            "kind=COALESCE(excluded.kind, chats.kind), last_synced_at=excluded.last_synced_at",
            (chat.id, chat.account_id, chat.jid, chat.display_name, chat.kind, chat.last_synced_at))
        self.conn.commit()

    def upsert_message(self, msg: Message):
        # from_me 用 MAX 语义: 只升不降 (0→1 可, 1→0 不行)。
        # 原因: fast_tick 走 DOM tail 启发式, 连续多条自己发的消息只有最后一条带 tail-out,
        # 前面的会误判 from_me=0; slow_tick 用 IDB 的权威 fromMe 纠正为 1 后, 不应再被 DOM 覆盖回 0。
        self.conn.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id,account_id) DO UPDATE SET "
            "from_me=MAX(excluded.from_me, messages.from_me), "
            "sender_jid=excluded.sender_jid, ts=excluded.ts, type=excluded.type, "
            "body=COALESCE(excluded.body, body), body_present=excluded.body_present, "
            "sender_name=COALESCE(excluded.sender_name, sender_name)",
            (msg.id, msg.account_id, msg.chat_id, int(msg.from_me), msg.sender_jid,
             msg.ts, msg.type, msg.body, int(msg.body_present), msg.ingested_at, msg.sender_name))
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

    def sync_customer_column(self, customer_id, field, value):
        """把画像字段同步到 customers 固定列 (G: 消除 EAV 与固定列双轨)。
        目前 company/country 同时存在于 profiles EAV 与 customers 列, 抽取时同步到列,
        使 search_customers (查固定列) 能命中。仅覆盖非空值, 不覆盖已有非空列。"""
        if not value:
            return
        # 显式白名单映射, 避免 f-string 拼列名 (A4)
        if field == "company":
            self.conn.execute(
                "UPDATE customers SET company=COALESCE(NULLIF(company,''), ?) WHERE id=?",
                (value, customer_id))
        elif field == "country":
            self.conn.execute(
                "UPDATE customers SET country=COALESCE(NULLIF(country,''), ?) WHERE id=?",
                (value, customer_id))
        else:
            return
        self.conn.commit()

    def list_messages(self, chat_id, limit=50, before_ts=None):
        if before_ts:
            rows = self.conn.execute("SELECT * FROM messages WHERE chat_id=? AND ts<? ORDER BY ts DESC LIMIT ?",
                                     (chat_id, before_ts, limit)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY ts DESC LIMIT ?",
                                     (chat_id, limit)).fetchall()
        return [self._row_to_msg(r) for r in rows]

    def list_messages_after(self, chat_id, after_ts, limit=200):
        """取某会话 ts > after_ts 的消息 (时间正序), 供增量摘要取新消息。"""
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE chat_id=? AND ts>? ORDER BY ts ASC LIMIT ?",
            (chat_id, after_ts, limit)).fetchall()
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
        rows = self.conn.execute(f"SELECT rowid, * FROM {fts} WHERE {col} MATCH ? LIMIT ?", (expr, limit)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _escape_like(term: str) -> str:
        """转义 LIKE 通配符 %/_ (D1: 防用户输入误匹配)。"""
        return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def search_customers(self, query: str, limit: int = 20) -> list[dict]:
        """按 名称/电话/公司/国家 LIKE 检索客户 (D1)。空查询返回 []。"""
        q = query.strip()
        if not q:
            return []
        esc = f"%{self._escape_like(q)}%"
        rows = self.conn.execute(
            "SELECT id, display_name, phone, company, country FROM customers "
            "WHERE display_name LIKE ? ESCAPE '\\' OR phone LIKE ? ESCAPE '\\' "
            "OR company LIKE ? ESCAPE '\\' OR country LIKE ? ESCAPE '\\' "
            "ORDER BY created_at DESC LIMIT ?",
            (esc, esc, esc, esc, limit)).fetchall()
        return [dict(r) for r in rows]

    def search_profiles(self, query: str, limit: int = 20) -> list[dict]:
        """按 field/value LIKE 检索画像 (D1), 附带 customer_id。空查询返回 []。"""
        q = query.strip()
        if not q:
            return []
        esc = f"%{self._escape_like(q)}%"
        rows = self.conn.execute(
            "SELECT customer_id, field, value FROM profiles "
            "WHERE field LIKE ? ESCAPE '\\' OR value LIKE ? ESCAPE '\\' "
            "ORDER BY updated_at DESC LIMIT ?",
            (esc, esc, limit)).fetchall()
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
            try:
                docs = json.loads(r["source_doc_ids"])
            except (TypeError, ValueError):
                continue  # 脏 JSON 跳过, 不中断删除
            if not isinstance(docs, list):
                continue
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

    # ---- batch2-search-cleanup-monitor: 手动清理 (D2) ----
    def _rebuild_messages_fts(self):
        """messages 外部内容表: 删内容后重建 FTS 索引 (参照 delete_document 的 rebuild 模式)。"""
        self.conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")

    def delete_messages_by_chat(self, chat_id: str) -> dict:
        """删除某会话全部消息 + 重建 messages FTS。返回 {deleted_rows, affected_chats}。"""
        cur = self.conn.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        deleted = cur.rowcount
        self._rebuild_messages_fts()
        self.conn.commit()
        return {"deleted_rows": deleted, "affected_chats": [chat_id] if deleted else []}

    def delete_messages_before(self, cutoff_ts: int) -> dict:
        """删除 ts < cutoff_ts 的全部消息 + 重建 FTS。返回 {deleted_rows, affected_chats}。"""
        rows = self.conn.execute(
            "SELECT DISTINCT chat_id FROM messages WHERE ts < ?", (cutoff_ts,)).fetchall()
        chat_ids = [r["chat_id"] for r in rows]
        cur = self.conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff_ts,))
        deleted = cur.rowcount
        self._rebuild_messages_fts()
        self.conn.commit()
        return {"deleted_rows": deleted, "affected_chats": chat_ids}

    # ---- reply-workflow-optimization: 回复任务 (D1/D7) ----
    def create_reply_task(self, customer_id, chat_id, message, style, session_id, mode,
                          language=None, scenario=None, formality=None):
        task_id = uuid.uuid4().hex
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO reply_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, customer_id, chat_id, message, style, session_id, mode,
             "pending", None, None, now, now, language, scenario, formality))
        self.conn.commit()
        return task_id

    def get_reply_task(self, task_id):
        r = self.conn.execute("SELECT * FROM reply_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(r) if r else None

    def next_pending_reply_task(self):
        r = self.conn.execute(
            "SELECT * FROM reply_tasks WHERE status='pending' "
            "ORDER BY created_at ASC, rowid ASC LIMIT 1").fetchone()
        return dict(r) if r else None

    def update_reply_task(self, task_id, *, status=None, result=None, error=None):
        self.conn.execute(
            "UPDATE reply_tasks SET status=COALESCE(?,status), result=COALESCE(?,result), "
            "error=COALESCE(?,error), updated_at=? WHERE id=?",
            (status, result, error, int(time.time()), task_id))
        self.conn.commit()

    def mark_legacy_reply_tasks_failed(self):
        self.conn.execute(
            "UPDATE reply_tasks SET status='failed', "
            "error='进程重启遗留任务已清理', updated_at=? "
            "WHERE status IN ('pending','running')", (int(time.time()),))
        self.conn.commit()

    def mark_stuck_reply_tasks_failed(self, timeout_sec: int = 180) -> int:
        """把超过 timeout_sec 仍处于 running 的任务标记为 failed (回复生成卡住兜底)。

        仅处理 running (正在生成) 的任务: pending 可能只是排队等待, 不应误杀。
        返回受影响行数。"""
        cutoff = int(time.time()) - timeout_sec
        cur = self.conn.execute(
            "UPDATE reply_tasks SET status='failed', "
            "error='生成超时，请重试', updated_at=? "
            "WHERE status='running' AND updated_at < ?",
            (int(time.time()), cutoff))
        self.conn.commit()
        return cur.rowcount

    # ---- reply-workflow-optimization: 多轮会话 (D4) ----
    def find_or_create_reply_session(self, customer_id, chat_id):
        r = self.conn.execute(
            "SELECT id FROM reply_sessions WHERE customer_id=? AND chat_id=?",
            (customer_id, chat_id)).fetchone()
        if r:
            return r["id"]
        sid = uuid.uuid4().hex
        now = int(time.time())
        try:
            self.conn.execute("INSERT INTO reply_sessions VALUES(?,?,?,?,?)",
                              (sid, customer_id, chat_id, now, now))
            self.conn.commit()
        except sqlite3.IntegrityError:
            r = self.conn.execute(
                "SELECT id FROM reply_sessions WHERE customer_id=? AND chat_id=?",
                (customer_id, chat_id)).fetchone()
            if r:
                return r["id"]
            raise
        return sid

    def append_session_message(self, session_id, role, content):
        now = int(time.time())
        self.conn.execute("INSERT INTO reply_session_messages VALUES(?,?,?,?,?)",
                          (uuid.uuid4().hex, session_id, role, content, now))
        self.conn.commit()

    def get_session_history(self, session_id, limit=10):
        rows = self.conn.execute(
            "SELECT role, content, ts, rowid FROM ("
            "  SELECT role, content, ts, rowid FROM reply_session_messages WHERE session_id=? "
            "  ORDER BY ts DESC, rowid DESC LIMIT ?) "
            "ORDER BY ts ASC, rowid ASC",
            (session_id, limit)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def _row_to_msg(self, r):
        return Message(r["id"], r["account_id"], r["chat_id"], bool(r["from_me"]), r["sender_jid"],
                       r["ts"], r["type"], r["body"], bool(r["body_present"]), r["ingested_at"],
                       r["sender_name"])

    # ---- collector-settings-center: 全量扫描请求 (D1 意图表) ----
    def create_scan_request(self) -> int:
        """记录一次全量扫描请求 (无参数, 一次一条)。返回新行 id。"""
        cur = self.conn.execute(
            "INSERT INTO scan_requests(requested_at) VALUES(?)", (int(time.time()),))
        self.conn.commit()
        return cur.lastrowid

    def next_pending_scan_request(self):
        """取待消费请求: 未完成且 attempts<3, 按请求先后。"""
        r = self.conn.execute(
            "SELECT * FROM scan_requests WHERE done=0 AND attempts<3 "
            "ORDER BY id ASC LIMIT 1").fetchone()
        return dict(r) if r else None

    def has_active_scan_request(self) -> bool:
        """是否存在未完成请求 (pending/running/failed 待重试, done=0) — Web 层 busy 判定。
        与 next_pending 同口径: done=0 AND attempts<3, 避免 failed 待重试行漏判致连续两次扫描。"""
        r = self.conn.execute(
            "SELECT id FROM scan_requests WHERE done=0 AND attempts<3 ORDER BY id LIMIT 1").fetchone()
        return r is not None

    def mark_scan_request_running(self, req_id: int):
        self.conn.execute("UPDATE scan_requests SET status='running' WHERE id=?", (req_id,))
        self.conn.commit()

    def mark_scan_request_done(self, req_id: int):
        self.conn.execute("UPDATE scan_requests SET status='done', done=1 WHERE id=?", (req_id,))
        self.conn.commit()

    def bump_scan_request_attempts(self, req_id: int):
        self.conn.execute(
            "UPDATE scan_requests SET attempts=attempts+1, status='failed' WHERE id=?", (req_id,))
        self.conn.commit()

    # ---- whatsapp-bidirectional-chat: 发送任务 (send_requests, 镜像 scan_requests) ----
    def create_send_request(self, chat_id: str, text: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO send_requests(chat_id, text, requested_at, updated_at) VALUES(?,?,?,?)",
            (chat_id, text, int(time.time()), int(time.time())))
        self.conn.commit()
        return cur.lastrowid

    def get_send_request(self, req_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM send_requests WHERE id=?", (req_id,)).fetchone()
        return dict(r) if r else None

    def next_pending_send_request(self) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM send_requests WHERE done=0 AND attempts<3 ORDER BY id ASC LIMIT 1").fetchone()
        return dict(r) if r else None

    def mark_send_request_running(self, req_id: int):
        self.conn.execute(
            "UPDATE send_requests SET status='running', updated_at=? WHERE id=?",
            (int(time.time()), req_id))
        self.conn.commit()

    def mark_send_request_done(self, req_id: int):
        self.conn.execute(
            "UPDATE send_requests SET status='done', done=1, updated_at=? WHERE id=?",
            (int(time.time()), req_id))
        self.conn.commit()

    def mark_send_request_failed(self, req_id: int, error: str):
        """直接失败并终止重试 (如开关关闭)。"""
        self.conn.execute(
            "UPDATE send_requests SET status='failed', error=?, done=1, updated_at=? WHERE id=?",
            (error, int(time.time()), req_id))
        self.conn.commit()

    def bump_send_request_attempts(self, req_id: int, error: str):
        """瞬时失败 attempts+1; 满 3 次后 next_pending 不再取 (done 保持 0, 与 scan 同口径)。"""
        self.conn.execute(
            "UPDATE send_requests SET attempts=attempts+1, status='failed', error=?, updated_at=? WHERE id=?",
            (error, int(time.time()), req_id))
        self.conn.commit()

    # ---- whatsapp-bidirectional-chat: 会话列表实时预览 ----
    def upsert_chat_previews(self, previews: list[dict]):
        now = int(time.time())
        for p in previews:
            self.conn.execute(
                "INSERT INTO chat_previews VALUES(?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET "
                "unread_count=excluded.unread_count, preview=excluded.preview, updated_at=excluded.updated_at",
                (p["chat_id"], p.get("unread_count") or 0, p.get("preview"), now))
        self.conn.commit()

    def get_customers_chat_preview(self, customer_ids: list[str]) -> dict[str, dict]:
        """批量返回 {customer_id: {unread, preview}}; unread 为各会话未读之和。"""
        if not customer_ids:
            return {}
        result = {cid: {"unread": 0, "preview": None} for cid in customer_ids}
        ph = ",".join("?" * len(customer_ids))
        for r in self.conn.execute(
            "SELECT cm.customer_id, p.unread_count, p.preview FROM chat_previews p "
            "JOIN customer_chat_map cm ON cm.chat_id=p.chat_id "
            "WHERE cm.customer_id IN (%s)" % ph, customer_ids).fetchall():
            cur = result[r["customer_id"]]
            cur["unread"] += r["unread_count"] or 0
            if r["preview"] and cur["preview"] is None:
                cur["preview"] = r["preview"]
        return result

    def resolve_chat_ids_by_names(self, names: list[str]) -> dict[str, str]:
        """按显示名反查 chat_id。返回 {name: chat_id}。

        优先级 (高→低):
        1. customers + customer_chat_map: 客户显示名来自画像/匹配, 比 chats 表可信。
           chats 表可能被 IDB 串名污染 (如 Lucas 会话显示成「苏童」), 导致同名会话
           映射到错误 chat_id —— 这正是「检测到未读却显示在别的客户」的根因。
        2. chats 表: 补充尚未匹配客户的会话 (含群聊)。
        3. contacts 表: 含 @lid → @c.us 归一, 兜底。
        各层均优先 @c.us 形态; setdefault 保证同名取首个稳定命中。
        """
        out = {}
        for r in self.conn.execute(
                "SELECT cu.display_name AS name, cm.chat_id AS cid FROM customers cu "
                "JOIN customer_chat_map cm ON cm.customer_id = cu.id "
                "WHERE cu.display_name IS NOT NULL AND cu.display_name != '' "
                "ORDER BY (cm.chat_id LIKE '%@c.us') DESC, cm.match_confidence DESC").fetchall():
            out.setdefault(r["name"], r["cid"])
        for r in self.conn.execute(
                "SELECT id, display_name FROM chats WHERE display_name IS NOT NULL AND display_name != '' "
                "ORDER BY (id LIKE '%@c.us') DESC").fetchall():
            out.setdefault(r["display_name"], r["id"])
        for r in self.conn.execute(
                "SELECT jid, display_name, phone FROM contacts "
                "WHERE display_name IS NOT NULL AND display_name != '' "
                "ORDER BY (jid LIKE '%@c.us') DESC").fetchall():
            jid = r["jid"]
            if jid and str(jid).endswith("@lid") and r["phone"]:
                jid = f'{r["phone"]}@c.us'
            out.setdefault(r["display_name"], jid)
        return out

    def reconcile_chat_names_from_customers(self) -> int:
        """把单聊 chats.display_name 按手机号回填为 customers 画像名 (Part C: 清脏名)。

        chats 表可能被 IDB 串名污染 (如 Lucas 会话显示成「苏童」), 而 customers 表的
        display_name 只在为空时补齐、不会被覆盖, 更可信。此处对每个 @c.us 会话, 若存在
        手机号一致的客户且画像名与 chats 名不同, 用画像名纠正。群聊/无客户映射的会话不动。
        返回纠正条数。
        """
        changed = 0
        try:
            rows = self.conn.execute("SELECT id FROM chats WHERE id LIKE '%@c.us'").fetchall()
        except Exception:
            return 0
        for r in rows:
            chat_id = r["id"]
            digits = str(chat_id).rsplit("@", 1)[0]
            if not digits.isdigit():
                continue
            cust = self.conn.execute(
                "SELECT display_name FROM customers WHERE phone=? "
                "AND display_name IS NOT NULL AND display_name != ''",
                (digits,)).fetchone()
            if not cust:
                continue
            new_name = cust["display_name"]
            cur = self.conn.execute(
                "SELECT display_name FROM chats WHERE id=?", (chat_id,)).fetchone()
            if cur and (cur["display_name"] or "") == new_name:
                continue
            self.conn.execute(
                "UPDATE chats SET display_name=? WHERE id=?", (new_name, chat_id))
            changed += 1
        if changed:
            self.conn.commit()
        return changed

    # ---- customer-intent-tiering: 分层历史 + 分层任务 ----
    def add_tier_history(self, customer_id, intent_level, tags, source):
        self.conn.execute(
            "INSERT INTO customer_tier_history VALUES(?,?,?,?,?,?)",
            (uuid.uuid4().hex, customer_id, intent_level, tags, source, int(time.time())))
        self.conn.commit()

    def get_tier_history(self, customer_id):
        rows = self.conn.execute(
            "SELECT * FROM customer_tier_history WHERE customer_id=? "
            "ORDER BY created_at ASC, rowid ASC", (customer_id,)).fetchall()
        return [dict(r) for r in rows]

    def create_tiering_task(self, customer_ids):
        task_id = uuid.uuid4().hex
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO tiering_tasks VALUES(?,?,?,?,?,?,?,?)",
            (task_id, json.dumps(customer_ids, ensure_ascii=False), "pending",
             0, None, None, now, now))
        self.conn.commit()
        return task_id

    @staticmethod
    def _parse_tiering_customer_ids(raw) -> list:
        """解析任务 customer_ids JSON; 损坏数据返回 [] 避免 worker 无限重试。"""
        try:
            data = json.loads(raw or "[]")
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def get_tiering_task(self, task_id):
        r = self.conn.execute("SELECT * FROM tiering_tasks WHERE id=?", (task_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["customer_ids"] = self._parse_tiering_customer_ids(d["customer_ids"])
        return d

    def next_pending_tiering_task(self):
        r = self.conn.execute(
            "SELECT * FROM tiering_tasks WHERE status='pending' "
            "ORDER BY created_at ASC, rowid ASC LIMIT 1").fetchone()
        if not r:
            return None
        d = dict(r)
        d["customer_ids"] = self._parse_tiering_customer_ids(d["customer_ids"])
        return d

    def update_tiering_task(self, task_id, *, status=None, progress=None, result=None, error=None):
        self.conn.execute(
            "UPDATE tiering_tasks SET status=COALESCE(?,status), "
            "progress=COALESCE(?,progress), result=COALESCE(?,result), "
            "error=COALESCE(?,error), updated_at=? WHERE id=?",
            (status, progress, result, error, int(time.time()), task_id))
        self.conn.commit()

    def list_recent_active_customers(self, days):
        cutoff = int(time.time()) - days * 86400
        rows = self.conn.execute(
            "SELECT DISTINCT cm.customer_id FROM customer_chat_map cm "
            "JOIN messages m ON m.chat_id = cm.chat_id "
            "WHERE m.ts >= ?", (cutoff,)).fetchall()
        return [r["customer_id"] for r in rows]

    # ---- customer-summary: 历史对话结构化摘要 ----
    def upsert_customer_summary(self, customer_id: str, data: dict, last_ts: int | None = None) -> None:
        """写入/更新客户结构化摘要 (customer_summaries 表)。data 含 overview/intent_vehicle 等字段。
        last_ts 为增量游标 (已处理到的最大消息时间戳); 缺省保留原值。"""
        if last_ts is None:
            cur = self.conn.execute(
                "SELECT last_ts FROM customer_summaries WHERE customer_id=?", (customer_id,)).fetchone()
            last_ts = cur["last_ts"] if cur else 0
        self.conn.execute(
            "INSERT INTO customer_summaries VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(customer_id) DO UPDATE SET "
            "overview=excluded.overview, intent_vehicle=excluded.intent_vehicle, "
            "budget_range=excluded.budget_range, target_country=excluded.target_country, "
            "concerns=excluded.concerns, follow_up=excluded.follow_up, updated_at=excluded.updated_at, "
            "last_ts=excluded.last_ts",
            (customer_id, data.get("overview", ""), data.get("intent_vehicle", ""),
             data.get("budget_range", ""), data.get("target_country", ""),
             data.get("concerns", ""), data.get("follow_up", ""), int(time.time()), last_ts))
        self.conn.commit()

    def get_customer_summary(self, customer_id: str) -> dict | None:
        """读取客户结构化摘要; 无则返回 None。"""
        r = self.conn.execute(
            "SELECT * FROM customer_summaries WHERE customer_id=?", (customer_id,)).fetchone()
        if not r:
            return None
        return dict(r)

    def get_customer_summary_last_ts(self, customer_id: str) -> int:
        """读取客户摘要的增量游标 (已处理到的最大消息 ts); 无摘要返回 0。"""
        r = self.conn.execute(
            "SELECT last_ts FROM customer_summaries WHERE customer_id=?", (customer_id,)).fetchone()
        return r["last_ts"] if r and r["last_ts"] is not None else 0

    # ---- workspace-live-refresh: 客户最近活跃 + 未读 (settings 表记最后查看时间) ----
    def get_last_seen(self, customer_id: str) -> int:
        """读取客户工作台最后查看时间 (settings.ws_last_seen:{customer_id}); 无则 0。"""
        r = self.conn.execute(
            "SELECT value FROM settings WHERE key=?", (f"ws_last_seen:{customer_id}",)).fetchone()
        if not r or not r["value"]:
            return 0
        try:
            return int(r["value"])
        except (TypeError, ValueError):
            return 0

    def set_last_seen(self, customer_id: str, ts: int):
        """记录客户工作台最后查看时间 (视为已读)。"""
        self.conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (f"ws_last_seen:{customer_id}", str(ts), int(time.time())))
        self.conn.commit()

    def get_customer_recent_activity(self, customer_id: str) -> dict:
        """返回客户最近活跃信息: 最近消息时间 + 未读数 (非我方且 ts > 最后查看时间)。
        未读定义: from_me=0 且 ts > last_seen。"""
        last_seen = self.get_last_seen(customer_id)
        # 该客户全部关联会话的最近消息时间
        recent = self.conn.execute(
            "SELECT MAX(m.ts) AS last_ts FROM messages m "
            "JOIN customer_chat_map c ON c.chat_id=m.chat_id "
            "WHERE c.customer_id=?", (customer_id,)).fetchone()
        last_ts = recent["last_ts"] if recent and recent["last_ts"] is not None else 0
        # 未读数: 非我方且 ts > last_seen
        unread = self.conn.execute(
            "SELECT COUNT(*) AS n FROM messages m "
            "JOIN customer_chat_map c ON c.chat_id=m.chat_id "
            "WHERE c.customer_id=? AND m.from_me=0 AND m.ts>?", (customer_id, last_seen)).fetchone()
        return {"last_ts": last_ts, "unread": unread["n"] if unread else 0}

    def get_customers_recent_activity(self, customer_ids: list[str]) -> dict[str, dict]:
        """批量返回多个客户的最近活跃 (最近消息时间 + 未读数), 避免 N+1 查询。
        返回 {customer_id: {last_ts, unread}}。"""
        if not customer_ids:
            return {}
        result: dict[str, dict] = {cid: {"last_ts": 0, "unread": 0} for cid in customer_ids}
        placeholders = ",".join("?" * len(customer_ids))
        # 最近消息时间 (一次 JOIN 聚合)
        for r in self.conn.execute(
            "SELECT c.customer_id, MAX(m.ts) AS last_ts FROM messages m "
            "JOIN customer_chat_map c ON c.chat_id=m.chat_id "
            "WHERE c.customer_id IN (%s) GROUP BY c.customer_id" % placeholders,
            customer_ids).fetchall():
            result[r["customer_id"]]["last_ts"] = r["last_ts"] or 0
        # 未读数: 非我方且 ts > last_seen (一次 JOIN settings 取 last_seen, 避免逐客户 N+1)
        for r in self.conn.execute(
            "SELECT c.customer_id, COUNT(*) AS n FROM messages m "
            "JOIN customer_chat_map c ON c.chat_id=m.chat_id "
            "LEFT JOIN settings s ON s.key='ws_last_seen:'||c.customer_id "
            "WHERE c.customer_id IN (%s) AND m.from_me=0 "
            "AND m.ts > COALESCE(CAST(s.value AS INTEGER), 0) "
            "GROUP BY c.customer_id" % placeholders,
            customer_ids).fetchall():
            result[r["customer_id"]]["unread"] = r["n"]
        return result

    # ---- customer-summary: 摘要异步任务 (worker 串行消费) ----
    def create_summary_task(self, customer_id: str) -> str:
        task_id = uuid.uuid4().hex
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO summary_tasks VALUES(?,?,?,?,?,?,?)",
            (task_id, customer_id, "pending", None, None, now, now))
        self.conn.commit()
        return task_id

    def get_summary_task(self, task_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM summary_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(r) if r else None

    def next_pending_summary_task(self) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM summary_tasks WHERE status='pending' "
            "ORDER BY created_at ASC, rowid ASC LIMIT 1").fetchone()
        return dict(r) if r else None

    def update_summary_task(self, task_id: str, *, status=None, result=None, error=None):
        self.conn.execute(
            "UPDATE summary_tasks SET status=COALESCE(?,status), result=COALESCE(?,result), "
            "error=COALESCE(?,error), updated_at=? WHERE id=?",
            (status, result, error, int(time.time()), task_id))
        self.conn.commit()

    def mark_legacy_summary_tasks_failed(self):
        self.conn.execute(
            "UPDATE summary_tasks SET status='failed', "
            "error='进程重启遗留任务已清理', updated_at=? "
            "WHERE status IN ('pending','running')", (int(time.time()),))
        self.conn.commit()
