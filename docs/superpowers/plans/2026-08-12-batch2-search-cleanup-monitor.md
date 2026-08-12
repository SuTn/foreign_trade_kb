---
change: batch2-search-cleanup-monitor
design-doc: docs/superpowers/specs/2026-08-12-batch2-search-cleanup-monitor-design.md
base-ref: 84cafaf13f20e7f46ef25f5159aedfc350c6cac6
---
# 全局搜索 / 手动数据清理 / 采集器异常横幅 (batch2-search-cleanup-monitor) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Web 端三处增强——全局搜索页（客户/消息/知识库/画像四源分组）、手动数据清理（按会话或按天数删消息 + FTS 重建 + 向量清理，保留画像与知识库）、采集器异常全局横幅（自适应轮询）。

**Architecture:** `GET /api/search?q=` 聚合四源返回 JSON 分组，htmx 请求（`HX-Request` 头）内容协商返回渲染片段（JSON 可测 + htmx 可渲染）；`POST /api/cleanup` 先删 messages 行再一次性 rebuild `messages_fts`（参照 `delete_document`），再按 chat_id 逐个删 chroma 消息向量；`base.html` 顶部横幅 + `app.js` 递归 setTimeout 轮询 `/api/collector/status`（在线 15s / 离线 5s 快查），复用既有端点不新增。

**Tech Stack:** FastAPI / Jinja2 / HTMX / sqlite3（FTS5 外部内容表）/ chromadb（metadata where 过滤）/ pytest（TestClient + monkeypatch）

## Global Constraints

- 不引入新外部依赖（搜索复用 FTS5，无检索引擎；清理无队列/无后台任务）。
- 清理删除不可恢复：前端必须用 `hx-confirm` 确认；只清 messages + FTS + 消息向量，**profiles / documents / doc_chunks 完全不动**。
- FTS 同步固定用 `INSERT INTO messages_fts(messages_fts) VALUES('rebuild')` 一次性重建（参照 `delete_document` 模式）；**不改动 `delete_document` 现有逻辑**。
- 向量删除 = `ChromaStore.delete_message_vectors(chat_id)` 实现为 `msg_col.delete(where={"chat_id": chat_id})`；days 模式先 `SELECT DISTINCT chat_id` 收集再逐个删。
- 搜索 LIKE 必须转义 `%`/`_`（SQL `ESCAPE '\'`），防通配符误匹配。
- `/api/search` 默认返回 JSON `{query, customers[], messages[], knowledge[], profiles[]}`；仅当请求带 `HX-Request: true` 头时返回 `search_results.html` 渲染片段（内容协商，兼容 htmx 页面）。
- 搜索/清理均包 try/except：搜索失败返回空分组 + `error` 字段；清理失败返回 `{error}` 可读信息——**不返回 500**。
- 清理参数校验：mode 非 chat/days、chat 缺 chat_id、days 缺 days 或非正数 → `400` 可读错误。
- 回归基线：`pytest -q` 全量通过 + `compileall -q app` 通过。
- base-ref: `84cafaf13f20e7f46ef25f5159aedfc350c6cac6`

## 与 tasks.md 的任务边界对应关系

| tasks.md 区段 | 对应 Task |
| --- | --- |
| §1.1 SqliteStore 客户/画像 LIKE 检索 | Task 1 |
| §1.2 FTS 结果 join 映射（消息/知识库） | Task 4（join 在 routes 聚合中实现，参照 `knowledge_search` 的 doc_lookup 模式；`search_fts` 补 rowid 列在 Task 1 完成） |
| §1.3 `GET /api/search` 聚合四源 | Task 4 |
| §1.4 `/search` 页模板（分组展示、空查询提示） | Task 5 |
| §1.5 单测：四源命中与空查询 | Task 1（store）/ Task 4（API）/ Task 5（页面） |
| §2.1 `VectorStore.delete_message_vectors` + 接口声明 | Task 3 |
| §2.2 SqliteStore 按 chat_id / 按 ts 删除 + FTS rebuild | Task 2 |
| §2.3 `POST /api/cleanup`（参数校验 + 降级） | Task 6 |
| §2.4 管理入口：cleanup.html 模板页 + 确认按钮 | Task 6 |
| §2.5 单测：按会话/按天数、画像与知识库保留断言 | Task 2 / 3 / 6 |
| §3.1 `base.html` 横幅容器 | Task 7 |
| §3.2 `app.js` 轮询 `/api/collector/status` 显示/隐藏横幅 | Task 7 |
| §3.3 前端测试/走读确认横幅逻辑 | Task 7（渲染断言 + JS 走读） |
| §4 回归验证（pytest / compileall / 走读） | Task 8 |

## 文件结构总览

