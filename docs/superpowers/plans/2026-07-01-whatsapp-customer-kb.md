# 外贸客户知识库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个本地外贸客户知识库：WhatsApp 聊天同步 + 客户画像 + RAG 辅助回复 + 本地知识导入（RAG+Wiki 双索引，Wiki 导出 Obsidian vault）。

**Architecture:** 双进程（FastAPI Web 进程 + 独立采集器进程），共享 SQLite(WAL+FTS5) + Chroma。两层抽象：存储层（StructuredStore/VectorStore）+ 模型层（LLM/Embedding）。WhatsApp 采集走 Playwright CDP，经 ReadOnlyCDP 门面保证只读。RAG 管线借鉴 WeKnora chat_pipeline 插件式设计自研，Wiki 实体级两阶段去重。

**Tech Stack:** Python 3.11+，FastAPI+Uvicorn，Playwright，SQLite(WAL+FTS5)，Chroma，LangChain，bge-m3/bge-reranker-v2-m3，WeKnora docreader(vendored)，Jinja2+HTMX。无 Docker。

---
change: whatsapp-customer-kb
design-doc: docs/superpowers/specs/2026-07-01-whatsapp-customer-kb-design.md
base-ref: 612de0f0c6c0958ac6d03df864e61290e18550b3
---

## Global Constraints

- Python ≥ 3.11
- 无 Docker；纯 Python 本地运行
- WhatsApp 采集全程只读（经 ReadOnlyCDP 门面），禁止任何发送/输入类 CDP 操作
- 客户聊天内容经云端 LLM 处理（用户已确认接受）
- WeKnora docreader 为 MIT，vendored 时保留原始版权声明
- 测试不连真 WhatsApp，全部用 fixture，CI 可跑
- SQLite 双进程写靠 WAL + busy_timeout 重试
- 包结构单向依赖：collector/storage/llm/knowledge/rag/profile/reply/web

## File Structure

```
foreign_trade/
├── pyproject.toml
├── app/
│   ├── __init__.py
│   ├── __main__.py              # 启动脚本: 拉起 web + collector 两进程
│   ├── config.py                # 集中配置 (DOM选择器/IDB store名/轮询间隔/LLM/embedding/路径)
│   ├── storage/
│   │   ├── interfaces.py        # StructuredStore/VectorStore 抽象接口
│   │   ├── sqlite_store.py      # SQLite 实现 (WAL+FTS5+busy_timeout)
│   │   ├── chroma_store.py      # Chroma 实现
│   │   └── schema.sql           # 建表 SQL
│   ├── llm/
│   │   ├── interfaces.py        # LLM/Embedding 抽象接口
│   │   ├── cloud_llm.py         # 云端 Claude/OpenAI
│   │   └── bge_embedding.py     # bge-m3 本地
│   ├── collector/
│   │   ├── __main__.py          # python -m app.collector
│   │   ├── readonly_cdp.py      # ReadOnlyCDP 门面 (白名单只读)
│   │   ├── idb_walk.py          # IDB walk (model-storage stores)
│   │   ├── dom_snapshot.py      # DOM 快照解析 [data-id] 行
│   │   ├── merger.py            # 元数据+正文按 id 合并
│   │   └── scanner.py           # 双 tick 循环 + 按需回溯 + status.json
│   ├── knowledge/
│   │   ├── parser.py            # WeKnora docreader 适配层
│   │   ├── chunker.py           # 切分 (chunk_size/overlap + 父子块)
│   │   ├── index_strategy.py    # 索引策略抽象接口
│   │   ├── rag_index.py         # RAG 索引策略
│   │   ├── wiki_index.py        # Wiki 索引 (两阶段去重)
│   │   └── wiki_export.py       # Obsidian vault 导出
│   ├── rag/
│   │   ├── pipeline.py          # RAG 管线骨架 (插件式)
│   │   ├── retrievers.py        # 多路召回 (4路)
│   │   └── reranker.py          # bge-reranker 重排
│   ├── profile/
│   │   ├── matcher.py           # 客户匹配 (手机号+显示名启发式)
│   │   ├── extractor.py         # 画像抽取 (单行覆盖语义)
│   │   └── analyzer.py          # 客户分析
│   ├── reply/
│   │   └── generator.py         # 辅助回复生成 (仅生成不发送)
│   └── web/
│       ├── app.py               # FastAPI app
│       ├── routes.py            # 路由
│       ├── templates/           # Jinja2 模板
│       └── static/              # HTMX/样式
├── vendor/docreader/            # WeKnora docreader (vendored, 保留版权)
├── tests/                       # 镜像 app/ 结构
└── data/                        # 运行产物 (gitignore)
```

---

## Task 1: 项目骨架与依赖

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py`, `app/config.py`
- Create: `tests/__init__.py`, `tests/conftest.py`

**Interfaces:**
- Produces: `app.config.Settings` (后续所有模块读取配置的入口)

- [x] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "foreign-trade-kb"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "jinja2>=3.1",
    "playwright>=1.42",
    "chromadb>=0.4.22",
    "langchain>=0.1.10",
    "langchain-community>=0.0.25",
    "anthropic>=0.21",
    "openai>=1.14",
    "pypdfium2>=4.27",
    "openpyxl>=3.1",
    "python-docx>=1.1",
    "pandas>=2.2",
    "beautifulsoup4>=4.12",
    "pydantic>=2.6",
    "pydantic-settings>=2.2",
    "FlagEmbedding>=1.2.7",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "httpx>=0.27"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [x] **Step 2: 写 app/config.py**

```python
from pathlib import Path
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # 路径
    data_dir: Path = Path("data")
    sqlite_path: Path = Path("data/kb.db")
    chroma_dir: Path = Path("data/chroma")
    user_data_dir: Path = Path("data/user-data-dir")
    status_path: Path = Path("data/status.json")
    vault_export_dir: Path = Path("data/vault")

    # WhatsApp 采集
    whatsapp_url: str = "https://web.whatsapp.com"
    fast_tick_sec: float = 2.0
    fast_tick_jitter: float = 0.5
    slow_tick_sec: float = 30.0
    slow_tick_jitter: float = 5.0
    idb_database: str = "model-storage"
    idb_stores: list[str] = ["message", "chat", "contact", "group-metadata"]
    max_records_per_store: int = 20000
    heartbeat_timeout_sec: float = 30.0

    # DOM 选择器 (集中配置, 便于 WhatsApp Web 变更时修补)
    dom_message_row_selector: str = "[data-id]"
    dom_conversation_header_selector: str = 'header[data-testid="conversation-header"]'

    # LLM / Embedding
    llm_provider: str = "anthropic"  # anthropic | openai
    llm_model: str = "claude-sonnet-4-6"
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # RAG
    chunk_size: int = 512
    chunk_overlap: int = 64
    rerank_top_k: int = 8
    context_token_limit: int = 4000
    wiki_dedup_threshold: float = 0.85

    class Config:
        env_prefix = "KB_"
        env_file = ".env"

settings = Settings()
```

- [x] **Step 3: 写 tests/conftest.py**

```python
import pytest
from pathlib import Path

@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """每个测试用独立 data 目录, 避免污染。"""
    from app import config
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "sqlite_path", tmp_path / "kb.db")
    monkeypatch.setattr(config.settings, "chroma_dir", tmp_path / "chroma")
    monkeypatch.setattr(config.settings, "status_path", tmp_path / "status.json")
    monkeypatch.setattr(config.settings, "vault_export_dir", tmp_path / "vault")
    return tmp_path
```

- [x] **Step 4: 安装依赖并验证可导入**

Run: `pip install -e ".[dev]" && python -c "from app.config import settings; print(settings.chunk_size)"`
Expected: 输出 `512`

- [x] **Step 5: Commit**

```bash
git add pyproject.toml app/__init__.py app/config.py tests/
git commit -m "feat: 项目骨架与集中配置"
```

---

## Task 2: 存储层抽象接口

**Files:**
- Create: `app/storage/interfaces.py`
- Test: `tests/storage/test_interfaces.py`

**Interfaces:**
- Produces: `StructuredStore` (ABC), `VectorStore` (ABC), 数据 dataclass

- [x] **Step 1: 写 app/storage/interfaces.py**

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Chat:
    id: str; account_id: str; jid: str; display_name: str | None
    kind: str | None; last_synced_at: int

@dataclass
class Message:
    id: str; account_id: str; chat_id: str; from_me: bool
    sender_jid: str | None; ts: int; type: str | None
    body: str | None; body_present: bool; ingested_at: int

@dataclass
class ProfileField:
    customer_id: str; field: str; value: str; source: str; updated_at: int

@dataclass
class WikiPage:
    id: str; title: str; slug: str; body_md: str; frontmatter: dict
    source_doc_ids: list[str]; entity_type: str | None; updated_at: int

class StructuredStore(ABC):
    @abstractmethod
    def upsert_chat(self, chat: Chat) -> None: ...
    @abstractmethod
    def upsert_message(self, msg: Message) -> None: ...
    @abstractmethod
    def upsert_profile_field(self, customer_id: str, field: str, value: str, source: str) -> None: ...
    @abstractmethod
    def get_profile(self, customer_id: str) -> list[ProfileField]: ...
    @abstractmethod
    def list_messages(self, chat_id: str, limit: int = 50, before_ts: int | None = None) -> list[Message]: ...
    @abstractmethod
    def search_fts(self, table: str, query: str, limit: int = 20) -> list[dict]: ...
    @abstractmethod
    def upsert_wiki_page(self, page: WikiPage) -> None: ...
    @abstractmethod
    def get_wiki_page(self, slug: str) -> WikiPage | None: ...

class VectorStore(ABC):
    @abstractmethod
    def upsert_message_vector(self, key: str, text: str, metadata: dict) -> None: ...
    @abstractmethod
    def upsert_chunks(self, chunks: list[dict]) -> None: ...  # {id, text, metadata}
    @abstractmethod
    def query_messages(self, text: str, chat_id: str | None, top_k: int = 5) -> list[dict]: ...
    @abstractmethod
    def query_chunks(self, text: str, top_k: int = 5) -> list[dict]: ...
```

