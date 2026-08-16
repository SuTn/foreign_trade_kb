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
  session_id TEXT, mode TEXT, status TEXT, result TEXT, error TEXT, created_at INTEGER, updated_at INTEGER,
  language TEXT, scenario TEXT, formality TEXT);
CREATE TABLE IF NOT EXISTS reply_sessions(
  id TEXT PRIMARY KEY, customer_id TEXT, chat_id TEXT, created_at INTEGER, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS reply_session_messages(
  id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, ts INTEGER);
CREATE INDEX IF NOT EXISTS idx_reply_tasks_status ON reply_tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_reply_sessions_cust_chat ON reply_sessions(customer_id, chat_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reply_sessions_cust_chat_uniq ON reply_sessions(customer_id, chat_id);
CREATE INDEX IF NOT EXISTS idx_reply_sess_msgs ON reply_session_messages(session_id, ts);
-- 采集器设置中心 (collector-settings-center): 参数持久化 + 全量扫描意图表
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at INTEGER);
CREATE TABLE IF NOT EXISTS scan_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  requested_at INTEGER,
  status TEXT DEFAULT 'pending',   -- pending | running | done | failed
  attempts INTEGER DEFAULT 0,
  done INTEGER DEFAULT 0);
-- 客户自动分层标签体系 (customer-intent-tiering): 分层任务表 / 分层历史表
CREATE TABLE IF NOT EXISTS tiering_tasks(
  id TEXT PRIMARY KEY,
  customer_ids TEXT,          -- JSON 数组
  status TEXT,                -- pending | running | done | failed
  progress INTEGER DEFAULT 0,
  result TEXT,
  error TEXT,
  created_at INTEGER,
  updated_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_tiering_tasks_status ON tiering_tasks(status, created_at);
CREATE TABLE IF NOT EXISTS customer_tier_history(
  id TEXT PRIMARY KEY,
  customer_id TEXT,
  intent_level TEXT,
  tags TEXT,
  source TEXT,                -- auto | manual
  created_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_tier_history_customer ON customer_tier_history(customer_id, created_at);
-- 历史对话智能摘要 (customer-summary): 按客户聚合的结构化摘要
CREATE TABLE IF NOT EXISTS customer_summaries(
  customer_id TEXT PRIMARY KEY,
  overview TEXT,          -- 自由文本概述
  intent_vehicle TEXT,    -- 意向车型
  budget_range TEXT,      -- 预算区间
  target_country TEXT,    -- 目标国家
  concerns TEXT,          -- 核心顾虑
  follow_up TEXT,         -- 待跟进事项
  updated_at INTEGER,
  last_ts INTEGER);       -- 增量游标: 已处理到的最大消息时间戳 (0=尚未增量)
-- 摘要异步任务 (customer-summary): 与 reply_tasks 同构, worker 串行消费
CREATE TABLE IF NOT EXISTS summary_tasks(
  id TEXT PRIMARY KEY,
  customer_id TEXT,
  status TEXT,            -- pending | running | done | failed
  result TEXT,            -- JSON 摘要
  error TEXT,
  created_at INTEGER,
  updated_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_summary_tasks_status ON summary_tasks(status, created_at);
-- 双向收发 (whatsapp-bidirectional-chat): 发送任务表 (镜像 scan_requests 语义)
CREATE TABLE IF NOT EXISTS send_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id TEXT,
  text TEXT,
  status TEXT DEFAULT 'pending',   -- pending | running | done | failed
  attempts INTEGER DEFAULT 0,
  error TEXT,
  done INTEGER DEFAULT 0,
  requested_at INTEGER,
  updated_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_send_requests_status ON send_requests(status, requested_at);
-- 双向收发: 会话列表实时预览 (未读红点 + 最后一句, 不打开会话)
CREATE TABLE IF NOT EXISTS chat_previews(
  chat_id TEXT PRIMARY KEY,
  unread_count INTEGER DEFAULT 0,
  preview TEXT,
  updated_at INTEGER);