| 文件 | 变更 | 职责 |
| --- | --- | --- |
| `app/storage/sqlite_store.py` | 修改 | `search_customers`/`search_profiles`/`_escape_like`、`search_fts` 补 rowid、`delete_messages_by_chat`/`delete_messages_before`/`_rebuild_messages_fts` |
| `app/storage/interfaces.py` | 修改 | `VectorStore` 抽象新增 `delete_message_vectors(chat_id)` |
| `app/storage/chroma_store.py` | 修改 | 实现 `delete_message_vectors(chat_id)`（`msg_col.delete(where={"chat_id": chat_id})`） |
| `app/web/routes.py` | 修改 | `_search_messages`/`_search_knowledge`/`GET /api/search`/`GET /search`、`_cleanup_params`/`POST /api/cleanup`/`GET /cleanup`；`JSONResponse` 导入 |
| `app/web/templates/base.html` | 修改 | 横幅容器 + 导航「搜索」「清理」链接 |
| `app/web/templates/search.html` | 新建 | 搜索页（输入框 + htmx keyup delay 300ms） |
| `app/web/templates/search_results.html` | 新建 | 分组渲染片段（htmx 内容协商目标） |
| `app/web/templates/cleanup.html` | 新建 | 清理管理页（chat / days 两种模式 + hx-confirm） |
| `app/web/static/css/app.css` | 修改 | `#collector-banner` 红色横幅样式 |
| `app/web/static/js/app.js` | 修改 | 横幅自适应轮询（15s/5s 递归 setTimeout） |
| `tests/storage/test_sqlite_store.py` | 修改 | Task 1/2 的 store 单测 |
| `tests/storage/test_chroma_store.py` | 修改 | `delete_message_vectors` 单测 |
| `tests/storage/test_interfaces.py` | 修改 | ChromaStore 实现 VectorStore 断言 |
| `tests/web/test_search.py` | 新建 | `/api/search` + `/search` 页单测 |
| `tests/web/test_cleanup.py` | 新建 | `/api/cleanup` + `/cleanup` 页单测 |
| `tests/web/test_banner.py` | 新建 | 横幅渲染断言 |

---

### Task 1: SqliteStore 全局搜索方法（客户/画像 LIKE + FTS rowid）

**Files:**
- Modify: `app/storage/sqlite_store.py:87-99`（`search_fts` 补 rowid）
- Modify: `app/storage/sqlite_store.py`（追加 `_escape_like`/`search_customers`/`search_profiles`）
- Test: `tests/storage/test_sqlite_store.py`（末尾追加）

**Interfaces:**
- Consumes: 既有 `SqliteStore` 风格（`self.conn.execute` + `commit`）、`customers`/`profiles` 表结构、`search_fts` 既有签名。
- Produces（Task 4 routes 依赖）：
  - `SqliteStore.search_customers(query: str, limit: int = 20) -> list[dict]`（键 `id/display_name/phone/company/country`）
  - `SqliteStore.search_profiles(query: str, limit: int = 20) -> list[dict]`（键 `customer_id/field/value`）
  - `SqliteStore.search_fts(table, query, limit)` 返回 dict 中**新增 `rowid` 键**（向后兼容，既有 `r["text"]` 仍可用）
  - `SqliteStore._escape_like(term: str) -> str`

- [x] **Step 1: 写失败测试**（追加到 `tests/storage/test_sqlite_store.py` 末尾）

```python
# ---- batch2-search-cleanup-monitor: 全局搜索 (tasks 1.1 / 1.5) ----
def test_search_customers_matches_fields_and_escapes(tmp_data):
    s = SqliteStore()
    s.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                   ("c1", "Alice", "10086", "ACME", "USA", 0, None))
    s.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                   ("c2", "Bob", "10086-2", "Beta", "Canada", 1, None))
    s.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                   ("c3", "100%", "0", "X", "Y", 2, None))
    s.conn.commit()
    assert [c["id"] for c in s.search_customers("ACME")] == ["c1"]
    assert [c["id"] for c in s.search_customers("10086")] == ["c2", "c1"]  # phone 命中, created_at DESC
    assert [c["id"] for c in s.search_customers("%")] == ["c3"]  # % 转义为字面量, 不匹配全部
    assert s.search_customers("_") == []  # _ 转义, 不匹配任意单字符
    assert s.search_customers("") == []


def test_search_profiles_matches_field_and_value(tmp_data):
    s = SqliteStore()
    s.upsert_profile_field("c1", "country", "USA", "auto")
    s.upsert_profile_field("c1", "company", "ACME", "auto")
    s.upsert_profile_field("c2", "country", "China", "auto")
    assert [r["customer_id"] for r in s.search_profiles("USA")] == ["c1"]
    assert [r["customer_id"] for r in s.search_profiles("company")] == ["c1"]  # field 命中
    assert s.search_profiles("") == []


def test_fts_search_returns_rowid(tmp_data):
    s = SqliteStore()
    s.upsert_message(Message("m1", "a1", "c1", False, "x", 1, "chat", "invoice for order", True, 1))
    res = s.search_fts("messages", "invoice", 10)
    assert len(res) == 1
    assert "rowid" in res[0]
    row = s.conn.execute("SELECT rowid FROM messages WHERE id='m1'").fetchone()
    assert res[0]["rowid"] == row["rowid"]
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/storage/test_sqlite_store.py -v`
Expected: 3 个新用例 FAIL（`'SqliteStore' object has no attribute 'search_customers'` / `search_profiles`；`test_fts_search_returns_rowid` 断言 `"rowid" in res[0]` 失败，因 `SELECT *` 不含 rowid）。

- [x] **Step 3: 最小实现**

(1) `app/storage/sqlite_store.py` 的 `search_fts`（第 98 行）把 `SELECT *` 改为 `SELECT rowid, *`：

```python
        rows = self.conn.execute(f"SELECT rowid, * FROM {fts} WHERE {col} MATCH ? LIMIT ?", (expr, limit)).fetchall()
```

(2) 在 `search_fts` 方法之后（`upsert_wiki_page` 之前）追加：

```python
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
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/storage/test_sqlite_store.py -v`
Expected: 既有用例 + 新增 3 个全部 PASS。

- [x] **Step 5: 提交**

```bash
git add app/storage/sqlite_store.py tests/storage/test_sqlite_store.py
git commit -m "feat: SqliteStore 全局搜索方法 (客户/画像 LIKE 转义 + search_fts 返回 rowid)"
```

---

### Task 2: SqliteStore 清理方法（按 chat_id / 按 ts + FTS rebuild）