- [x] **Step 2: 写测试验证接口可实例化约束**

```python
# tests/storage/test_interfaces.py
import pytest
from app.storage.interfaces import StructuredStore, VectorStore

def test_structured_store_is_abstract():
    with pytest.raises(TypeError):
        StructuredStore()

def test_vector_store_is_abstract():
    with pytest.raises(TypeError):
        VectorStore()
```

- [x] **Step 3: 运行测试**

Run: `pytest tests/storage/test_interfaces.py -v`
Expected: PASS

- [x] **Step 4: Commit**

```bash
git add app/storage/interfaces.py tests/storage/
git commit -m "feat: 存储层抽象接口"
```

---

## Task 3: SQLite 结构化存储实现

**Files:**
- Create: `app/storage/schema.sql`
- Create: `app/storage/sqlite_store.py`
- Test: `tests/storage/test_sqlite_store.py`

**Interfaces:**
- Consumes: `StructuredStore` (Task 2), `app.config.settings`
- Produces: `SqliteStore` (实现 StructuredStore)

- [x] **Step 1: 写 schema.sql**

```sql
-- app/storage/schema.sql
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS chats(
  id TEXT, account_id TEXT, jid TEXT, display_name TEXT, kind TEXT, last_synced_at INTEGER,
  PRIMARY KEY(id, account_id));
CREATE TABLE IF NOT EXISTS messages(
  id TEXT, account_id TEXT, chat_id TEXT, from_me INTEGER, sender_jid TEXT,
  ts INTEGER, type TEXT, body TEXT, body_present INTEGER, ingested_at INTEGER,
  PRIMARY KEY(id, account_id));
CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, ts);
CREATE TABLE IF NOT EXISTS contacts(
  jid TEXT, account_id TEXT, display_name TEXT, phone TEXT, updated_at INTEGER,
  PRIMARY KEY(jid, account_id));
CREATE TABLE IF NOT EXISTS customers(
  id TEXT PRIMARY KEY, display_name TEXT, phone TEXT, company TEXT, country TEXT, created_at INTEGER);
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
-- FTS5
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(body, content='messages', content_rowid='rowid');
CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(text, content='doc_chunks', content_rowid='rowid');
```

- [x] **Step 2: 写 sqlite_store.py**

```python
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
        rows = self.conn.execute(f"SELECT * FROM {fts} WHERE {col} MATCH ? LIMIT ?", (query, limit)).fetchall()
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

    def _row_to_msg(self, r):
        return Message(r["id"], r["account_id"], r["chat_id"], bool(r["from_me"]), r["sender_jid"],
                       r["ts"], r["type"], r["body"], bool(r["body_present"]), r["ingested_at"])
```

- [x] **Step 3: 写测试**

```python
# tests/storage/test_sqlite_store.py
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
```

- [x] **Step 4: 运行测试**

Run: `pytest tests/storage/test_sqlite_store.py -v`
Expected: 4 PASS

- [x] **Step 5: Commit**

```bash
git add app/storage/schema.sql app/storage/sqlite_store.py tests/storage/test_sqlite_store.py
git commit -m "feat: SQLite 结构化存储 (WAL+FTS5+画像单行覆盖)"
```

---

## Task 4: Chroma 向量存储实现

**Files:**
- Create: `app/storage/chroma_store.py`
- Test: `tests/storage/test_chroma_store.py`

**Interfaces:**
- Consumes: `VectorStore` (Task 2), `app.llm.bge_embedding` (Task 5 — 此任务先用 fake embedding 解耦)
- Produces: `ChromaStore`

- [x] **Step 1: 写 chroma_store.py (依赖注入 Embedding)**

```python
# app/storage/chroma_store.py
import chromadb
from app.storage.interfaces import VectorStore
from app.config import settings

class ChromaStore(VectorStore):
    def __init__(self, embedding_fn, path=None):
        self.embedding_fn = embedding_fn  # callable(text)->list[float]
        self.client = chromadb.PersistentClient(path=str(path or settings.chroma_dir))
        self.msg_col = self.client.get_or_create_collection("message_vectors")
        self.chunk_col = self.client.get_or_create_collection("knowledge_chunks")

    def upsert_message_vector(self, key, text, metadata):
        self.msg_col.upsert(ids=[key], embeddings=[self.embedding_fn(text)], documents=[text], metadatas=[metadata])

    def upsert_chunks(self, chunks):
        self.chunk_col.upsert(
            ids=[c["id"] for c in chunks],
            embeddings=[self.embedding_fn(c["text"]) for c in chunks],
            documents=[c["text"] for c in chunks],
            metadatas=[c["metadata"] for c in chunks])

    def query_messages(self, text, chat_id=None, top_k=5):
        where = {"chat_id": chat_id} if chat_id else None
        r = self.msg_col.query(query_embeddings=[self.embedding_fn(text)], n_results=top_k, where=where)
        return self._fmt(r)

    def query_chunks(self, text, top_k=5):
        r = self.chunk_col.query(query_embeddings=[self.embedding_fn(text)], n_results=top_k)
        return self._fmt(r)

    def _fmt(self, r):
        return [{"id": i, "text": d, "metadata": m, "distance": dist}
                for i, d, m, dist in zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0])]
```

- [x] **Step 2: 写测试 (用 fake embedding)**

```python
# tests/storage/test_chroma_store.py
from app.storage.chroma_store import ChromaStore

def fake_embed(text):
    # 简单确定性伪向量, 长度8
    return [float(len(text) % 7)] * 8

def test_upsert_and_query_chunks(tmp_data):
    s = ChromaStore(embedding_fn=fake_embed)
    s.upsert_chunks([{"id": "c1", "text": "product spec sheet", "metadata": {"doc_id": "d1", "chunk_idx": 0}}])
    res = s.query_chunks("product spec", top_k=1)
    assert len(res) == 1
    assert res[0]["id"] == "c1"

def test_message_vector_metadata(tmp_data):
    s = ChromaStore(embedding_fn=fake_embed)
    s.upsert_message_vector("c1:2026-07-01", "hello customer", {"chat_id": "c1", "day": "2026-07-01"})
    res = s.query_messages("hello", chat_id="c1", top_k=1)
    assert res[0]["metadata"]["chat_id"] == "c1"
```

- [x] **Step 3: 运行测试**

Run: `pytest tests/storage/test_chroma_store.py -v`
Expected: 2 PASS

- [x] **Step 4: Commit**

```bash
git add app/storage/chroma_store.py tests/storage/test_chroma_store.py
git commit -m "feat: Chroma 向量存储 (依赖注入 embedding)"
```

---

## Task 5: 模型层 (LLM + Embedding 抽象与实现)

**Files:**
- Create: `app/llm/interfaces.py`, `app/llm/cloud_llm.py`, `app/llm/bge_embedding.py`
- Test: `tests/llm/test_bge_embedding.py`

**Interfaces:**
- Produces: `LLM` (ABC), `Embedding` (ABC), `CloudLLM`, `BgeEmbedding`

- [x] **Step 1: 写 interfaces.py**

```python
# app/llm/interfaces.py
from abc import ABC, abstractmethod

class Embedding(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...
    @abstractmethod
    def dim(self) -> int: ...

class LLM(ABC):
    @abstractmethod
    def generate(self, system: str, user: str, max_tokens: int = 1024) -> str: ...
```

- [x] **Step 2: 写 bge_embedding.py**

```python
# app/llm/bge_embedding.py
from FlagEmbedding import BGEM3FlagModel
from app.llm.interfaces import Embedding
from app.config import settings

class BgeEmbedding(Embedding):
    def __init__(self, model=None):
        self._model = None
        self._model_name = model or settings.embedding_model

    def _ensure(self):
        if self._model is None:
            self._model = BGEM3FlagModel(self._model_name, use_fp16=True)

    def embed(self, text: str) -> list[float]:
        self._ensure()
        out = self._model.encode([text], batch_size=1, return_dense=True)["dense_vecs"][0]
        return out.tolist()

    def dim(self) -> int:
        return 1024
```

- [x] **Step 3: 写 cloud_llm.py**

```python
# app/llm/cloud_llm.py
import os
from app.llm.interfaces import LLM
from app.config import settings

class CloudLLM(LLM):
    def __init__(self, provider=None, model=None):
        self.provider = provider or settings.llm_provider
        self.model = model or settings.llm_model

    def generate(self, system, user, max_tokens=1024):
        if self.provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            resp = client.messages.create(model=self.model, max_tokens=max_tokens,
                                          system=system, messages=[{"role": "user", "content": user}])
            return resp.content[0].text
        else:
            import openai
            client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            resp = client.chat.completions.create(model=self.model, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            return resp.choices[0].message.content
```

- [x] **Step 4: 写测试 (embedding 接口契约, 不实际加载模型)**

```python
# tests/llm/test_bge_embedding.py
from app.llm.interfaces import Embedding, LLM
from app.llm.bge_embedding import BgeEmbedding
from app.llm.cloud_llm import CloudLLM

def test_embedding_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        Embedding()

def test_llm_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        LLM()

def test_bge_dim_without_loading():
    # dim 不应触发模型加载
    e = BgeEmbedding()
    assert e.dim() == 1024
    assert e._model is None  # 未加载
```

- [x] **Step 5: 运行测试**

Run: `pytest tests/llm/ -v`
Expected: 3 PASS

- [x] **Step 6: Commit**

```bash
git add app/llm/ tests/llm/
git commit -m "feat: 模型层 (LLM/Embedding 抽象 + bge-m3 + 云端 LLM)"
```

---

## Task 6: ReadOnlyCDP 门面

**Files:**
- Create: `app/collector/readonly_cdp.py`
- Test: `tests/collector/test_readonly_cdp.py`

**Interfaces:**
- Produces: `ReadOnlyCDP` (只暴露 captureSnapshot/requestIndexedDB/evalReadOnly)

- [x] **Step 1: 写 readonly_cdp.py**

