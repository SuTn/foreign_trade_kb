-- app/storage/schema.sql
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS chats(
  id TEXT, account_id TEXT, jid TEXT, display_name TEXT, kind TEXT, last_synced_at INTEGER,
  PRIMARY KEY(id, account_id));
CREATE TABLE IF NOT EXISTS messages(
  id TEXT, account_id TEXT, chat_id TEXT, from_me INTEGER, sender_jid TEXT,
  ts INTEGER, type TEXT, body TEXT, body_present INTEGER, ingested_at INTEGER,
  sender_name TEXT,
  PRIMARY KEY(id, account_id));
CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, ts);
CREATE TABLE IF NOT EXISTS contacts(
  jid TEXT, account_id TEXT, display_name TEXT, phone TEXT, updated_at INTEGER,
  PRIMARY KEY(jid, account_id));
CREATE TABLE IF NOT EXISTS customers(
  id TEXT PRIMARY KEY, display_name TEXT, phone TEXT, company TEXT, country TEXT, created_at INTEGER,
  avatar_path TEXT);
CREATE TABLE IF NOT EXISTS customer_chat_map(
  account_id TEXT, chat_id TEXT, customer_id TEXT, match_confidence REAL, confirmed INTEGER, updated_at INTEGER,
  PRIMARY KEY(account_id, chat_id));
CREATE TABLE IF NOT EXISTS profiles(
  customer_id TEXT, field TEXT, value TEXT, source TEXT, updated_at INTEGER,
  PRIMARY KEY(customer_id, field));
CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, filename TEXT, format TEXT, parser TEXT, status TEXT, ingested_at INTEGER);
CREATE TABLE IF NOT EXISTS doc_chunks(
  id TEXT, doc_id TEXT, chunk_idx INTEGER, text TEXT, parent_chunk_id TEXT, vector_id TEXT,
  PRIMARY KEY(doc_id, chunk_idx));
CREATE TABLE IF NOT EXISTS wiki_pages(
  id TEXT PRIMARY KEY, title TEXT, slug TEXT UNIQUE, body_md TEXT, frontmatter TEXT,
  source_doc_ids TEXT, entity_type TEXT, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS wiki_links(from_page_id TEXT, to_page_id TEXT);
CREATE TABLE IF NOT EXISTS wiki_log_entries(id TEXT PRIMARY KEY, page_id TEXT, action TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS backfill_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, max_scrolls INTEGER,
  requested_at INTEGER, done INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0);
-- FTS5
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(body, content='messages', content_rowid='rowid');
CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(text, content='doc_chunks', content_rowid='rowid');
-- 回复异步化 (reply-workflow-optimization): 任务表 / 会话表
-- mode: generate=主生成(追加会话历史) | regenerate=重生成(只读历史不追加)
CREATE TABLE IF NOT EXISTS reply_tasks(
  id TEXT PRIMARY KEY, customer_id TEXT, chat_id TEXT, message TEXT, style TEXT,
  session_id TEXT, mode TEXT, status TEXT, result TEXT, error TEXT, created_at INTEGER, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS reply_sessions(
  id TEXT PRIMARY KEY, customer_id TEXT, chat_id TEXT, created_at INTEGER, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS reply_session_messages(
  id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, ts INTEGER);
CREATE INDEX IF NOT EXISTS idx_reply_tasks_status ON reply_tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_reply_sessions_cust_chat ON reply_sessions(customer_id, chat_id);
CREATE INDEX IF NOT EXISTS idx_reply_sess_msgs ON reply_session_messages(session_id, ts);