**Files:**
- Modify: `app/storage/sqlite_store.py`（追加 `_rebuild_messages_fts`/`delete_messages_by_chat`/`delete_messages_before`）
- Test: `tests/storage/test_sqlite_store.py`（末尾追加）

**Interfaces:**
- Consumes: 既有 `self.conn`；`messages`/`messages_fts`（外部内容表，`content_rowid='rowid'`）；`delete_document` 的 rebuild 模式（不改动它）。
- Produces（Task 6 routes 依赖）：
  - `delete_messages_by_chat(chat_id: str) -> dict`（返回 `{"deleted_rows": int, "affected_chats": list[str]}`，无删除时 `affected_chats=[]`）
  - `delete_messages_before(cutoff_ts: int) -> dict`（返回同上，`affected_chats` 为该时间前涉及的全部 DISTINCT chat_id）
  - `_rebuild_messages_fts() -> None`

- [x] **Step 1: 写失败测试**（追加到 `tests/storage/test_sqlite_store.py` 末尾）

```python
# ---- batch2-search-cleanup-monitor: 手动清理 (tasks 2.2 / 2.5) ----
def test_delete_messages_by_chat_and_fts_rebuild(tmp_data):
    s = SqliteStore()
    s.upsert_message(Message("m1", "a1", "c1", False, "x", 1, "chat", "invoice c1", True, 1))
    s.upsert_message(Message("m2", "a1", "c1", False, "x", 2, "chat", "order c1", True, 2))
    s.upsert_message(Message("m3", "a1", "c2", False, "y", 3, "chat", "invoice c2", True, 3))
    res = s.delete_messages_by_chat("c1")
    assert res == {"deleted_rows": 2, "affected_chats": ["c1"]}
    assert s.list_messages("c1") == []
    assert len(s.list_messages("c2")) == 1  # 其他会话不受影响
    assert s.search_fts("messages", "order", 10) == []        # FTS 已重建, 删除内容不可搜
    assert len(s.search_fts("messages", "invoice", 10)) == 1  # 仅剩 c2
    assert s.delete_messages_by_chat("nope") == {"deleted_rows": 0, "affected_chats": []}


def test_delete_messages_before_cutoff(tmp_data):
    s = SqliteStore()
    s.upsert_message(Message("m1", "a1", "c1", False, "x", 100, "chat", "old c1", True, 1))
    s.upsert_message(Message("m2", "a1", "c1", False, "x", 200, "chat", "new c1", True, 2))
    s.upsert_message(Message("m3", "a1", "c2", False, "y", 150, "chat", "old c2", True, 3))
    res = s.delete_messages_before(180)
    assert res["deleted_rows"] == 2
    assert sorted(res["affected_chats"]) == ["c1", "c2"]
    assert [m.body for m in s.list_messages("c1")] == ["new c1"]
    assert s.list_messages("c2") == []
    assert s.search_fts("messages", "old", 10) == []


def test_delete_messages_keeps_profiles_and_documents(tmp_data):
    s = SqliteStore()
    s.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", ("d1", "a.md", "md", "docreader", "done", 1))
    s.conn.execute("INSERT INTO doc_chunks VALUES(?,?,?,?,?,?)", ("ch1", "d1", 0, "LED spec", "0", "ch1"))
    s.upsert_profile_field("c1", "country", "USA", "auto")
    s.upsert_message(Message("m1", "a1", "c1", False, "x", 1, "chat", "hi", True, 1))
    s.delete_messages_by_chat("c1")
    assert len(s.get_profile("c1")) == 1  # 画像保留
    assert s.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1   # 文档保留
    assert s.conn.execute("SELECT COUNT(*) FROM doc_chunks").fetchone()[0] == 1  # chunk 保留
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/storage/test_sqlite_store.py -v`
Expected: 3 个新用例 FAIL（`AttributeError: 'SqliteStore' object has no attribute 'delete_messages_by_chat'`）。

- [x] **Step 3: 最小实现**

在 `app/storage/sqlite_store.py` 的 `delete_document` 之后（`# ---- reply-workflow-optimization` 注释之前）追加：

```python
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
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/storage/test_sqlite_store.py -v`
Expected: 既有用例 + 新增 3 个全部 PASS。

- [x] **Step 5: 提交**

```bash
git add app/storage/sqlite_store.py tests/storage/test_sqlite_store.py
git commit -m "feat: SqliteStore 清理方法 (按 chat_id / 按 ts 删消息 + FTS rebuild)"
```

---

### Task 3: VectorStore 接口抽象 + ChromaStore.delete_message_vectors

**Files:**
- Modify: `app/storage/interfaces.py:58`（`VectorStore` 增加抽象方法）
- Modify: `app/storage/chroma_store.py`（实现方法）
- Test: `tests/storage/test_chroma_store.py`、`tests/storage/test_interfaces.py`

**Interfaces:**
- Consumes: 既有 `VectorStore` ABC、`ChromaStore.msg_col`（`get_or_create_collection("message_vectors")`）、metadata 键 `chat_id`（`upsert_message_vector` 时写入）。
- Produces（Task 6 routes 依赖）：
  - `VectorStore.delete_message_vectors(chat_id: str) -> None`（抽象）
  - `ChromaStore.delete_message_vectors(chat_id: str) -> None`（`msg_col.delete(where={"chat_id": chat_id})`）

- [x] **Step 1: 写失败测试**

`tests/storage/test_chroma_store.py` 末尾追加：