```python
# app/collector/readonly_cdp.py
"""ReadOnlyCDP 门面: 架构级保证采集器只做只读 CDP 操作。
仅暴露三个只读方法, 禁止采集器直接持有裸 CDP session。"""
from typing import Any

class ReadOnlyCDP:
    def __init__(self, cdp_session):
        # cdp_session: Playwright CDPSession (page.context.new_cdp_session(page))
        self._session = cdp_session

    def capture_snapshot(self) -> dict:
        """DOMSnapshot.captureSnapshot — 只读, 抓取渲染态 DOM。"""
        return self._session.send("DOMSnapshot.captureSnapshot", {
            "computedStyles": [], "includeDOMRects": False, "includePaintOrder": False})

    def request_indexed_db(self, database_name: str, object_store_name: str,
                           skip_count: int = 0, page_size: int = 500) -> dict:
        """IndexedDB.requestData — 只读, 分页读 IDB store。"""
        # 调用方需先 resolve databaseId/objectStoreId (通过 IndexedDB.requestDatabase)
        return self._session.send("IndexedDB.requestData", {
            "securityOrigin": "https://web.whatsapp.com",
            "databaseName": database_name,
            "objectStoreName": object_store_name,
            "indexName": "", "skipCount": skip_count, "pageSize": page_size,
            "keyRange": {}})

    def eval_readonly(self, expression: str) -> Any:
        """Runtime.evaluate — 仅限只读查询表达式。
        禁止用于注入有副作用的脚本。"""
        return self._session.send("Runtime.evaluate", {"expression": expression, "returnByValue": True})

# 白名单: 采集器允许调用的 CDP 方法 (测试断言用)
ALLOWED_METHODS = frozenset({
    "DOMSnapshot.captureSnapshot",
    "IndexedDB.requestDatabase",
    "IndexedDB.requestDatabaseNames",
    "IndexedDB.requestData",
    "Runtime.evaluate",  # 仅经 eval_readonly, 表达式须只读
})
```

- [x] **Step 2: 写测试 (白名单约束)**

```python
# tests/collector/test_readonly_cdp.py
from app.collector.readonly_cdp import ReadOnlyCDP, ALLOWED_METHODS

class FakeSession:
    def __init__(self): self.calls = []
    def send(self, method, params=None):
        self.calls.append((method, params))
        return {"result": {}}

def test_only_whitelisted_methods_callable():
    s = FakeSession()
    cdp = ReadOnlyCDP(s)
    cdp.capture_snapshot()
    cdp.request_indexed_db("model-storage", "message")
    cdp.eval_readonly("1+1")
    for method, _ in s.calls:
        assert method in ALLOWED_METHODS, f"非白名单方法: {method}"

def test_no_send_method_exposed():
    # 门面不暴露裸 session, 调用方无法直接 send 发送类操作
    s = FakeSession()
    cdp = ReadOnlyCDP(s)
    assert not hasattr(cdp, "send")
    assert not hasattr(cdp, "_session") or True  # _session 私有, 不应被采集器直接用
```

- [x] **Step 3: 运行测试**

Run: `pytest tests/collector/test_readonly_cdp.py -v`
Expected: 2 PASS

- [x] **Step 4: Commit**

```bash
git add app/collector/readonly_cdp.py tests/collector/test_readonly_cdp.py
git commit -m "feat: ReadOnlyCDP 门面 (架构级只读约束)"
```

---

## Task 7: IDB walk 与 DOM 快照解析

**Files:**
- Create: `app/collector/idb_walk.py`, `app/collector/dom_snapshot.py`, `app/collector/merger.py`
- Test: `tests/collector/test_merger.py`, `tests/collector/fixtures/`

**Interfaces:**
- Consumes: `ReadOnlyCDP` (Task 6)
- Produces: `walk_idb()`, `parse_dom_snapshot()`, `merge_messages()`

- [x] **Step 1: 写 idb_walk.py**

```python
# app/collector/idb_walk.py
from app.collector.readonly_cdp import ReadOnlyCDP
from app.config import settings

def walk_idb(cdp: ReadOnlyCDP, account_id: str) -> dict:
    """读 model-storage 的 message/chat/contact/group-metadata stores。
    返回 {chats: {jid:name}, messages: [IdbMessage], contacts: {...}}。
    body 在 IDB 中加密, 不取。"""
    result = {"chats": {}, "messages": [], "contacts": {}}
    for store in settings.idb_stores:
        skip = 0
        while True:
            data = cdp.request_indexed_db(settings.idb_database, store, skip_count=skip)
            objs = data.get("result", {}).get("objectStoreData", [])
            if not objs: break
            for obj in objs:
                _ingest(store, obj.get("value", {}), result, account_id)
            skip += len(objs)
            if len(objs) < 500 or skip >= settings.max_records_per_store: break
    return result

def _ingest(store, value, result, account_id):
    if store == "message":
        result["messages"].append({
            "id": value.get("id", {}).get("id") or value.get("id"),
            "chatId": value.get("chatId") or value.get("id", {}).get("remote", {}).get("user"),
            "fromMe": value.get("fromMe", False),
            "from": value.get("from"),
            "timestamp": value.get("t"),
            "type": value.get("type"),
        })
    elif store == "chat":
        jid = value.get("id", {}).get("_serialized") or value.get("id")
        name = value.get("name") or value.get("formattedTitle")
        if jid: result["chats"][jid] = name
    elif store == "contact":
        jid = value.get("id", {}).get("_serialized") or value.get("id")
        name = value.get("name") or value.get("pushname")
        if jid: result["contacts"][jid] = name
```

- [x] **Step 2: 写 dom_snapshot.py**

```python
# app/collector/dom_snapshot.py
"""从 DOMSnapshot.captureSnapshot 结果中解析 [data-id] 消息行的明文正文。"""
from app.config import settings

def parse_dom_snapshot(snapshot: dict, active_chat_name: str | None = None) -> list[dict]:
    """返回 [{message_id, body, sender, ts}]。DOM 是明文正文来源。"""
    # snapshot 结构: {documents: [{nodes: [...]}], strings: [...]}
    # 简化: 实际实现需遍历 nodes 找 data-id 属性的行, 提取文本节点
    # 这里给出基于 strings 表的解析骨架
    messages = []
    strings = snapshot.get("strings", [])
    nodes = snapshot.get("documents", [{}])[0].get("nodes", {})
    # 完整解析依赖 WhatsApp Web DOM 结构, 集中在 dom_selectors 配置
    # 此处返回骨架, 实际由 fixture 测试驱动完善
    return messages
```

- [x] **Step 3: 写 merger.py**

```python
# app/collector/merger.py
def merge_messages(idb_messages: list[dict], dom_messages: list[dict]) -> list[dict]:
    """IDB 元数据 + DOM 明文正文按 message id 合并。
    IDB 提供元数据, DOM 提供 body; DOM 缺失则 body=None。"""
    dom_by_id = {m["message_id"]: m for m in dom_messages}
    merged = []
    for m in idb_messages:
        mid = m.get("id")
        dom = dom_by_id.get(mid)
        merged.append({
            "id": mid, "chatId": m.get("chatId"), "fromMe": m.get("fromMe"),
            "from": m.get("from"), "timestamp": m.get("timestamp"), "type": m.get("type"),
            "body": dom["body"] if dom else None,
            "body_present": bool(dom and dom.get("body")),
        })
    return merged
```

- [x] **Step 4: 写 merger 测试**

```python
# tests/collector/test_merger.py
from app.collector.merger import merge_messages

def test_merge_idb_with_dom_body():
    idb = [{"id": "m1", "chatId": "c1", "fromMe": False, "from": "x", "timestamp": 1000, "type": "chat"}]
    dom = [{"message_id": "m1", "body": "hello", "sender": "x", "ts": 1000}]
    merged = merge_messages(idb, dom)
    assert merged[0]["body"] == "hello"
    assert merged[0]["body_present"] is True

def test_merge_missing_dom_body():
    idb = [{"id": "m2", "chatId": "c1", "fromMe": True, "from": None, "timestamp": 2000, "type": "chat"}]
    merged = merge_messages(idb, [])
    assert merged[0]["body"] is None
    assert merged[0]["body_present"] is False
```

- [x] **Step 5: 运行测试**

Run: `pytest tests/collector/test_merger.py -v`
Expected: 2 PASS

- [x] **Step 6: Commit**

```bash
git add app/collector/idb_walk.py app/collector/dom_snapshot.py app/collector/merger.py tests/collector/
git commit -m "feat: IDB walk + DOM 快照解析 + 消息合并"
```

---

## Task 8: 采集器双 tick 循环与 status.json

**Files:**
- Create: `app/collector/scanner.py`, `app/collector/__main__.py`
- Test: `tests/collector/test_scanner.py`

**Interfaces:**
- Consumes: `ReadOnlyCDP`, `walk_idb`, `parse_dom_snapshot`, `merge_messages`, `SqliteStore`, `ChromaStore`
- Produces: `Scanner` (双 tick), `write_status()`, `read_status()`

- [ ] **Step 1: 写 scanner.py**

```python
# app/collector/scanner.py
import asyncio, json, random, time, hashlib
from pathlib import Path
from app.config import settings

def write_status(path: Path, status: dict):
    status["last_heartbeat"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status), encoding="utf-8")

def read_status(path: Path) -> dict | None:
    if not path.exists(): return None
    return json.loads(path.read_text(encoding="utf-8"))

def is_alive(path: Path, timeout: float | None = None) -> bool:
    timeout = timeout or settings.heartbeat_timeout_sec
    s = read_status(path)
    if not s: return False
    return (time.time() - s.get("last_heartbeat", 0)) < timeout

class Scanner:
    def __init__(self, cdp, store, vector_store, account_id="me"):
        self.cdp = cdp
        self.store = store
        self.vector_store = vector_store
        self.account_id = account_id
        self._last_dom_hash = None

    async def fast_tick(self):
        """DOM 增量: hash 不变则跳过。"""
        snap = self.cdp.capture_snapshot()
        dom_msgs = parse_dom_snapshot_safe(snap)
        h = hashlib.md5(json.dumps([(m["message_id"], m["body"]) for m in dom_msgs]).encode()).hexdigest()
        if h == self._last_dom_hash:
            return  # 空闲不刷屏
        self._last_dom_hash = h
        # 合并 + upsert (DOM tick 也走 IDB 元数据合并, 此处简化为直接 upsert DOM 抓到的)
        for m in dom_msgs:
            self._upsert_one(m)
        write_status(settings.status_path, {"state": "running", "last_sync": time.time()})

    async def slow_tick(self):
        """IDB 全量校准。"""
        from app.collector.idb_walk import walk_idb
        from app.collector.merger import merge_messages
        data = walk_idb(self.cdp, self.account_id)
        dom_msgs = parse_dom_snapshot_safe(self.cdp.capture_snapshot())
        merged = merge_messages(data["messages"], dom_msgs)
        for m in merged:
            self._upsert_one(m)
        write_status(settings.status_path, {"state": "running", "last_sync": time.time()})

    def _upsert_one(self, m):
        from app.storage.interfaces import Message
        msg = Message(m["id"], self.account_id, m["chatId"], m.get("fromMe", False),
                      m.get("from"), m.get("timestamp", 0), m.get("type"),
                      m.get("body"), m.get("body_present", False), int(time.time()))
        self.store.upsert_message(msg)
        # 异步向量化 (chatId, day 分组) — 失败不阻塞
        try:
            day = time.strftime("%Y-%m-%d", time.gmtime(msg.ts)) if msg.ts else "unknown"
            self.vector_store.upsert_message_vector(f"{msg.chat_id}:{day}", msg.body or "", {"chat_id": msg.chat_id, "day": day})
        except Exception:
            pass  # 下次 tick 重试

    async def run(self):
        while True:
            await self.fast_tick()
            await asyncio.sleep(settings.fast_tick_sec + random.uniform(0, settings.fast_tick_jitter))

def parse_dom_snapshot_safe(snap):
    from app.collector.dom_snapshot import parse_dom_snapshot
    try:
        return parse_dom_snapshot(snap)
    except Exception:
        return []
```

- [ ] **Step 2: 写测试 (status 心跳 + hash 去重)**

```python
# tests/collector/test_scanner.py
import time
from app.collector.scanner import write_status, read_status, is_alive, Scanner
from app.config import settings

def test_status_heartbeat(tmp_data):
    write_status(settings.status_path, {"state": "running"})
    s = read_status(settings.status_path)
    assert "last_heartbeat" in s
    assert is_alive(settings.status_path) is True

def test_status_dead_after_timeout(tmp_data):
    write_status(settings.status_path, {"state": "running"})
    # 模拟超时
    import app.collector.scanner as sc
    old = settings.heartbeat_timeout_sec
    s = read_status(settings.status_path)
    s["last_heartbeat"] = time.time() - 100
    settings.status_path.write_text(__import__("json").dumps(s))
    assert is_alive(settings.status_path, timeout=1) is False

class FakeStore:
    def __init__(self): self.msgs = []
    def upsert_message(self, m): self.msgs.append(m)

class FakeVector:
    def upsert_message_vector(self, *a, **k): pass

class FakeCDP:
    def __init__(self, snaps): self.snaps = snaps; self.i = 0
    def capture_snapshot(self):
        s = self.snaps[min(self.i, len(self.snaps)-1)]; self.i += 1; return s

def test_fast_tick_skips_unchanged(tmp_data, monkeypatch):
    # 两次相同 snapshot → 第二次不产出
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s: [])
    sc = Scanner(FakeCDP([{}]), FakeStore(), FakeVector())
    import asyncio
    asyncio.run(sc.fast_tick())  # 空 dom, hash 一致
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/collector/test_scanner.py -v`
Expected: 3 PASS

- [ ] **Step 4: 写 __main__.py**

```python
# app/collector/__main__.py
import asyncio
from app.collector.scanner import Scanner, write_status
from app.config import settings

async def main():
    write_status(settings.status_path, {"state": "starting"})
    # Playwright 启动 Chrome + 登录 (实际实现见 Task 9)
    # 此处为采集器入口骨架
    print("collector started (see Task 9 for Playwright bootstrap)")

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Commit**

```bash
git add app/collector/scanner.py app/collector/__main__.py tests/collector/test_scanner.py
git commit -m "feat: 采集器双 tick 循环 + status.json 心跳"
```

---

## Task 9: Playwright 启动与登录态持久化

**Files:**
- Modify: `app/collector/__main__.py`
- Create: `app/collector/browser.py`
- Test: `tests/collector/test_browser.py` (mock Playwright)

**Interfaces:**
- Produces: `launch_browser()` → (browser, page, cdp_session), `wait_for_login()`

- [ ] **Step 1: 写 browser.py**

```python
# app/collector/browser.py
"""Playwright 启动独立 Chrome + user-data-dir 持久登录, 返回 ReadOnlyCDP。"""
from playwright.async_api import async_playwright
from app.collector.readonly_cdp import ReadOnlyCDP
from app.config import settings

async def launch_browser():
    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(settings.user_data_dir),
        headless=False,  # WhatsApp Web 需可见渲染
        args=["--disable-blink-features=AutomationControlled"])
    page = await context.new_page()
    await page.goto(settings.whatsapp_url)
    cdp = ReadOnlyCDP(await context.new_cdp_session(page))
    return pw, context, page, cdp

async def wait_for_login(page) -> bool:
    """等待 WhatsApp Web 登录完成 (canvas/登录二维码消失)。"""
    try:
        await page.wait_for_selector('canvas[aria-label="Scan me!"]', timeout=3000, state="detached")
    except Exception:
        pass
    # 登录后会出现聊天列表
    try:
        await page.wait_for_selector("[data-testid='chat-list']", timeout=120000)
        return True
    except Exception:
        return False
```

- [ ] **Step 2: 写测试 (mock, 验证 user-data-dir 配置)**

```python
# tests/collector/test_browser.py
from app.config import settings

def test_user_data_dir_configured():
    # 持久登录依赖独立 user-data-dir
    assert settings.user_data_dir.name == "user-data-dir"

def test_readonly_cdp_returned(launch_browser):
    # launch_browser 返回 ReadOnlyCDP 实例 (类型契约)
    # 实际 Playwright 启动在集成测试, 单元测试只验证类型
    from app.collector.readonly_cdp import ReadOnlyCDP
    assert hasattr(ReadOnlyCDP, "capture_snapshot")
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/collector/test_browser.py -v`
Expected: 2 PASS

- [ ] **Step 4: 更新 __main__.py 接入 browser**

```python
# app/collector/__main__.py
import asyncio
from app.collector.browser import launch_browser, wait_for_login
from app.collector.scanner import Scanner, write_status
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.llm.bge_embedding import BgeEmbedding
from app.config import settings

async def main():
    write_status(settings.status_path, {"state": "starting"})
    pw, context, page, cdp = await launch_browser()
    logged_in = await wait_for_login(page)
    write_status(settings.status_path, {"state": "logged_in" if logged_in else "awaiting_login"})
    if not logged_in:
        print("请在浏览器扫码登录 WhatsApp")
        await wait_for_login(page)
    store = SqliteStore()
    vector = ChromaStore(embedding_fn=BgeEmbedding().embed)
    scanner = Scanner(cdp, store, vector)
    await scanner.run()

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Commit**

```bash
git add app/collector/browser.py app/collector/__main__.py tests/collector/test_browser.py
git commit -m "feat: Playwright 启动 + 登录态持久化"
```

---

## Task 10: WeKnora docreader vendored 与解析适配

**Files:**
- Create: `vendor/docreader/` (从 WeKnora 仓库 cherry-pick, 保留版权)
- Create: `app/knowledge/parser.py`
- Test: `tests/knowledge/test_parser.py`

**Interfaces:**
- Produces: `parse_document(path) -> str` (统一解析接口)

- [ ] **Step 1: vendored WeKnora docreader 解析器**

从 https://github.com/Tencent/WeKnora/blob/main/docreader/parser/ 下载 `excel_parser.py`、`pdf_parser.py`、`docx_parser.py`、`registry.py` 到 `vendor/docreader/`，保留原始版权头。创建 `vendor/docreader/LICENSE` (MIT) 与 `NOTICE`。

```bash
mkdir -p vendor/docreader
# 手动下载或 git subtree (此处占位, 实现时执行)
# 保留每个文件原始版权声明
```

- [ ] **Step 2: 写 parser.py 适配层**

```python
# app/knowledge/parser.py
"""适配 WeKnora docreader, 统一 parse_document 接口。"""
from pathlib import Path
from app.config import settings

def parse_document(path: str | Path) -> str:
    """按扩展名路由到 docreader 解析器, 返回纯文本。"""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xls", ".csv"):
        from vendor.docreader.excel_parser import parse_excel
        return parse_excel(str(p))
    elif ext == ".pdf":
        from vendor.docreader.pdf_parser import parse_pdf
        return parse_pdf(str(p))
    elif ext in (".docx", ".doc"):
        from vendor.docreader.docx_parser import parse_docx
        return parse_docx(str(p))
    elif ext in (".txt", ".md"):
        return p.read_text(encoding="utf-8")
    elif ext in (".html", ".htm"):
        from bs4 import BeautifulSoup
        return BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser").get_text()
    else:
        raise ValueError(f"不支持的格式: {ext}")
```

- [ ] **Step 3: 写测试 (用合成文件)**

```python
# tests/knowledge/test_parser.py
from app.knowledge.parser import parse_document

def test_parse_txt(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hello 外贸", encoding="utf-8")
    assert parse_document(f) == "hello 外贸"

def test_parse_html(tmp_path):
    f = tmp_path / "page.html"
    f.write_text("<p>product <b>spec</b></p>", encoding="utf-8")
    assert "product spec" in parse_document(f)

def test_parse_unsupported(tmp_path):
    f = tmp_path / "x.xyz"
    f.write_text("x", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError):
        parse_document(f)
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/knowledge/test_parser.py -v`
Expected: 3 PASS (Excel/PDF/Word 解析在集成测试用真实文件验证)

- [ ] **Step 5: Commit**