```python
def test_delete_message_vectors_by_chat(tmp_data):
    s = ChromaStore(embedding_fn=fake_embed)
    s.upsert_message_vector("k1", "hello c1", {"chat_id": "c1", "day": "2026-01-01"})
    s.upsert_message_vector("k2", "hello c2", {"chat_id": "c2", "day": "2026-01-01"})
    s.delete_message_vectors("c1")
    res = s.query_messages("hello", top_k=10)
    assert [m["metadata"]["chat_id"] for m in res] == ["c2"]  # c1 向量已删, c2 保留
```

`tests/storage/test_interfaces.py` 末尾追加：

```python
def test_chroma_store_implements_vector_store(tmp_data):
    from app.storage.chroma_store import ChromaStore
    s = ChromaStore(embedding_fn=lambda t: [1.0] * 8)
    assert isinstance(s, VectorStore)
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/storage/test_chroma_store.py tests/storage/test_interfaces.py -v`
Expected: `test_delete_message_vectors_by_chat` FAIL（`AttributeError: 'ChromaStore' object has no attribute 'delete_message_vectors'`）。

- [x] **Step 3: 最小实现**

(1) `app/storage/interfaces.py` 的 `VectorStore` 类追加抽象方法（`delete_chunks` 之后）：

```python
    @abstractmethod
    def delete_message_vectors(self, chat_id: str) -> None: ...
```

(2) `app/storage/chroma_store.py` 在 `delete_chunks` 之后追加：

```python
    def delete_message_vectors(self, chat_id: str):
        """按 chat_id 删除全部消息向量 (metadata 过滤, D2)。"""
        self.msg_col.delete(where={"chat_id": chat_id})
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/storage/test_chroma_store.py tests/storage/test_interfaces.py -v`
Expected: 全部 PASS（含既有 `test_delete_chunks_by_doc`、`test_vector_store_is_abstract`）。

- [x] **Step 5: 提交**

```bash
git add app/storage/interfaces.py app/storage/chroma_store.py tests/storage/test_chroma_store.py tests/storage/test_interfaces.py
git commit -m "feat: VectorStore.delete_message_vectors 抽象 + ChromaStore metadata 过滤实现"
```

---

### Task 4: GET /api/search 聚合四源

**Files:**
- Modify: `app/web/routes.py`
- Create: `tests/web/test_search.py`

**Interfaces:**
- Consumes: Task 1 的 `search_customers`/`search_profiles`/`search_fts(含 rowid)`；既有 `_store(request)`；`knowledge_search` 的 `doc_lookup` 模式；`settings`/`read_status` 无关。
- Produces（Task 5 页面依赖）：
  - `_search_messages(store, query, limit=20) -> list[dict]`（键 `chat_id/ts/body`）
  - `_search_knowledge(store, query, limit=20) -> list[dict]`（键 `doc_id/text`）
  - `GET /api/search?q=`：默认返回 `{query, customers[], messages[], knowledge[], profiles[]}`（异常时含 `error`）；带 `HX-Request` 头返回 `search_results.html` 渲染片段

- [x] **Step 1: 写失败测试 `tests/web/test_search.py`**

```python
from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Message


def _seed_all_sources():
    """造四源都命中 'LED' 的数据。"""
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "ACME LED", "10086", "ACME", "USA", 0, None))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",
                       ("c1", "interest", "LED 灯带", "auto", 0))
    store.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                       ("d1", "a.md", "md", "docreader", "done", 1))
    store.conn.execute("INSERT INTO doc_chunks VALUES(?,?,?,?,?,?)",
                       ("ch1", "d1", 0, "LED 产品规格", "0", "ch1"))
    store.conn.execute("INSERT INTO doc_chunks_fts(rowid, text) "
                       "VALUES((SELECT rowid FROM doc_chunks WHERE id='ch1'), ?)",
                       ("LED 产品规格",))
    store.upsert_message(Message("m1", "a1", "c1", False, "x", 1000, "chat", "LED invoice", True, 1))
    store.conn.commit()


def test_api_search_groups_four_sources(tmp_data):
    _seed_all_sources()
    j = TestClient(create_app()).get("/api/search", params={"q": "LED"}).json()
    assert j["query"] == "LED"
    assert [c["id"] for c in j["customers"]] == ["c1"]
    assert j["messages"] == [{"chat_id": "c1", "ts": 1000, "body": "LED invoice"}]
    assert j["knowledge"] == [{"doc_id": "d1", "text": "LED 产品规格"}]
    assert j["profiles"] == [{"customer_id": "c1", "field": "interest", "value": "LED 灯带"}]


def test_api_search_empty_query_returns_empty_groups(tmp_data):
    j = TestClient(create_app()).get("/api/search", params={"q": ""}).json()
    assert j == {"query": "", "customers": [], "messages": [], "knowledge": [], "profiles": []}


def test_api_search_htmx_returns_rendered_partial(tmp_data):
    _seed_all_sources()
    r = TestClient(create_app()).get("/api/search", params={"q": "LED"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "客户 (1)" in r.text and "消息 (1)" in r.text
    assert "知识库 (1)" in r.text and "画像 (1)" in r.text


def test_api_search_degrades_on_error(tmp_data, monkeypatch):
    from app.storage.sqlite_store import SqliteStore as SS

    def boom(self, query, limit=20):
        raise RuntimeError("db 故障")

    monkeypatch.setattr(SS, "search_customers", boom)
    j = TestClient(create_app()).get("/api/search", params={"q": "LED"}).json()
    assert "error" in j
    assert j["customers"] == []
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/web/test_search.py -v`
Expected: 全部 FAIL（`404: Not Found`，`/api/search` 未实现）。

- [x] **Step 3: 最小实现**