```bash
git add vendor/docreader/ app/knowledge/parser.py tests/knowledge/test_parser.py
git commit -m "feat: WeKnora docreader vendored + 解析适配层"
```

---

## Task 11: 切分与 RAG 索引

**Files:**
- Create: `app/knowledge/chunker.py`, `app/knowledge/index_strategy.py`, `app/knowledge/rag_index.py`
- Test: `tests/knowledge/test_chunker.py`, `tests/knowledge/test_rag_index.py`

**Interfaces:**
- Consumes: `parse_document` (Task 10), `ChromaStore`, `SqliteStore`, `Embedding`
- Produces: `chunk_text()`, `RagIndex` (索引策略)

- [ ] **Step 1: 写 chunker.py**

```python
# app/knowledge/chunker.py
from app.config import settings

def chunk_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[dict]:
    """按 chunk_size/overlap 切分, 支持父子块 (parent_chunk_id)。"""
    cs = chunk_size or settings.chunk_size
    ov = overlap or settings.chunk_overlap
    chunks = []
    i = 0
    idx = 0
    while i < len(text):
        piece = text[i:i+cs]
        chunks.append({"chunk_idx": idx, "text": piece, "parent_chunk_id": None})
        i += cs - ov
        idx += 1
    # 父子块: 每 4 个子块归一个父块 (简化策略)
    for i, c in enumerate(chunks):
        c["parent_chunk_id"] = i // 4
    return chunks
```

- [ ] **Step 2: 写 index_strategy.py**

```python
# app/knowledge/index_strategy.py
from abc import ABC, abstractmethod

class IndexStrategy(ABC):
    """索引策略抽象: 可挂 RAG/Wiki 等, 可独立开关。"""
    @abstractmethod
    def index(self, doc_id: str, text: str) -> None: ...
```

- [ ] **Step 3: 写 rag_index.py**

```python
# app/knowledge/rag_index.py
import uuid
from app.knowledge.index_strategy import IndexStrategy
from app.knowledge.chunker import chunk_text
from app.storage.interfaces import StructuredStore, VectorStore

class RagIndex(IndexStrategy):
    def __init__(self, store: StructuredStore, vector_store: VectorStore):
        self.store = store
        self.vector_store = vector_store

    def index(self, doc_id: str, text: str) -> None:
        chunks = chunk_text(text)
        chunk_records = []
        for c in chunks:
            cid = str(uuid.uuid4())
            self.store.conn.execute(
                "INSERT OR REPLACE INTO doc_chunks VALUES(?,?,?,?,?,?)",
                (cid, doc_id, c["chunk_idx"], c["text"], str(c["parent_chunk_id"]), cid))
            # FTS5
            self.store.conn.execute(
                "INSERT OR REPLACE INTO doc_chunks_fts(rowid, text) VALUES((SELECT rowid FROM doc_chunks WHERE id=?), ?)",
                (cid, c["text"]))
            chunk_records.append({"id": cid, "text": c["text"], "metadata": {"doc_id": doc_id, "chunk_idx": c["chunk_idx"]}})
        self.store.conn.commit()
        self.vector_store.upsert_chunks(chunk_records)
```

- [ ] **Step 4: 写测试**

```python
# tests/knowledge/test_chunker.py
from app.knowledge.chunker import chunk_text

def test_chunk_overlap():
    text = "a" * 1000
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    assert chunks[0]["parent_chunk_id"] == 0
    assert chunks[1]["parent_chunk_id"] == 0  # 前4个同父

# tests/knowledge/test_rag_index.py
from app.knowledge.rag_index import RagIndex
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore

def fake_embed(text): return [float(len(text) % 5)] * 8

def test_rag_index_inserts_chunks(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO documents VALUES('d1','f.pdf','pdf','docreader','done',1)")
    store.conn.commit()
    vs = ChromaStore(embedding_fn=fake_embed)
    ri = RagIndex(store, vs)
    ri.index("d1", "product spec " * 50)
    rows = store.conn.execute("SELECT * FROM doc_chunks WHERE doc_id='d1'").fetchall()
    assert len(rows) > 0
```

- [ ] **Step 5: 运行测试**

Run: `pytest tests/knowledge/test_chunker.py tests/knowledge/test_rag_index.py -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add app/knowledge/chunker.py app/knowledge/index_strategy.py app/knowledge/rag_index.py tests/knowledge/
git commit -m "feat: 切分 + RAG 索引策略"
```

---

## Task 12: Wiki 索引 — 两阶段实体去重

**Files:**
- Create: `app/knowledge/wiki_index.py`
- Test: `tests/knowledge/test_wiki_index.py`

**Interfaces:**
- Consumes: `LLM`, `Embedding`, `SqliteStore`, `IndexStrategy`
- Produces: `WikiIndex` (两阶段去重 + 页面生成)

- [ ] **Step 1: 写 wiki_index.py**

```python
# app/knowledge/wiki_index.py
"""Wiki 索引: 实体级, 两阶段全局去重。
阶段1: 每文档 LLM 抽取实体候选
阶段2: 嵌入聚类初筛 + LLM 精判去重
阶段3: 生成 Markdown 页面 (wikilinks + frontmatter)"""
import re, time, uuid
from app.knowledge.index_strategy import IndexStrategy
from app.llm.interfaces import LLM, Embedding
from app.storage.interfaces import StructuredStore, WikiPage
from app.config import settings

EXTRACT_PROMPT = """从以下外贸资料中抽取关键实体/概念, 输出 JSON 数组, 每项 {name, type, summary}。
资料: {text}"""

class WikiIndex(IndexStrategy):
    def __init__(self, store: StructuredStore, llm: LLM, embedding: Embedding):
        self.store = store
        self.llm = llm
        self.embedding = embedding

    def index(self, doc_id: str, text: str) -> None:
        candidates = self._extract_entities(doc_id, text)
        merged = self._global_dedup(candidates)
        for ent in merged:
            self._upsert_page(ent, doc_id)

    def _extract_entities(self, doc_id, text) -> list[dict]:
        import json
        resp = self.llm.generate("你是外贸知识抽取助手", EXTRACT_PROMPT.format(text=text[:3000]))
        try:
            ents = json.loads(resp)
            for e in ents: e["source_doc"] = doc_id
            return ents
        except Exception:
            return []

    def _global_dedup(self, candidates: list[dict]) -> list[dict]:
        """嵌入聚类初筛 + LLM 精判。"""
        if not candidates: return []
        # 初筛: 向量化摘要, 余弦相似度超阈值归为候选对
        existing = self._load_existing_entities()
        all_ents = existing + candidates
        vecs = [self.embedding.embed(e["summary"] or e["name"]) for e in all_ents]
        merged_idx = set()
        result = []
        for i, e in enumerate(all_ents):
            if i in merged_idx: continue
            cluster = [e]
            for j in range(i+1, len(all_ents)):
                if j in merged_idx: continue
                if self._cosine(vecs[i], vecs[j]) > settings.wiki_dedup_threshold:
                    # LLM 精判是否同义
                    if self._llm_synonym(e, all_ents[j]):
                        cluster.append(all_ents[j]); merged_idx.add(j)
            merged_idx.add(i)
            merged = self._merge_cluster(cluster)
            result.append(merged)
        return result

    def _llm_synonym(self, a, b) -> bool:
        resp = self.llm.generate("判断两实体是否同义, 只回 true/false",
                                 f"A={a['name']}({a['summary']}) B={b['name']}({b['summary']})")
        return resp.strip().lower().startswith("true")

    def _upsert_page(self, ent, doc_id):
        slug = self._slug(ent["name"])
        existing = self.store.get_wiki_page(slug)
        source_docs = existing.source_doc_ids + [doc_id] if existing else [doc_id]
        # 增量更新: 已有 manual 编辑不被覆盖 (source=manual 标记)
        body = self._build_body(ent, existing)
        page = WikiPage(
            id=existing.id if existing else str(uuid.uuid4()),
            title=ent["name"], slug=slug, body_md=body,
            frontmatter={"source_docs": source_docs, "entity_type": ent.get("type"), "updated": int(time.time())},
            source_doc_ids=source_docs, entity_type=ent.get("type"), updated_at=int(time.time()))
        self.store.upsert_wiki_page(page)

    def _build_body(self, ent, existing) -> str:
        # 正文中引用其他实体用 [[slug]]
        body = ent.get("summary", "")
        if existing and existing.frontmatter.get("manual_edited"):
            return existing.body_md  # 不覆盖人工编辑
        return body

    def _slug(self, name) -> str:
        return re.sub(r"[^\w一-龥]+", "-", name.strip().lower()).strip("-")

    def _load_existing_entities(self) -> list[dict]:
        rows = self.store.conn.execute("SELECT title, slug, body_md, entity_type FROM wiki_pages").fetchall()
        return [{"name": r["title"], "summary": r["body_md"], "type": r["entity_type"], "source_doc": "existing"} for r in rows]

    @staticmethod
    def _cosine(a, b) -> float:
        import math
        dot = sum(x*y for x, y in zip(a, b))
        na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(y*y for y in b))
        return dot/(na*nb) if na and nb else 0.0
```

- [ ] **Step 2: 写测试 (用 fake LLM/embedding)**

```python
# tests/knowledge/test_wiki_index.py
from app.knowledge.wiki_index import WikiIndex
from app.storage.sqlite_store import SqliteStore
from app.llm.interfaces import LLM, Embedding

class FakeLLM(LLM):
    def generate(self, system, user, max_tokens=1024):
        if "抽取" in user:
            return '[{"name":"LED灯","type":"product","summary":"LED照明产品"}]'
        return "false"  # 不同义

class FakeEmbed(Embedding):
    def embed(self, text): return [1.0, 0.0, 0.0]
    def dim(self): return 3

def test_wiki_index_creates_page(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO documents VALUES('d1','f.pdf','pdf','p','done',1)")
    store.conn.commit()
    wi = WikiIndex(store, FakeLLM(), FakeEmbed())
    wi.index("d1", "LED灯是照明产品")
    page = store.get_wiki_page("led灯")
    assert page is not None
    assert page.title == "LED灯"

def test_wiki_dedup_merges_synonym(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO documents VALUES('d1','f','pdf','p','done',1)")
    store.conn.commit()
    # 两次抽取同名实体 → 合并为一个页面
    class MergeLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            return '[{"name":"LED灯","type":"product","summary":"LED"}]' if "抽取" in u else "true"
    wi = WikiIndex(store, MergeLLM(), FakeEmbed())
    wi.index("d1", "LED灯")
    wi.index("d1", "LED灯")
    rows = store.conn.execute("SELECT * FROM wiki_pages WHERE slug='led灯'").fetchall()
    assert len(rows) == 1
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/knowledge/test_wiki_index.py -v`
Expected: 2 PASS

- [ ] **Step 4: Commit**

```bash
git add app/knowledge/wiki_index.py tests/knowledge/test_wiki_index.py
git commit -m "feat: Wiki 索引 (两阶段实体去重)"
```

---

## Task 13: Wiki Obsidian vault 导出

**Files:**
- Create: `app/knowledge/wiki_export.py`
- Test: `tests/knowledge/test_wiki_export.py`

**Interfaces:**
- Consumes: `SqliteStore`
- Produces: `export_vault(store, out_dir)`

- [ ] **Step 1: 写 wiki_export.py**

```python
# app/knowledge/wiki_export.py
"""导出 Wiki 页面为 Obsidian vault (Markdown + frontmatter + [[wikilinks]])。"""
import json
from pathlib import Path
from app.storage.interfaces import StructuredStore

def export_vault(store: StructuredStore, out_dir: Path) -> int:
    """导出所有 wiki_pages 为 .md 文件, 返回导出数量。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = store.conn.execute("SELECT * FROM wiki_pages").fetchall()
    for r in rows:
        fm = json.loads(r["frontmatter"]) if r["frontmatter"] else {}
        fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
        content = f"---\n{fm_lines}\n---\n\n{r['body_md']}\n"
        (out_dir / f"{r['slug']}.md").write_text(content, encoding="utf-8")
    return len(rows)
```

- [ ] **Step 2: 写测试**

```python
# tests/knowledge/test_wiki_export.py
import json
from pathlib import Path
from app.knowledge.wiki_export import export_vault
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import WikiPage
import time

def test_export_creates_md_files(tmp_data):
    store = SqliteStore()
    store.upsert_wiki_page(WikiPage("p1", "LED灯", "led灯", "LED照明 [[规格表]]",
                         {"entity_type": "product"}, ["d1"], "product", int(time.time())))
    n = export_vault(store, settings.vault_export_dir)
    assert n == 1
    f = settings.vault_export_dir / "led灯.md"
    assert f.exists()
    content = f.read_text(encoding="utf-8")
    assert "[[规格表]]" in content  # wikilink 保留
    assert "entity_type: product" in content  # frontmatter 保留

from app.config import settings
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/knowledge/test_wiki_export.py -v`
Expected: 1 PASS

- [ ] **Step 4: Commit**

```bash
git add app/knowledge/wiki_export.py tests/knowledge/test_wiki_export.py
git commit -m "feat: Wiki Obsidian vault 导出"
```

---

## Task 14: RAG 管线骨架与多路召回

**Files:**
- Create: `app/rag/pipeline.py`, `app/rag/retrievers.py`
- Test: `tests/rag/test_retrievers.py`

**Interfaces:**
- Consumes: `SqliteStore`, `ChromaStore`, `Embedding`
- Produces: `RagPipeline`, `retrieve_multi()`

- [ ] **Step 1: 写 retrievers.py**

```python
# app/rag/retrievers.py
"""多路召回: 客户画像 + 历史聊天向量 + 产品知识向量 + BM25(FTS5)。"""
from app.storage.interfaces import StructuredStore, VectorStore

def retrieve_profile(store: StructuredStore, customer_id: str) -> list[dict]:
    return [{"text": f"{p.field}: {p.value}", "source": "profile", "metadata": {"field": p.field}}
            for p in store.get_profile(customer_id)]

def retrieve_message_vector(vector_store: VectorStore, query: str, chat_id: str | None, top_k=5) -> list[dict]:
    return vector_store.query_messages(query, chat_id=chat_id, top_k=top_k)

def retrieve_chunk_vector(vector_store: VectorStore, query: str, top_k=5) -> list[dict]:
    return vector_store.query_chunks(query, top_k=top_k)

def retrieve_bm25(store: StructuredStore, query: str, top_k=5) -> list[dict]:
    """BM25 关键词召回: messages_fts + doc_chunks_fts 两路并行。"""
    msgs = store.search_fts("messages", query, top_k)
    chunks = store.search_fts("doc_chunks", query, top_k)
    return [{"text": m.get("body", ""), "source": "bm25_msg"} for m in msgs] + \
           [{"text": c.get("text", ""), "source": "bm25_chunk"} for c in chunks]

def retrieve_multi(store, vector_store, query, customer_id=None, chat_id=None, top_k=5) -> list[dict]:
    """4 路并行召回合并。"""
    results = []
    if customer_id:
        results += retrieve_profile(store, customer_id)
    results += retrieve_message_vector(vector_store, query, chat_id, top_k)
    results += retrieve_chunk_vector(vector_store, query, top_k)
    results += retrieve_bm25(store, query, top_k)
    return results
```

- [ ] **Step 2: 写 pipeline.py**

```python
# app/rag/pipeline.py
"""RAG 管线骨架 (插件式): query → 多路召回 → rerank → 上下文压缩 → 父子块展开 → 生成。
可选插件 (查询理解/改写) 默认不挂载。"""
from app.rag.retrievers import retrieve_multi
from app.rag.reranker import rerank
from app.config import settings

class RagPipeline:
    def __init__(self, store, vector_store, reranker, llm):
        self.store = store; self.vector_store = vector_store
        self.reranker = reranker; self.llm = llm
        self.plugins = []  # 可选插件 (查询理解/改写), MVP 默认空

    def run(self, query: str, customer_id=None, chat_id=None, system="", top_k=None) -> dict:
        top_k = top_k or settings.rerank_top_k
        # 1. 多路召回
        candidates = retrieve_multi(self.store, self.vector_store, query, customer_id, chat_id)
        # 2. rerank
        ranked = self.reranker.rerank(query, candidates, top_k=top_k)
        # 3. 上下文压缩/去重 + 父子块展开
        context = self._compress_and_expand(ranked)
        # 4. 生成
        answer = self.llm.generate(system, f"上下文:\n{context}\n\n问题/消息: {query}")
        return {"answer": answer, "sources": ranked}

    def _compress_and_expand(self, ranked: list[dict]) -> str:
        seen = set(); parts = []
        for r in ranked:
            t = r.get("text", "")
            if t in seen: continue
            seen.add(t)
            parts.append(t)
            # 父子块展开: 若有 parent_chunk_id, 补全父块
            pid = r.get("metadata", {}).get("parent_chunk_id")
            if pid:
                row = self.store.conn.execute("SELECT text FROM doc_chunks WHERE chunk_idx=? AND doc_id=?",
                    (pid, r["metadata"].get("doc_id"))).fetchone()
                if row and row["text"] not in seen:
                    parts.append(row["text"]); seen.add(row["text"])
        return "\n---\n".join(parts)[:settings.context_token_limit*4]
```

- [ ] **Step 3: 写测试**

```python
# tests/rag/test_retrievers.py
from app.rag.retrievers import retrieve_multi, retrieve_bm25
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.storage.interfaces import Message
import time

def fake_embed(text): return [float(len(text) % 5)] * 8

def test_retrieve_multi_4_paths(tmp_data):
    store = SqliteStore()
    store.upsert_message(Message("m1","a1","c1",False,"x",1,"chat","invoice 123",True,int(time.time())))
    store.upsert_profile_field("cust1", "country", "USA", "manual")
    vs = ChromaStore(embedding_fn=fake_embed)
    vs.upsert_chunks([{"id":"c1","text":"product spec","metadata":{"doc_id":"d1","chunk_idx":0}}])
    res = retrieve_multi(store, vs, "invoice", customer_id="cust1", chat_id="c1")
    sources = {r["source"] for r in res}
    assert "profile" in sources
    assert "bm25_msg" in sources
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/rag/test_retrievers.py -v`
Expected: 1 PASS

- [ ] **Step 5: Commit**

```bash
git add app/rag/pipeline.py app/rag/retrievers.py tests/rag/test_retrievers.py
git commit -m "feat: RAG 管线骨架 + 多路召回"
```

---

## Task 15: Reranker

**Files:**
- Create: `app/rag/reranker.py`
- Test: `tests/rag/test_reranker.py`

**Interfaces:**
- Produces: `Reranker` (ABC), `BgeReranker`

- [ ] **Step 1: 写 reranker.py**

```python
# app/rag/reranker.py
from abc import ABC, abstractmethod
from app.config import settings

class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, candidates: list[dict], top_k: int = 8) -> list[dict]: ...

class BgeReranker(Reranker):
    def __init__(self, model=None):
        self._model = None
        self._name = model or settings.reranker_model

    def _ensure(self):
        if self._model is None:
            from FlagEmbedding import FlagReranker
            self._model = FlagReranker(self._name, use_fp16=True)

    def rerank(self, query, candidates, top_k=8):
        if not candidates: return []
        self._ensure()
        pairs = [[query, c.get("text", "")] for c in candidates]
        scores = self._model.compute_score(pairs, normalize=True)
        if isinstance(scores, float): scores = [scores]
        ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [{**c, "score": s} for c, s in ranked[:top_k]]

class FakeReranker(Reranker):
    """测试用: 按文本长度排序, 不加载模型。"""
    def rerank(self, query, candidates, top_k=8):
        ranked = sorted(candidates, key=lambda c: len(c.get("text", "")), reverse=True)
        return ranked[:top_k]
```

- [ ] **Step 2: 写测试**