`app/web/routes.py` 在 `collector_status` 端点之后、`/api/stats` 之前追加：

```python
def _search_messages(store, query, limit=20):
    """D1: 消息 FTS 行 join 回 messages 取 chat_id/body/ts (search_fts 已含 rowid)。"""
    out = []
    for r in store.search_fts("messages", query, limit):
        row = store.conn.execute(
            "SELECT chat_id, ts, body FROM messages WHERE rowid=?", (r["rowid"],)).fetchone()
        if row:
            out.append({"chat_id": row["chat_id"], "ts": row["ts"], "body": row["body"]})
    return out


def _search_knowledge(store, query, limit=20):
    """D1: 知识库 FTS join 回 doc_chunks 取 doc_id (参照 knowledge_search 的 doc_lookup)。"""
    doc_lookup = {}
    for r in store.conn.execute("SELECT rowid, doc_id FROM doc_chunks").fetchall():
        doc_lookup[r["rowid"]] = r["doc_id"]
    out = []
    for r in store.search_fts("doc_chunks", query, limit):
        out.append({"doc_id": doc_lookup.get(r["rowid"]), "text": r["text"]})
    return out


@router.get("/api/search")
async def api_search(request: Request, q: str = ""):
    """D1: 全局搜索聚合四源 → JSON 分组; htmx 请求 (HX-Request) 返回渲染片段。"""
    query = (q or "").strip()
    store = _store(request)
    result = {"query": query, "customers": [], "messages": [], "knowledge": [], "profiles": []}
    try:
        if query:
            result["customers"] = store.search_customers(query)
            result["messages"] = _search_messages(store, query)
            result["knowledge"] = _search_knowledge(store, query)
            result["profiles"] = store.search_profiles(query)
    except Exception as e:
        result["error"] = f"搜索失败: {e}"
    if request.headers.get("HX-Request"):
        return request.app.state.templates.TemplateResponse(request, "search_results.html", result)
    return result
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/web/test_search.py -v`
Expected: 4 passed。

同时验证 `search_fts` 改动无回归（既有 `knowledge_search` 依赖 `r["text"]`）：

Run: `pytest tests/web/test_routes.py::test_knowledge_search_returns_results tests/web/test_routes.py::test_search_embedding_failure_degrades_to_bm25 -v`
Expected: 2 passed。

- [x] **Step 5: 提交**

```bash
git add app/web/routes.py tests/web/test_search.py
git commit -m "feat: GET /api/search 四源聚合 (JSON 分组 + htmx 内容协商片段)"
```

---

### Task 5: /search 全局搜索页

**Files:**
- Create: `app/web/templates/search.html`
- Create: `app/web/templates/search_results.html`
- Modify: `app/web/routes.py`（新增 `GET /search` 页路由）
- Modify: `app/web/templates/base.html`（导航加「搜索」链接）
- Test: `tests/web/test_search.py`（追加页面用例）

**Interfaces:**
- Consumes: Task 4 的 `/api/search`（HX-Request 内容协商）。
- Produces:
  - `GET /search` → 渲染 `search.html`（输入框 + `hx-get="/api/search"` + `hx-trigger="keyup changed delay:300ms, search"` + `hx-target="#search-results"`）
  - `search_results.html`：四分组渲染 + 空查询友好提示 + 错误提示
  - base.html 导航 `搜索` 链接（后续 Task 7 会在同一导航行再追加 `清理`）

- [ ] **Step 1: 写失败测试**（追加到 `tests/web/test_search.py`）

```python
def test_search_page_renders(tmp_data):
    html = TestClient(create_app()).get("/search").text
    assert 'hx-get="/api/search"' in html
    assert "keyup changed delay:300ms" in html
    assert 'id="search-results"' in html
    assert '<a href="/search">搜索</a>' in html
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/web/test_search.py::test_search_page_renders -v`
Expected: FAIL（`404: Not Found` 或断言失败，模板不存在、导航无链接）。

- [ ] **Step 3: 最小实现**

(1) 新建 `app/web/templates/search.html`：

```html
{% extends "base.html" %}
{% block content %}
<h1>全局搜索</h1>
<input class="input" id="global-q" name="q" type="search"
       placeholder="搜索客户 / 消息 / 知识库 / 画像…" autocomplete="off"
       hx-get="/api/search"
       hx-trigger="keyup changed delay:300ms, search"
       hx-target="#search-results"
       hx-swap="innerHTML">
<div id="search-results">
  <p class="muted">输入关键词开始搜索。</p>
</div>
{% endblock %}
```

(2) 新建 `app/web/templates/search_results.html`：

```html
{% if query == '' %}
<p class="muted">输入关键词开始搜索。</p>
{% elif error %}
<p class="empty">{{ error }}</p>
{% else %}
<section>
  <h2>客户 ({{ customers|length }})</h2>
  {% for c in customers %}
  <div class="result-card">
    <a href="/customers/{{ c['id'] }}">{{ c['display_name'] or c['id'] }}</a>
    <div class="muted">{{ c['phone'] or '' }} · {{ c['company'] or '' }} · {{ c['country'] or '' }}</div>
  </div>
  {% else %}
  <p class="muted">无客户命中</p>
  {% endfor %}
</section>
<section>
  <h2>消息 ({{ messages|length }})</h2>
  {% for m in messages %}
  <div class="result-card">
    <div class="muted">chat={{ m['chat_id'] }} · ts={{ m['ts'] }}</div>
    <p>{{ (m['body'] or '')[:200] }}</p>
  </div>
  {% else %}
  <p class="muted">无消息命中</p>
  {% endfor %}
</section>
<section>
  <h2>知识库 ({{ knowledge|length }})</h2>
  {% for k in knowledge %}
  <div class="result-card">
    <div class="muted">doc={{ k['doc_id'] or '-' }}</div>
    <p>{{ (k['text'] or '')[:200] }}</p>
  </div>
  {% else %}
  <p class="muted">无知识库命中</p>
  {% endfor %}
</section>
<section>
  <h2>画像 ({{ profiles|length }})</h2>
  {% for p in profiles %}
  <div class="result-card">
    <div class="muted">customer={{ p['customer_id'] }} · {{ p['field'] }}</div>
    <p>{{ p['value'] }}</p>
  </div>
  {% else %}
  <p class="muted">无画像命中</p>
  {% endfor %}
</section>
{% endif %}
```

(3) `app/web/routes.py` 在 `api_search` 端点之后追加页路由：

```python
@router.get("/search")
async def search_page(request: Request):
    """D1: 全局搜索页 (htmx 驱动 /api/search)。"""
    return request.app.state.templates.TemplateResponse(request, "search.html", {})
```

(4) `app/web/templates/base.html` 导航行追加链接：

```html
  <a href="/">首页</a><a href="/customers">客户</a><a href="/knowledge">知识库</a><a href="/search">搜索</a>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/web/test_search.py -v`
Expected: 5 passed（既有 4 个 + 页面 1 个）。

- [ ] **Step 5: 提交**

```bash
git add app/web/templates/search.html app/web/templates/search_results.html app/web/routes.py app/web/templates/base.html tests/web/test_search.py
git commit -m "feat: /search 全局搜索页 (htmx keyup delay 300ms 分组渲染)"
```

---

### Task 6: POST /api/cleanup + /cleanup 管理页

**Files:**
- Modify: `app/web/routes.py`（`JSONResponse` 导入、`_cleanup_params`/`cleanup`/`cleanup_page`）
- Modify: `app/web/templates/base.html`（导航加「清理」链接）
- Create: `app/web/templates/cleanup.html`
- Create: `tests/web/test_cleanup.py`

**Interfaces:**
- Consumes: Task 2 的 `delete_messages_by_chat(chat_id) -> dict` / `delete_messages_before(cutoff_ts) -> dict`；Task 3 的 `VectorStore.delete_message_vectors(chat_id)`；既有 `_chroma_store(request)`（返回进程级 ChromaStore 单例，测试经 `routes.get_embedding` monkeypatch 注入 FakeEmbed）。
- Produces:
  - `_cleanup_params(request) -> dict`（解析 JSON 或表单 body 的 `mode/chat_id/days`）
  - `POST /api/cleanup`：400 校验 + 删除 + 向量清理；成功返回 `{"deleted_rows", "affected_chats"}`；向量失败返回 `{"deleted_rows", "affected_chats", "error"}`
  - `GET /cleanup` → `cleanup.html`（chat/days 两表单 + `hx-confirm` 确认）

- [ ] **Step 1: 写失败测试 `tests/web/test_cleanup.py`**

```python
import time
from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Message


def test_cleanup_by_chat_deletes_messages_and_vectors(tmp_data, monkeypatch):
    from app.web import routes
    from app.storage.chroma_store import ChromaStore

    class FakeEmbed:
        def embed(self, text):
            return [float(len(text) % 7)] * 8

    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    store = SqliteStore()
    store.upsert_message(Message("m1", "a1", "c1", False, "x", 1, "chat", "hi c1", True, 1))
    store.upsert_message(Message("m2", "a1", "c2", False, "y", 2, "chat", "hi c2", True, 2))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)", ("c1", "country", "USA", "auto", 0))
    store.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", ("d1", "a.md", "md", "docreader", "done", 1))
    store.conn.commit()
    vec = ChromaStore(embedding_fn=FakeEmbed().embed)
    vec.upsert_message_vector("v1", "hi c1", {"chat_id": "c1"})
    vec.upsert_message_vector("v2", "hi c2", {"chat_id": "c2"})
    client = TestClient(create_app())
    r = client.post("/api/cleanup", json={"mode": "chat", "chat_id": "c1"})
    assert r.status_code == 200
    assert r.json() == {"deleted_rows": 1, "affected_chats": ["c1"]}
    assert store.list_messages("c1") == []
    assert len(store.list_messages("c2")) == 1
    res = vec.query_messages("hi", top_k=10)
    assert [m["metadata"]["chat_id"] for m in res] == ["c2"]  # c1 向量已删
    assert len(store.get_profile("c1")) == 1   # 画像保留
    assert store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1  # 文档保留


def test_cleanup_by_days_deletes_old_messages(tmp_data, monkeypatch):
    from app.web import routes

    class FakeEmbed:
        def embed(self, text):
            return [1.0] * 8

    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    now = int(time.time())
    store = SqliteStore()
    store.upsert_message(Message("m1", "a1", "c1", False, "x", now - 3 * 86400, "chat", "old c1", True, 1))
    store.upsert_message(Message("m2", "a1", "c1", False, "x", now - 86400, "chat", "yesterday", True, 2))
    store.upsert_message(Message("m3", "a1", "c2", False, "y", now - 5 * 86400, "chat", "old c2", True, 3))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/cleanup", json={"mode": "days", "days": 2})
    assert r.status_code == 200
    j = r.json()
    assert j["deleted_rows"] == 2
    assert sorted(j["affected_chats"]) == ["c1", "c2"]
    assert [m.body for m in store.list_messages("c1")] == ["yesterday"]
    assert store.list_messages("c2") == []


def test_cleanup_validation_400(tmp_data):
    client = TestClient(create_app())
    assert client.post("/api/cleanup", json={"mode": "chat"}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "days"}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "days", "days": 0}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "days", "days": -3}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "days", "days": "abc"}).status_code == 400
    assert client.post("/api/cleanup", json={"mode": "weird"}).status_code == 400


def test_cleanup_degrades_when_vector_delete_fails(tmp_data, monkeypatch):
    from app.web import routes

    class BoomVS:
        def delete_message_vectors(self, chat_id):
            raise RuntimeError("chroma 故障")

    monkeypatch.setattr(routes, "_chroma_store", lambda request: BoomVS())
    store = SqliteStore()
    store.upsert_message(Message("m1", "a1", "c1", False, "x", 1, "chat", "hi", True, 1))
    client = TestClient(create_app())
    r = client.post("/api/cleanup", json={"mode": "chat", "chat_id": "c1"})
    assert r.status_code == 200
    j = r.json()
    assert j["deleted_rows"] == 1
    assert "向量" in j["error"]


def test_cleanup_page_renders(tmp_data):
    html = TestClient(create_app()).get("/cleanup").text
    assert 'hx-post="/api/cleanup"' in html
    assert "hx-confirm" in html
    assert '<a href="/cleanup">清理</a>' in html
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/web/test_cleanup.py -v`
Expected: 全部 FAIL（`404: Not Found`，`/api/cleanup` 与 `/cleanup` 未实现）。