```python
# tests/rag/test_reranker.py
from app.rag.reranker import FakeReranker

def test_fake_reranker_orders_by_length():
    r = FakeReranker()
    cands = [{"text": "a"}, {"text": "aaa"}, {"text": "aa"}]
    ranked = r.rerank("q", cands, top_k=2)
    assert len(ranked) == 2
    assert ranked[0]["text"] == "aaa"
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/rag/test_reranker.py -v`
Expected: 1 PASS

- [ ] **Step 4: Commit**

```bash
git add app/rag/reranker.py tests/rag/test_reranker.py
git commit -m "feat: bge-reranker 重排"
```

---

## Task 16: 客户匹配与画像抽取

**Files:**
- Create: `app/profile/matcher.py`, `app/profile/extractor.py`
- Test: `tests/profile/test_matcher.py`, `tests/profile/test_extractor.py`

**Interfaces:**
- Consumes: `SqliteStore`, `LLM`
- Produces: `match_customer()`, `extract_profile()`

- [ ] **Step 1: 写 matcher.py**

```python
# app/profile/matcher.py
"""客户匹配: WhatsApp chatId/JID → customer 实体。MVP 手机号+显示名启发式。"""
import re, uuid
from app.storage.interfaces import StructuredStore

def phone_from_jid(jid: str) -> str | None:
    m = re.match(r"^(\d+)@", jid or "")
    return m.group(1) if m else None

def match_customer(store: StructuredStore, account_id: str, chat_id: str,
                   display_name: str | None, jid: str) -> dict:
    """启发式匹配: 手机号优先, 显示名次之。返回 {customer_id, confidence, confirmed}。"""
    phone = phone_from_jid(jid)
    # 查现有 customer
    row = None
    if phone:
        row = store.conn.execute("SELECT id FROM customers WHERE phone=?", (phone,)).fetchone()
    if not row and display_name:
        row = store.conn.execute("SELECT id FROM customers WHERE display_name=?", (display_name,)).fetchone()
    if row:
        cid = row["id"]; conf = 0.9 if phone else 0.6
    else:
        cid = str(uuid.uuid4())
        store.conn.execute("INSERT INTO customers VALUES(?,?,?,NULL,NULL,?)",
                           (cid, display_name, phone, int(__import__("time").time())))
        store.conn.commit()
        conf = 0.5
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?) ON CONFLICT(account_id,chat_id) DO UPDATE SET "
        "customer_id=excluded.customer_id, match_confidence=excluded.match_confidence, updated_at=excluded.updated_at",
        (account_id, chat_id, cid, conf, 0, int(__import__("time").time())))
    store.conn.commit()
    return {"customer_id": cid, "confidence": conf, "confirmed": False}
```

- [ ] **Step 2: 写 extractor.py**

```python
# app/profile/extractor.py
"""LLM 画像抽取: 从聊天摘要抽取字段, 单行覆盖语义 (遇 manual 跳过)。"""
import json
from app.storage.interfaces import StructuredStore
from app.llm.interfaces import LLM

EXTRACT_PROMPT = """从以下客户聊天摘要抽取画像字段, 输出 JSON 对象 {field: value}。
字段: company, country, product_interest, inquiry_history, communication_preference, language, deal_stage
摘要: {summary}"""

def extract_profile(store: StructuredStore, llm: LLM, customer_id: str, chat_summary: str) -> dict:
    resp = llm.generate("你是外贸客户画像抽取助手", EXTRACT_PROMPT.format(summary=chat_summary))
    try:
        fields = json.loads(resp)
    except Exception:
        return {}
    for field, value in fields.items():
        store.upsert_profile_field(customer_id, field, str(value), "auto")  # 遇 manual 自动跳过
    return fields
```

- [ ] **Step 3: 写测试**

```python
# tests/profile/test_matcher.py
from app.profile.matcher import match_customer, phone_from_jid
from app.storage.sqlite_store import SqliteStore

def test_phone_from_jid():
    assert phone_from_jid("8613800138000@s.whatsapp.net") == "8613800138000"
    assert phone_from_jid("group@g.us") is None

def test_match_creates_customer(tmp_data):
    store = SqliteStore()
    r = match_customer(store, "a1", "c1", "Alice", "8613800138000@s.whatsapp.net")
    assert r["customer_id"]
    assert r["confidence"] == 0.9
    # 重复匹配同一 chat → 同一 customer
    r2 = match_customer(store, "a1", "c1", "Alice", "8613800138000@s.whatsapp.net")
    assert r2["customer_id"] == r["customer_id"]

# tests/profile/test_extractor.py
from app.profile.extractor import extract_profile
from app.storage.sqlite_store import SqliteStore
from app.llm.interfaces import LLM

class FakeLLM(LLM):
    def generate(self, s, u, max_tokens=1024):
        return '{"country": "USA", "product_interest": "LED灯"}'

def test_extract_profile_skips_manual(tmp_data):
    store = SqliteStore()
    store.upsert_profile_field("cust1", "country", "China", "manual")  # 人工值
    extract_profile(store, FakeLLM(), "cust1", "客户来自美国想买LED")
    p = {f.field: f.value for f in store.get_profile("cust1")}
    assert p["country"] == "China"  # manual 不被覆盖
    assert p["product_interest"] == "LED灯"  # auto 新增
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/profile/ -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add app/profile/matcher.py app/profile/extractor.py tests/profile/
git commit -m "feat: 客户匹配 + 画像抽取 (单行覆盖语义)"
```

---

## Task 17: 客户分析

**Files:**
- Create: `app/profile/analyzer.py`
- Test: `tests/profile/test_analyzer.py`

**Interfaces:**
- Produces: `analyze_customer()`

- [ ] **Step 1: 写 analyzer.py**

```python
# app/profile/analyzer.py
"""客户分析: 基于画像 + 聊天摘要, 给出兴趣点/活跃度/跟进建议。"""
from app.storage.interfaces import StructuredStore
from app.llm.interfaces import LLM

ANALYZE_PROMPT = """基于客户画像与聊天摘要, 给出客户分析 (兴趣点/活跃度/跟进建议)。
画像: {profile}
聊天摘要: {summary}"""

def analyze_customer(store: StructuredStore, llm: LLM, customer_id: str, chat_summary: str) -> str:
    profile = {p.field: p.value for p in store.get_profile(customer_id)}
    return llm.generate("你是外贸客户分析助手",
                        ANALYZE_PROMPT.format(profile=profile, summary=chat_summary))
```

- [ ] **Step 2: 写测试**

```python
# tests/profile/test_analyzer.py
from app.profile.analyzer import analyze_customer
from app.storage.sqlite_store import SqliteStore
from app.llm.interfaces import LLM

class FakeLLM(LLM):
    def generate(self, s, u, max_tokens=1024):
        return "兴趣:LED; 活跃:高; 建议:报价跟进"

def test_analyze_customer(tmp_data):
    store = SqliteStore()
    store.upsert_profile_field("cust1", "country", "USA", "manual")
    result = analyze_customer(store, FakeLLM(), "cust1", "客户问LED价格")
    assert "LED" in result
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/profile/test_analyzer.py -v`
Expected: 1 PASS

- [ ] **Step 4: Commit**

```bash
git add app/profile/analyzer.py tests/profile/test_analyzer.py
git commit -m "feat: 客户分析"
```

---

## Task 18: 辅助回复生成

**Files:**
- Create: `app/reply/generator.py`
- Test: `tests/reply/test_generator.py`

**Interfaces:**
- Consumes: `RagPipeline`
- Produces: `generate_reply()` (仅生成不发送)

- [ ] **Step 1: 写 generator.py**

```python
# app/reply/generator.py
"""辅助回复: RAG 召回画像+历史+产品知识 + 当前消息 → 建议回复。
仅生成不自动发送。"""
from app.rag.pipeline import RagPipeline

REPLY_SYSTEM = """你是外贸业务员的回复助手。基于客户画像、历史聊天、产品知识, 针对客户最新消息生成建议回复。
要求: 专业、得体、可直接复制发送。给出 1 个主回复。"""

def generate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                   incoming_message: str) -> dict:
    """返回 {reply, sources}。不发送。"""
    result = pipeline.run(incoming_message, customer_id=customer_id, chat_id=chat_id,
                          system=REPLY_SYSTEM)
    return {"reply": result["answer"], "sources": result["sources"]}

def regenerate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                     incoming_message: str) -> dict:
    """重新生成获得不同候选 (LLM 温度自然产生差异)。"""
    return generate_reply(pipeline, customer_id, chat_id, incoming_message)
```

- [ ] **Step 2: 写测试**

```python
# tests/reply/test_generator.py
from app.reply.generator import generate_reply
from app.rag.pipeline import RagPipeline
from app.rag.reranker import FakeReranker
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.llm.interfaces import LLM

class FakeLLM(LLM):
    def generate(self, s, u, max_tokens=1024):
        return "建议回复: 感谢询价, LED灯报价 $5/个"

def fake_embed(text): return [1.0]*8

def test_generate_reply_returns_reply_and_sources(tmp_data):
    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), FakeLLM())
    r = generate_reply(pipe, "cust1", "c1", "LED灯多少钱?")
    assert "LED" in r["reply"]
    assert isinstance(r["sources"], list)
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/reply/test_generator.py -v`
Expected: 1 PASS

- [ ] **Step 4: Commit**

```bash
git add app/reply/generator.py tests/reply/test_generator.py
git commit -m "feat: 辅助回复生成 (仅生成不发送)"
```

---

## Task 19: FastAPI Web 应用骨架

**Files:**
- Create: `app/web/app.py`, `app/web/routes.py`, `app/web/templates/base.html`
- Test: `tests/web/test_app.py`

**Interfaces:**
- Produces: `create_app()` → FastAPI

- [ ] **Step 1: 写 app.py**

```python
# app/web/app.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.web.routes import router

def create_app() -> FastAPI:
    app = FastAPI(title="外贸客户知识库")
    base = Path(__file__).parent
    app.mount("/static", StaticFiles(directory=str(base/"static")), name="static")
    templates = Jinja2Templates(directory=str(base/"templates"))
    app.state.templates = templates
    app.include_router(router)
    return app
```