- [ ] **Step 3: 最小实现**

(1) `app/web/routes.py` 第 8 行改导入：

```python
from fastapi.responses import HTMLResponse, JSONResponse
```

(2) `app/web/routes.py` 在 `/search` 页路由之后追加：

```python
async def _cleanup_params(request: Request) -> dict:
    """从 JSON body 或表单解析 {mode, chat_id, days} (htmx 表单默认 form-encoded)。"""
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
    else:
        body = await request.form()
    return {"mode": (body.get("mode") or "").strip(),
            "chat_id": body.get("chat_id"),
            "days": body.get("days")}


@router.post("/api/cleanup")
async def cleanup(request: Request):
    """D2: 手动清理聊天消息。body: {mode: chat|days, chat_id?, days?}。
    删除 messages + 重建 FTS + 对应 chat 消息向量; 保留画像与知识库。"""
    body = await _cleanup_params(request)
    mode = body["mode"]
    store = _store(request)
    if mode == "chat":
        chat_id = (body.get("chat_id") or "").strip()
        if not chat_id:
            return JSONResponse({"error": "chat 模式需提供 chat_id"}, status_code=400)
        try:
            res = store.delete_messages_by_chat(chat_id)
        except Exception as e:
            return {"error": f"清理失败: {e}"}
        chat_ids = res["affected_chats"]
    elif mode == "days":
        days_raw = body.get("days")
        if days_raw is None or str(days_raw).strip() == "":
            return JSONResponse({"error": "days 模式需提供天数"}, status_code=400)
        try:
            days = int(days_raw)
        except (TypeError, ValueError):
            return JSONResponse({"error": "days 必须为正整数"}, status_code=400)
        if days <= 0:
            return JSONResponse({"error": "days 必须为正整数"}, status_code=400)
        cutoff = int(time.time()) - days * 86400
        try:
            res = store.delete_messages_before(cutoff)
        except Exception as e:
            return {"error": f"清理失败: {e}"}
        chat_ids = res["affected_chats"]
    else:
        return JSONResponse({"error": "mode 必须是 chat 或 days"}, status_code=400)
    try:
        vs = _chroma_store(request)
        for cid in chat_ids:
            vs.delete_message_vectors(cid)
    except Exception as e:
        return {"deleted_rows": res["deleted_rows"], "affected_chats": chat_ids,
                "error": f"消息已删除但向量清理失败: {e}"}
    return {"deleted_rows": res["deleted_rows"], "affected_chats": chat_ids}


@router.get("/cleanup")
async def cleanup_page(request: Request):
    """D2: 数据清理管理页 (chat / days 两种模式, 删除前确认)。"""
    return request.app.state.templates.TemplateResponse(request, "cleanup.html", {})
```

(3) 新建 `app/web/templates/cleanup.html`：

```html
{% extends "base.html" %}
{% block content %}
<h1>数据清理</h1>
<p class="muted">删除不可恢复! 仅清理聊天消息 (含向量), 画像与知识库不受影响。</p>
<div class="two-col">
  <section class="result-card">
    <h2>按会话清理</h2>
    <form hx-post="/api/cleanup"
          hx-confirm="确认删除该会话全部消息? 删除不可恢复!"
          hx-target="#cleanup-result" hx-swap="innerHTML">
      <input type="hidden" name="mode" value="chat">
      <input class="input" name="chat_id" placeholder="chat_id" style="width:100%">
      <button class="btn btn-danger" type="submit" style="margin-top:8px">删除该会话消息</button>
    </form>
  </section>
  <section class="result-card">
    <h2>按天数清理</h2>
    <form hx-post="/api/cleanup"
          hx-confirm="确认删除 N 天前的全部消息? 删除不可恢复!"
          hx-target="#cleanup-result" hx-swap="innerHTML">
      <input type="hidden" name="mode" value="days">
      <input class="input" name="days" type="number" min="1" placeholder="天数 (删除该天数前的消息)" style="width:100%">
      <button class="btn btn-danger" type="submit" style="margin-top:8px">删除 N 天前消息</button>
    </form>
  </section>
</div>
<div id="cleanup-result"></div>
{% endblock %}
```

(4) `app/web/templates/base.html` 导航行追加链接（在 `搜索` 之后）：

```html
  <a href="/">首页</a><a href="/customers">客户</a><a href="/knowledge">知识库</a><a href="/search">搜索</a><a href="/cleanup">清理</a>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/web/test_cleanup.py -v`
Expected: 5 passed。

同时跑 store/向量相关确认 Task 2/3 集成无回归：

Run: `pytest tests/storage/test_sqlite_store.py tests/storage/test_chroma_store.py -q`
Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add app/web/routes.py app/web/templates/cleanup.html app/web/templates/base.html tests/web/test_cleanup.py
git commit -m "feat: POST /api/cleanup 手动清理 (chat/days 校验 + 向量清理 + /cleanup 页)"
```

---

### Task 7: 采集器异常全局横幅

**Files:**
- Modify: `app/web/templates/base.html`（顶部横幅容器）
- Modify: `app/web/static/css/app.css`（横幅样式）
- Modify: `app/web/static/js/app.js`（自适应轮询）
- Create: `tests/web/test_banner.py`

**Interfaces:**
- Consumes: 既有 `GET /api/collector/status`（返回 `{"status": s, "alive": bool}`）；base.html 模板。
- Produces:
  - base.html `<div id="collector-banner" class="collector-banner" hidden>采集器异常</div>`
  - app.js 递归 setTimeout 轮询：在线 15000ms / 离线 5000ms；`alive=false` 显示横幅、恢复隐藏；fetch 异常也显示横幅 + 5s 快查

- [ ] **Step 1: 写失败测试 `tests/web/test_banner.py`**

```python
from fastapi.testclient import TestClient
from app.web.app import create_app


def test_collector_banner_renders_in_base(tmp_data):
    """3.1: base.html 含隐藏横幅容器, 任意继承页可渲染。"""
    html = TestClient(create_app()).get("/").text
    assert 'id="collector-banner"' in html
    assert "hidden" in html
    assert "采集器异常" in html
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/web/test_banner.py -v`
Expected: FAIL（base.html 无 `collector-banner`）。

- [ ] **Step 3: 最小实现**

(1) `app/web/templates/base.html` 全文替换为：

```html
<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><title>{% block title %}外贸客户知识库{% endblock %}</title>
<link rel="stylesheet" href="/static/css/app.css">
<script src="/static/js/htmx.min.js"></script>
<script src="/static/js/app.js"></script></head>
<body>
<div id="collector-banner" class="collector-banner" hidden>采集器异常</div>
<nav class="nav">
  <span class="brand">外贸客户知识库</span>
  <a href="/">首页</a><a href="/customers">客户</a><a href="/knowledge">知识库</a><a href="/search">搜索</a><a href="/cleanup">清理</a>
</nav>
<main class="container">{% block content %}{% endblock %}</main>
</body>
</html>
```

(2) `app/web/static/css/app.css` 末尾追加：

```css
/* ---- Collector banner (batch2) ---- */
#collector-banner {
  position: sticky;
  top: 0;
  z-index: 20;
  background: #dc2626;
  color: #fff;
  text-align: center;
  padding: 8px 16px;
  font-weight: 600;
}
```

(3) `app/web/static/js/app.js` 末尾追加：

```js
// batch2-search-cleanup-monitor: 采集器异常横幅 (D3, 自适应 15s/5s 轮询)
(function () {
  var banner = document.getElementById("collector-banner");
  if (!banner) return;
  var NORMAL_MS = 15000;
  var FAST_MS = 5000;
  function check() {
    fetch("/api/collector/status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var down = !d.alive;
        banner.hidden = !down;
        timer = setTimeout(check, down ? FAST_MS : NORMAL_MS);
      })
      .catch(function () {
        banner.hidden = false;
        timer = setTimeout(check, FAST_MS);
      });
  }
  var timer = setTimeout(check, 0);
})();
```

- [ ] **Step 4: 运行测试 + JS 走读（tasks 3.3）**

Run: `pytest tests/web/test_banner.py -v`
Expected: PASS。

JS 走读确认轮询逻辑（应命中 4 处：容器 id、端点、两个间隔）：

Run: `rg -n "collector-banner|api/collector/status|15000|5000" app/web/static/js/app.js`
Expected: 4 行匹配。

- [ ] **Step 5: 提交**

```bash
git add app/web/templates/base.html app/web/static/css/app.css app/web/static/js/app.js tests/web/test_banner.py
git commit -m "feat: 采集器异常全局横幅 (base.html 容器 + app.js 自适应 15s/5s 轮询)"
```

---

### Task 8: 回归验证

**Files:** 无（纯验证）

- [ ] **Step 1: 全量测试**

Run: `pytest -q`
Expected: 全部通过（新增 + 既有）。

- [ ] **Step 2: 语法编译检查**

Run: `python -m compileall -q app`
Expected: 无输出（退出码 0）。

- [ ] **Step 3: 代码走读（tasks 4.3）**

逐项核对：
1. 清理只动 messages / messages_fts / 消息向量，`profiles` / `documents` / `doc_chunks` 无 DELETE 语句（`rg -n "DELETE FROM (profiles|documents|doc_chunks)" app/storage app/web/routes.py` 应无命中）。
2. 搜索四源各自正确：`/api/search` 默认 JSON、`HX-Request` 返回片段；`%`/`_` 已转义。
3. 横幅轮询无泄漏：`app.js` 使用单条 `setTimeout` 链（上一轮完成才排下一轮），无 `setInterval` 累积；`base.html` 全局容器仅一份。

- [ ] **Step 4: 提交（如有走读修正，合并进本次）**

```bash
git add -A
git commit -m "chore: batch2-search-cleanup-monitor 回归验证通过"
```