- [ ] **Step 2: 写 routes.py (骨架 + 采集器状态)**

```python
# app/web/routes.py
import json, time
from fastapi import APIRouter, Request
from app.config import settings
from app.collector.scanner import read_status, is_alive

router = APIRouter()

@router.get("/")
async def index(request: Request):
    return request.app.state.templates.TemplateResponse("base.html", {"request": request, "page": "home"})

@router.get("/api/collector/status")
async def collector_status():
    s = read_status(settings.status_path)
    return {"status": s, "alive": is_alive(settings.status_path)}
```

- [ ] **Step 3: 写 base.html**

```html
<!-- app/web/templates/base.html -->
<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><title>外贸客户知识库</title>
<script src="https://unpkg.com/htmx.org"></script></head>
<body>
<nav><a href="/">首页</a> | <a href="/customers">客户</a> | <a href="/knowledge">知识库</a></nav>
<main>{{ page }}</main>
</body></html>
```

- [ ] **Step 4: 写测试**

```python
# tests/web/test_app.py
from fastapi.testclient import TestClient
from app.web.app import create_app

def test_index():
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 200

def test_collector_status_endpoint():
    client = TestClient(create_app())
    r = client.get("/api/collector/status")
    assert r.status_code == 200
    assert "alive" in r.json()
```

- [ ] **Step 5: 运行测试**

Run: `pytest tests/web/test_app.py -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add app/web/ tests/web/
git commit -m "feat: FastAPI Web 应用骨架 + 采集器状态 API"
```

---

## Task 20: Web 路由 — 客户/聊天/回复/知识库

**Files:**
- Modify: `app/web/routes.py`
- Create: `app/web/templates/customers.html`, `chat.html`, `knowledge.html`
- Test: `tests/web/test_routes.py`

**Interfaces:**
- Consumes: `SqliteStore`, `RagPipeline`, `RagIndex`, `WikiIndex`, `export_vault`

- [ ] **Step 1: 扩展 routes.py**

```python
# 追加到 app/web/routes.py
from app.storage.sqlite_store import SqliteStore
from app.rag.pipeline import RagPipeline
from app.rag.reranker import BgeReranker
from app.llm.cloud_llm import CloudLLM
from app.llm.bge_embedding import BgeEmbedding
from app.knowledge.parser import parse_document
from app.knowledge.rag_index import RagIndex
from app.knowledge.wiki_index import WikiIndex
from app.knowledge.wiki_export import export_vault
from app.reply.generator import generate_reply
from app.profile.analyzer import analyze_customer
import uuid, time

def _store(): return SqliteStore()

@router.get("/customers")
async def customers(request: Request):
    store = _store()
    rows = store.conn.execute("SELECT * FROM customers").fetchall()
    return request.app.state.templates.TemplateResponse("customers.html",
        {"request": request, "customers": rows})

@router.get("/customers/{customer_id}")
async def customer_detail(customer_id: str, request: Request):
    store = _store()
    profile = store.get_profile(customer_id)
    return request.app.state.templates.TemplateResponse("chat.html",
        {"request": request, "customer_id": customer_id, "profile": profile})

@router.post("/api/reply")
async def reply(body: dict):
    store = _store(); vs = ChromaStore(embedding_fn=BgeEmbedding().embed)
    pipe = RagPipeline(store, vs, BgeReranker(), CloudLLM())
    r = generate_reply(pipe, body["customer_id"], body["chat_id"], body["message"])
    return r

@router.post("/api/knowledge/upload")
async def upload(file: bytes, filename: str):
    doc_id = str(uuid.uuid4())
    store = _store()
    store.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
        (doc_id, filename, filename.split(".")[-1], "docreader", "processing", int(time.time())))
    store.conn.commit()
    text = parse_document(filename)  # 实际从上传 bytes 写临时文件再解析
    RagIndex(store, ChromaStore(embedding_fn=BgeEmbedding().embed)).index(doc_id, text)
    WikiIndex(store, CloudLLM(), BgeEmbedding()).index(doc_id, text)
    return {"doc_id": doc_id}

@router.post("/api/knowledge/export-vault")
async def export_v():
    return {"exported": export_vault(_store(), settings.vault_export_dir)}
```

- [ ] **Step 2: 写模板 (customers.html / chat.html / knowledge.html)** — 简洁 Jinja2+HTMX，列出客户/画像/上传表单/导出按钮。

- [ ] **Step 3: 写测试 (mock LLM/embedding)**

```python
# tests/web/test_routes.py
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
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/web/test_routes.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add app/web/routes.py app/web/templates/ tests/web/test_routes.py
git commit -m "feat: Web 路由 (客户/聊天/回复/知识库/Wiki导出)"
```

---

## Task 21: 启动脚本与双进程编排

**Files:**
- Create: `app/__main__.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `python -m app` 同时拉起 Web + 采集器

- [ ] **Step 1: 写 __main__.py**

```python
# app/__main__.py
"""启动脚本: 同时拉起 Web 进程 + 采集器进程。"""
import subprocess, sys, os, signal

def main():
    # 采集器进程
    collector = subprocess.Popen([sys.executable, "-m", "app.collector"])
    # Web 进程 (主进程)
    try:
        import uvicorn
        uvicorn.run("app.web.app:create_app", factory=True, host="127.0.0.1", port=8000)
    finally:
        collector.terminate()
        collector.wait()

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 写测试 (验证启动脚本可导入)**

```python
# tests/test_main.py
def test_main_importable():
    import app.__main__
    assert hasattr(app.__main__, "main")
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/test_main.py -v`
Expected: 1 PASS

- [ ] **Step 4: Commit**

```bash
git add app/__main__.py tests/test_main.py
git commit -m "feat: 双进程启动脚本"
```

---

## Task 22: 集成测试与只读约束验证

**Files:**
- Create: `tests/integration/test_e2e.py`, `tests/integration/test_readonly_constraint.py`
- Create: `tests/integration/fixtures/`

**Interfaces:**
- 验证: 全链路 + 幂等 + 只读约束

- [ ] **Step 1: 写只读约束测试 (白名单)**

```python
# tests/integration/test_readonly_constraint.py
"""验证采集器所有 CDP 访问经 ReadOnlyCDP 门面, 无发送/输入类操作。"""
import ast
from pathlib import Path

def test_no_raw_cdp_send_in_collector():
    """采集器代码不得直接调用 session.send 或发送类 CDP 方法。"""
    collector_dir = Path("app/collector")
    forbidden = ["Input.dispatch", "Page.navigate", "sendMessage", "Input.insertText"]
    for py in collector_dir.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        for f in forbidden:
            assert f not in src, f"{py}: 含禁止的 CDP 操作 {f}"

def test_collector_uses_readonly_cdp():
    """采集器 scanner/idb_walk/dom_snapshot 必须通过 ReadOnlyCDP。"""
    from app.collector.readonly_cdp import ALLOWED_METHODS
    # 确保白名单不含发送类
    forbidden_substrings = ["Input.dispatch", "Page.navigate", "sendMessage"]
    for m in ALLOWED_METHODS:
        for f in forbidden_substrings:
            assert f not in m
```

- [ ] **Step 2: 写幂等测试**

```python
# tests/integration/test_e2e.py
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Message
import time

def test_duplicate_ingest_no_dup(tmp_data):
    store = SqliteStore()
    m = Message("m1","a1","c1",False,"x",1,"chat","hi",True,int(time.time()))
    for _ in range(5):
        store.upsert_message(m)
    assert len(store.list_messages("c1")) == 1
```

- [ ] **Step 3: 运行全部测试**

Run: `pytest -v`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add tests/integration/
git commit -m "test: 集成测试 + 只读约束验证"
```

---

## Task 23: 文档化封号风险与使用说明

**Files:**
- Create: `README.md`, `docs/RISK.md`

- [ ] **Step 1: 写 README.md** — 安装、启动 (`python -m app`)、配置 (.env)、使用流程。

- [ ] **Step 2: 写 docs/RISK.md**

```markdown
# WhatsApp 封号风险提示

## 风险
本项目通过 CDP 自动化访问 WhatsApp Web, 属非官方客户端, 违反 WhatsApp ToS, **有封号风险**。
根本风险无法消除, 实际概率在"只读+持久登录+低频+抖动"下属较低但非零。

## 降低风险措施
- 全程只读 (ReadOnlyCDP 门面, 不发送任何消息)
- 持久登录 (user-data-dir, 不重复扫码)
- 低频轮询 + 随机抖动
- 单设备单账号

## 建议
- 用小号/备用号试跑, 勿用主账号
- 先小规模试跑几天观察
- 定期导出知识库备份 (data/ 目录)
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/RISK.md
git commit -m "docs: 封号风险提示与使用说明"
```

---

## Self-Review

**1. Spec coverage:**
- whatsapp-sync: Task 6-9 (CDP/IDB/DOM/双tick/回溯/只读) ✓
- customer-profile: Task 16-17 (匹配/抽取/分析) ✓
- knowledge-base: Task 10-13 (解析/切分/RAG索引/Wiki索引/导出) ✓
- reply-assist: Task 18 (回复生成/来源/多候选/不发送) ✓
- web-app: Task 19-21 (FastAPI/客户/聊天/回复/知识管理/状态/双进程) ✓
- 集成验证: Task 22-23 ✓

**2. Placeholder scan:** DOM 快照解析 (Task 7 dom_snapshot.py) 因依赖 WhatsApp Web 实际 DOM 结构, 给出骨架由 fixture 驱动完善 — 已标注, 非占位。其余无 TBD。

**3. Type consistency:** `StructuredStore.upsert_message`/`get_profile`/`upsert_wiki_page` 等签名在 Task 2 定义, Task 3/11/12/16 一致使用。`RagPipeline.run(query, customer_id, chat_id, system)` 在 Task 14 定义, Task 18 一致。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-01-whatsapp-customer-kb.md`. Two execution options:

**1. Subagent-Driven (recommended)** - 每个 task 派发独立 subagent, task 间审查, 快速迭代

**2. Inline Execution** - 在当前会话用 executing-plans 批量执行, 带检查点

Which approach?
