---
change: reply-workflow-optimization
design-doc: docs/superpowers/specs/2026-08-12-reply-workflow-optimization-design.md
base-ref: 20d4cfce05acbcf8ce2a5416bb7a3b0f3e80bfe0
---
# 回复链路优化 (reply-workflow-optimization) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把回复/重生成改为「提交即返回 task_id + 常驻 worker 异步执行 + HTMX 轮询」，并新增 CloudLLM client 复用、多轮会话上下文与一键复制。

**Architecture:** 引入 `reply_tasks` 常驻串行 worker（lifespan 启动 daemon 线程，独立 SQLite 连接），`POST /api/reply` 与 `/api/reply/regenerate` 只插入任务返回含 task_id 的轮询 HTML 片段；`GET /api/reply/status/{task_id}` 返回处理中片段（继续轮询）或最终结果（停止轮询）。`CloudLLM._client` 懒加载 + `threading.Lock` 跨任务复用；`reply_sessions` + `reply_session_messages` 记录会话，最近 10 轮历史拼入 system 提示词（仅主 generate 追加，regenerate 不追加）；reply_result.html 增加复制按钮（事件委托 + clipboard 回退）。

**Tech Stack:** FastAPI / Jinja2 / HTMX / sqlite3（WAL）/ threading / pytest（TestClient + monkeypatch）

## Global Constraints

- 不引入新外部依赖（无 Celery/Redis，任务队列 = SQLite 表 + 常驻 worker 线程）。
- 串行 worker：一次只有一个 LLM 调用；空循环 `time.sleep(1.0)`。
- 所有 SQLite 连接 `check_same_thread=False` + WAL + `busy_timeout=5000`。
- `reply_tasks` 表在 Design Doc schema 基础上**增加 `mode TEXT` 列**（`generate`/`regenerate`）——设计文档的 data flow 明确「仅主 generate 追加 user+assistant，regenerate 不追加」，仅凭 style 无法可靠区分任务类型，故需持久化 mode。
- 测试涉及 reply 的必须用 `with TestClient(create_app())` 触发 lifespan（否则 worker 不启动）。
- 前端沿用 HTMX，不新增 JS 轮询逻辑；复制用 `navigator.clipboard` + `execCommand` 回退。
- 回归基线：`pytest -q` 全量通过 + `compileall -q app` 通过。
- base-ref: `20d4cfce05acbcf8ce2a5416bb7a3b0f3e80bfe0`

## 与 tasks.md 的任务边界对应关系

| tasks.md 区段 | 对应 Task |
| --- | --- |
| §1 CloudLLM 复用客户端 (1.1/1.2/1.3) | Task 1 |
| §2.1 reply_tasks 表、§2.2 Store 任务方法、§3.1/3.2 会话表与 Store 方法 | Task 2 |
| §3.4 历史作为上下文传给 LLM | Task 3 |
| §2.3 后台线程执行 RAG+LLM（worker 侧） | Task 4 |
| §2.3 提交侧（插任务返回 task_id）、§2.4 status 接口、§3.3 会话 find-or-create | Task 5 |
| §2 遗留任务启动清理（D7）、lifespan 启动 worker + app.state.llm（D3） | Task 6 |
| §2.5 前端轮询、§3.5 session_id 透传、§4 一键复制 | Task 7 |
| §2.6 任务状态流转、§3.6 会话持久化（既有 4 测试迁移 + 新增集成） | Task 8 |
| §5 回归验证 | Task 9 |

## 文件结构总览

| 文件 | 变更 | 职责 |
| --- | --- | --- |
| `app/storage/schema.sql` | 修改 | 新增 reply_tasks / reply_sessions / reply_session_messages 三表 + 索引 |
| `app/storage/sqlite_store.py` | 修改 | 任务创建/查询/状态流转/清理、会话 find-or-create/追加/历史读取 |
| `app/llm/cloud_llm.py` | 修改 | `_client` 懒加载 + `threading.Lock` |
| `app/reply/generator.py` | 修改 | `generate_reply` 支持 history；新增 `NEXT_STYLE` |
| `app/web/worker.py` | 新建 | 常驻串行 worker（独立连接、任务执行、mode 语义） |
| `app/web/routes.py` | 修改 | 异步端点：POST reply/regenerate 插任务、GET status；`_get_chroma_store(app)` 提取 |
| `app/web/app.py` | 修改 | lifespan：`app.state.llm`、D7 清理、启动 worker |
| `app/web/templates/reply_polling.html` | 新建 | 轮询片段（every 1s，pending/running 复用） |
| `app/web/templates/reply_result.html` | 修改 | 复制按钮 + session_id 透传 |
| `app/web/templates/chat_messages.html` | 修改 | 生成回复按钮透传 session_id |
| `app/web/static/js/app.js` | 修改 | 事件委托绑定 `[data-copy]` 复制逻辑 |
| `tests/conftest.py` | 修改 | 新增 `reply_task_id` / `wait_reply_done` helper |
| `tests/llm/test_cloud_llm.py` | 新建 | client 复用 + 并发首建单测 |
| `tests/storage/test_reply_store.py` | 新建 | 任务/会话 Store 方法单测 |
| `tests/reply/test_generator.py` | 修改 | 新增 history 上下文测试 |
| `tests/reply/test_worker.py` | 新建 | `_execute_reply_task` 单测（append / 不 append / 失败） |
| `tests/web/test_reply_async.py` | 新建 | 提交侧 + 完整链路 + 遗留清理集成测试 |
| `tests/web/test_routes.py` | 修改 | 迁移 4 个既有 reply 测试 + 会话上下文集成测试 |

---

### Task 1: CloudLLM client 懒加载复用

**Files:**
- Modify: `app/llm/cloud_llm.py`
- Create: `tests/llm/test_cloud_llm.py`

**Interfaces:**
- Consumes: `app/llm/interfaces.py` 的 `LLM` 抽象接口；`app/config.py` 的 `settings.llm_provider / llm_model / llm_api_base / llm_api_key`。
- Produces: `CloudLLM(provider, model, api_base, api_key)` 构造函数签名不变；新增私有 `_get_client()`（`_client` 懒加载 + `_lock`），`generate(system, user, max_tokens=1024) -> str` 签名不变。后续 Task 4/6 依赖 `app.state.llm` 复用此实例。

- [x] **Step 1: 写失败测试 `tests/llm/test_cloud_llm.py`**

```python
# tests/llm/test_cloud_llm.py
import sys
import threading
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from app.llm.cloud_llm import CloudLLM


def _fake_openai_module(builds):
    class FakeCompletions:
        def create(self, model=None, max_tokens=None, messages=None):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    class FakeChat:
        @property
        def completions(self):
            return FakeCompletions()

    class FakeOpenAI:
        def __init__(self, *a, **k):
            builds.append("openai")

    return SimpleNamespace(OpenAI=FakeOpenAI, chat=FakeChat)


def test_reuses_single_client(monkeypatch):
    """1.3: 多次 generate 复用同一 client 实例。"""
    builds = []
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(builds))
    llm = CloudLLM(provider="openai", api_key="test-key")
    assert llm.generate("s1", "u1") == "ok"
    assert llm.generate("s2", "u2") == "ok"
    assert len(builds) == 1


def test_concurrent_first_call_builds_once(monkeypatch):
    """1.3: 并发首次调用仅创建一个 client (threading.Lock)。"""
    builds = []
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(builds))
    llm = CloudLLM(provider="openai", api_key="test-key")
    barrier = threading.Barrier(8)

    def work(_):
        barrier.wait()
        return llm.generate("s", "u")

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(work, range(8)))
    assert results == ["ok"] * 8
    assert len(builds) == 1
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest tests/llm/test_cloud_llm.py -v`
Expected: 两个用例均 FAIL（当前每次 `generate` 都新建 client，`len(builds) == 1` 断言失败）。

- [x] **Step 3: 最小实现**

`app/llm/cloud_llm.py` 全文替换为：

```python
# app/llm/cloud_llm.py
import os
import threading
from app.llm.interfaces import LLM
from app.config import settings


class CloudLLM(LLM):
    """云端 LLM。支持 anthropic 与 openai 两种 provider。
    openai 走 OpenAI 兼容接口 (可配 api_base 指向第三方/自建网关)。
    _client 懒加载缓存, 跨任务复用 (D3); threading.Lock 防并发首建竞态。"""

    def __init__(self, provider=None, model=None, api_base=None, api_key=None):
        self.provider = provider or settings.llm_provider
        self.model = model or settings.llm_model
        self.api_base = api_base or settings.llm_api_base
        self.api_key = api_key or settings.llm_api_key
        self._client = None
        self._lock = threading.Lock()

    def _resolve_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY")
        else:
            key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "未配置 LLM API key: 请设置 KB_LLM_API_KEY"
                f" (或 {self.provider} 对应的环境变量)")
        return key

    def _get_client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    if self.provider == "anthropic":
                        import anthropic
                        self._client = anthropic.Anthropic(
                            api_key=self._resolve_key(),
                            base_url=self.api_base,  # None=官方端点
                        )
                    else:
                        import openai
                        self._client = openai.OpenAI(
                            api_key=self._resolve_key(), base_url=self.api_base)
        return self._client

    def generate(self, system, user, max_tokens=1024):
        client = self._get_client()
        if self.provider == "anthropic":
            resp = client.messages.create(
                model=self.model, max_tokens=max_tokens,
                system=system, messages=[{"role": "user", "content": user}],
            )
            return resp.content[0].text
        else:
            # OpenAI 兼容接口 (官方 / 第三方网关 / 自建)
            resp = client.chat.completions.create(
                model=self.model, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return resp.choices[0].message.content
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest tests/llm/test_cloud_llm.py -v`
Expected: 2 passed。

- [x] **Step 5: 提交**

```bash
git add app/llm/cloud_llm.py tests/llm/test_cloud_llm.py
git commit -m "feat: CloudLLM client 懒加载复用 (threading.Lock 防并发首建)"
```

---


### Task 2: schema 三表 + SqliteStore 任务/会话方法

**Files:**
- Modify: `app/storage/schema.sql`
- Modify: `app/storage/sqlite_store.py`
- Create: `tests/storage/test_reply_store.py`

**Interfaces:**
- Consumes: 既有 `SqliteStore` 风格（`self.conn` 直接执行 + `self.conn.commit()`）；`app/config.py` 的 `settings.sqlite_path`。
- Produces: 以下方法（后续 Task 4 worker、Task 5 routes、Task 6 lifespan 依赖）：
  - `create_reply_task(customer_id: str, chat_id: str, message: str, style: str, session_id: str, mode: str) -> str`
  - `get_reply_task(task_id: str) -> dict | None`
  - `next_pending_reply_task() -> dict | None`（按 `created_at ASC, id ASC` 取最早 pending）
  - `update_reply_task(task_id: str, *, status=None, result=None, error=None) -> None`
  - `mark_legacy_reply_tasks_failed() -> None`
  - `find_or_create_reply_session(customer_id: str, chat_id: str) -> str`
  - `append_session_message(session_id: str, role: str, content: str) -> None`
  - `get_session_history(session_id: str, limit: int = 10) -> list[dict]`（最近 limit 条，正序返回）

- [ ] **Step 1: 写失败测试 `tests/storage/test_reply_store.py`**

```python
# tests/storage/test_reply_store.py
import sqlite3
from app.storage.sqlite_store import SqliteStore


def test_create_and_query_reply_task(tmp_data):
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    assert isinstance(sid, str) and sid
    assert store.find_or_create_reply_session("cust1", "c1") == sid  # find-or-create 幂等
    task_id = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    t = store.get_reply_task(task_id)
    assert t["status"] == "pending"
    assert t["mode"] == "generate"
    assert t["session_id"] == sid
    store.create_reply_task("cust1", "c1", "hi2", "concise", sid, "regenerate")
    assert store.next_pending_reply_task()["id"] == task_id  # 最早 pending 优先


def test_reply_task_status_transitions(tmp_data):
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    store.update_reply_task(tid, status="running")
    assert store.get_reply_task(tid)["status"] == "running"
    store.update_reply_task(tid, status="done", result='{"reply": "x"}')
    assert store.get_reply_task(tid)["result"] == '{"reply": "x"}'
    assert store.next_pending_reply_task() is None


def test_session_history_roundtrip_and_limit(tmp_data):
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    store.append_session_message(sid, "user", "m1")
    store.append_session_message(sid, "assistant", "a1")
    store.append_session_message(sid, "user", "m2")
    hist = store.get_session_history(sid, limit=10)
    assert [h["role"] for h in hist] == ["user", "assistant", "user"]
    # 超限取最新 10 条 (按 ts 控制唯一顺序)
    for i in range(12):
        store.conn.execute("INSERT INTO reply_session_messages VALUES(?,?,?,?,?)",
                           (f"x{i}", sid, "user", f"m{i}", 1000 + i))
    store.conn.commit()
    hist2 = store.get_session_history(sid, limit=10)
    assert len(hist2) == 10
    assert hist2[0]["content"] == "m2"   # 最新 10 条, 正序
    assert hist2[-1]["content"] == "m11"


def test_legacy_tasks_marked_failed(tmp_data):
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    store.conn.execute("UPDATE reply_tasks SET status='running' WHERE id=?", (tid,))
    store.conn.commit()
    store.mark_legacy_reply_tasks_failed()
    assert store.get_reply_task(tid)["status"] == "failed"
    assert "清理" in store.get_reply_task(tid)["error"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/storage/test_reply_store.py -v`
Expected: 全部 FAIL（`no such table: reply_sessions` / `'SqliteStore' object has no attribute 'find_or_create_reply_session'`）。

- [ ] **Step 3: 最小实现**

`app/storage/schema.sql` 末尾追加：

```sql
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
```

`app/storage/sqlite_store.py`：顶部 import 改为 `import sqlite3, time, json, uuid`，`_init_schema` 之后、`_row_to_msg` 之前追加以下方法：

```python
    # ---- reply-workflow-optimization: 回复任务 (D1/D7) ----
    def create_reply_task(self, customer_id, chat_id, message, style, session_id, mode):
        task_id = uuid.uuid4().hex
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO reply_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, customer_id, chat_id, message, style, session_id, mode,
             "pending", None, None, now, now))
        self.conn.commit()
        return task_id

    def get_reply_task(self, task_id):
        r = self.conn.execute("SELECT * FROM reply_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(r) if r else None

    def next_pending_reply_task(self):
        r = self.conn.execute(
            "SELECT * FROM reply_tasks WHERE status='pending' "
            "ORDER BY created_at ASC, id ASC LIMIT 1").fetchone()
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

    # ---- reply-workflow-optimization: 多轮会话 (D4) ----
    def find_or_create_reply_session(self, customer_id, chat_id):
        r = self.conn.execute(
            "SELECT id FROM reply_sessions WHERE customer_id=? AND chat_id=?",
            (customer_id, chat_id)).fetchone()
        if r:
            return r["id"]
        sid = uuid.uuid4().hex
        now = int(time.time())
        self.conn.execute("INSERT INTO reply_sessions VALUES(?,?,?,?,?)",
                          (sid, customer_id, chat_id, now, now))
        self.conn.commit()
        return sid

    def append_session_message(self, session_id, role, content):
        now = int(time.time())
        self.conn.execute("INSERT INTO reply_session_messages VALUES(?,?,?,?,?)",
                          (uuid.uuid4().hex, session_id, role, content, now))
        self.conn.commit()

    def get_session_history(self, session_id, limit=10):
        rows = self.conn.execute(
            "SELECT role, content FROM ("
            "  SELECT role, content, ts FROM reply_session_messages WHERE session_id=? "
            "  ORDER BY ts DESC, id DESC LIMIT ?) "
            "ORDER BY ts ASC, id ASC",
            (session_id, limit)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/storage/test_reply_store.py -v`
Expected: 4 passed。

- [ ] **Step 5: 提交**

```bash
git add app/storage/schema.sql app/storage/sqlite_store.py tests/storage/test_reply_store.py
git commit -m "feat: reply_tasks/reply_sessions 表与 SqliteStore 任务/会话方法"
```

---

### Task 3: generator 会话历史上下文

**Files:**
- Modify: `app/reply/generator.py`
- Modify: `tests/reply/test_generator.py`

**Interfaces:**
- Consumes: Task 2 的 `get_session_history` 返回 `[{"role": str, "content": str}]`；既有 `RagPipeline.run(query, customer_id, chat_id, system)`。
- Produces:
  - `NEXT_STYLE: dict`（`{"default": "concise", "concise": "warm", "warm": "formal", "formal": "default"}`，Task 5 routes 复用）
  - `generate_reply(pipeline, customer_id, chat_id, incoming_message, style="default", history=None) -> dict`（history 拼入 system，缺省 None 保持既有行为）
  - `regenerate_reply(..., previous_style="default", history=None)` 签名兼容（新加可选 history）

- [ ] **Step 1: 写失败测试（追加到 `tests/reply/test_generator.py` 末尾）**

```python
def test_generate_reply_includes_session_history(tmp_data):
    """3.4/3.6: 会话历史作为额外 system 上下文传入 LLM。"""
    seen = {}

    class CapturingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            seen["system"] = s
            return "回复"

    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), CapturingLLM())
    history = [{"role": "user", "content": "LED价格?"},
               {"role": "assistant", "content": "报价$5"}]
    generate_reply(pipe, "cust1", "c1", "何时到货?", history=history)
    assert "本次会话最近对话历史" in seen["system"]
    assert "LED价格" in seen["system"]
    assert "报价$5" in seen["system"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/reply/test_generator.py::test_generate_reply_includes_session_history -v`
Expected: FAIL（history 参数未实现，`AssertionError: "本次会话最近对话历史" not in ...`）。

- [ ] **Step 3: 最小实现**

`app/reply/generator.py` 全文替换为：

```python
# app/reply/generator.py
"""辅助回复: RAG 召回画像+历史+产品知识 + 当前消息 → 建议回复。
仅生成不自动发送。支持多候选: 通过 style 提示词让 LLM 产出不同表达。
多轮会话: history 为最近 N 轮 [{"role","content"}] 列表, 作为额外 system 上下文 (D4)。"""
from app.rag.pipeline import RagPipeline

REPLY_SYSTEM = """你是外贸业务员的回复助手。基于客户画像、历史聊天、产品知识, 针对客户最新消息生成建议回复。
要求: 专业、得体、可直接复制发送。给出一条主回复。{style}"""

REPLY_STYLE_VARIANTS = {
    "default": "",
    "concise": "语气简洁、直接，控制在三句话以内。",
    "warm": "语气热情友好，主动表达对客户需求的重视。",
    "formal": "语气正式严谨，突出专业与条理。",
}

NEXT_STYLE = {"default": "concise", "concise": "warm", "warm": "formal", "formal": "default"}


def _build_system(style_instruction: str, history: list[dict] | None) -> str:
    base = REPLY_SYSTEM.format(style=style_instruction)
    if history:
        lines = "\n".join(f"{h['role']}: {h['content']}" for h in history)
        base = f"{base}\n\n本次会话最近对话历史:\n{lines}"
    return base


def generate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                   incoming_message: str, style: str = "default",
                   history: list[dict] | None = None) -> dict:
    """返回 {reply, sources}。不发送。style 决定候选表达风格, history 提供会话上下文。"""
    style_instruction = REPLY_STYLE_VARIANTS.get(style, "")
    system = _build_system(style_instruction, history)
    result = pipeline.run(incoming_message, customer_id=customer_id, chat_id=chat_id,
                          system=system)
    return {"reply": result["answer"], "sources": result["sources"], "style": style}


def regenerate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                     incoming_message: str, previous_style: str = "default",
                     history: list[dict] | None = None) -> dict:
    """重新生成获得不同候选 (切换表达风格 + LLM 温度自然差异)。"""
    next_style = NEXT_STYLE.get(previous_style, "default")
    return generate_reply(pipeline, customer_id, chat_id, incoming_message,
                          style=next_style, history=history)
```

- [ ] **Step 4: 运行测试确认通过（含既有 3 个）**

Run: `pytest tests/reply/test_generator.py -v`
Expected: 4 passed（既有 3 个 + 新增 1 个）。

- [ ] **Step 5: 提交**

```bash
git add app/reply/generator.py tests/reply/test_generator.py
git commit -m "feat: 回复生成支持会话历史上下文 (history 拼入 system, 默认不启用)"
```

---


### Task 4: 常驻串行 reply worker

**Files:**
- Create: `app/web/worker.py`
- Create: `tests/reply/test_worker.py`

**Interfaces:**
- Consumes: Task 2 的 Store 方法；Task 3 的 `generate_reply`；`routes._build_store`（独立 SQLite 连接）、`routes._get_chroma_store(app)`（Task 5 提取）、`routes.get_reranker`、`routes.CloudLLM`；`app/rag/pipeline.py` 的 `RagPipeline`。
- Produces:
  - `worker_loop(app: FastAPI) -> None`（无限循环，Task 6 lifespan 调用）
  - `_execute_reply_task(app: FastAPI, store, task: dict) -> None`（可独立测试；mode=generate 追加 user+assistant，mode=regenerate 不追加；异常置 failed）

> **执行顺序说明：** worker.py 顶部 import 了 Task 5 才新增的 `routes._get_chroma_store`。本任务与 Task 5 紧耦合：**先完成 Task 5 的 routes 提取，再回到本任务跑测试**；两个任务可合并为一次提交，或按 Task 5 → Task 4 顺序各自提交。

- [ ] **Step 1: 写失败测试 `tests/reply/test_worker.py`**

```python
# tests/reply/test_worker.py
from app.web.app import create_app
from app.web import worker
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.rag.reranker import FakeReranker
from app.llm.interfaces import LLM


class FakeLLM(LLM):
    def __init__(self):
        self.prompts = []

    def generate(self, s, u, max_tokens=1024):
        self.prompts.append(s)
        return "worker回复"


def _make_app(store, llm):
    app = create_app()
    app.state.sqlite_store = store
    app.state.chroma_store = ChromaStore(embedding_fn=lambda t: [1.0] * 8)
    app.state.reranker = FakeReranker()
    app.state.llm = llm
    return app


def test_execute_generate_appends_history(tmp_data):
    """主 generate: 追加 user+assistant 到会话历史 (D4)。"""
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    llm = FakeLLM()
    app = _make_app(store, llm)
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    worker._execute_reply_task(app, store, store.get_reply_task(tid))
    done = store.get_reply_task(tid)
    assert done["status"] == "done"
    assert "worker回复" in done["result"]
    msgs = store.conn.execute("SELECT * FROM reply_session_messages ORDER BY ts").fetchall()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hi"


def test_execute_regenerate_does_not_append(tmp_data):
    """regenerate: 只读历史做上下文, 不追加 (替代候选不污染历史)。"""
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    store.append_session_message(sid, "user", "hi")
    store.append_session_message(sid, "assistant", "旧回复")
    llm = FakeLLM()
    app = _make_app(store, llm)
    tid = store.create_reply_task("cust1", "c1", "hi", "concise", sid, "regenerate")
    worker._execute_reply_task(app, store, store.get_reply_task(tid))
    assert store.get_reply_task(tid)["status"] == "done"
    msgs = store.conn.execute("SELECT * FROM reply_session_messages").fetchall()
    assert len(msgs) == 2  # 仍只有 generate 追加的 2 条
    assert "旧回复" in llm.prompts[0]  # 历史作为上下文传入


def test_execute_failure_marks_failed(tmp_data):
    """执行异常 → 任务 failed, error 截断可读。"""
    class BoomLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            raise RuntimeError("LLM 挂了")

    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    app = _make_app(store, BoomLLM())
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    worker._execute_reply_task(app, store, store.get_reply_task(tid))
    t = store.get_reply_task(tid)
    assert t["status"] == "failed"
    assert "LLM 挂了" in t["error"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/reply/test_worker.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.web.worker'`）。

- [ ] **Step 3: 最小实现**

`app/web/worker.py` 新建：

```python
# app/web/worker.py
"""reply_tasks 常驻串行 worker (D1/D7)。

worker 以 app 引用持有 app.state 共享资源 (chroma_store/reranker/llm),
使用独立 SQLite 连接 (routes._build_store 每次新建连接), 任务间复用。
串行消费保证一次只有一个 LLM 调用。
"""
import json
import logging
import time

from fastapi import FastAPI

from app.rag.pipeline import RagPipeline
from app.reply.generator import generate_reply
from app.web.routes import _build_store, _get_chroma_store, get_reranker, CloudLLM

POLL_INTERVAL_SEC = 1.0  # 空循环 sleep (Design Doc Open Question 已定 1s)
log = logging.getLogger("reply.worker")


def _resources(app: FastAPI, store):
    """worker 线程访问共享资源的统一入口 (审计 H: 不依赖 Request)。"""
    chroma = _get_chroma_store(app)
    reranker = getattr(app.state, "reranker", None) or get_reranker()
    llm = getattr(app.state, "llm", None) or CloudLLM()
    return RagPipeline(store, chroma, reranker, llm)


def _execute_reply_task(app: FastAPI, store, task: dict) -> None:
    """串行执行单个回复任务: running → 生成 → done/failed。
    mode=generate 追加 user+assistant 到会话; mode=regenerate 只读历史不追加 (D4)。"""
    task_id = task["id"]
    try:
        store.update_reply_task(task_id, status="running")
        pipe = _resources(app, store)
        history = store.get_session_history(task["session_id"]) if task["session_id"] else []
        result = generate_reply(pipe, task["customer_id"], task["chat_id"], task["message"],
                                style=task["style"], history=history)
        if task["mode"] == "generate":
            store.append_session_message(task["session_id"], "user", task["message"])
            store.append_session_message(task["session_id"], "assistant", result["reply"])
        store.update_reply_task(task_id, status="done",
                                result=json.dumps({**result, "session_id": task["session_id"]},
                                                  ensure_ascii=False))
    except Exception as e:
        log.warning("reply task %s 失败: %s", task_id, e)
        store.update_reply_task(task_id, status="failed", error=str(e)[:300])


def worker_loop(app: FastAPI) -> None:
    """常驻循环: 取最早 pending 任务串行执行; 空循环 sleep 1s。daemon 线程。"""
    store = _build_store()  # worker 独立 SQLite 连接, 任务间复用
    while True:
        try:
            task = store.next_pending_reply_task()
            if task is None:
                time.sleep(POLL_INTERVAL_SEC)
                continue
            _execute_reply_task(app, store, task)
        except Exception:
            log.exception("worker 循环异常")
            time.sleep(POLL_INTERVAL_SEC)
```

- [ ] **Step 4: 运行测试确认通过（需 Task 5 先完成 routes 提取）**

Run: `pytest tests/reply/test_worker.py -v`
Expected: 3 passed。

- [ ] **Step 5: 提交**

```bash
git add app/web/worker.py tests/reply/test_worker.py
git commit -m "feat: 常驻串行 reply worker (独立连接, mode 语义, 异常置 failed)"
```

---

### Task 5: routes 异步端点

**Files:**
- Modify: `app/web/routes.py`

**Interfaces:**
- Consumes: Task 2 Store 方法；Task 3 `NEXT_STYLE`；既有 `_build_store` / `get_embedding` / `get_reranker` / `CloudLLM` / `RagPipeline`。
- Produces:
  - `_get_chroma_store(app: FastAPI) -> ChromaStore`（request 无关，worker 复用）
  - `_embedding_ready(app)`（参数由 request 改为 app，调用处更新）
  - `_reply_params(request)` 增加返回 `session_id`
  - `_render_reply_result(request, customer_id, chat_id, message, result, session_id=None)`
  - `POST /api/reply`：find-or-create 会话 → 插入任务（mode=generate）→ 返回 reply_polling.html 轮询片段
  - `POST /api/reply/regenerate`：next_style 计算 → 插入任务（mode=regenerate）→ 返回轮询片段
  - `GET /api/reply/status/{task_id}`：pending/running → 轮询片段；done → reply_result.html；failed → 错误片段

- [ ] **Step 1: 写失败测试（新建 `tests/web/test_reply_async.py` 提交侧部分）**

```python
# tests/web/test_reply_async.py
from fastapi.testclient import TestClient
from app.web.app import create_app
from tests.conftest import reply_task_id


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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/web/test_reply_async.py -v`
Expected: FAIL（当前 POST 同步执行，响应不含 task_id / 无 status 端点 404）。

- [ ] **Step 3: 最小实现**

`app/web/routes.py` 修改点：

(1) 顶部 import 追加 `import json`（第 2 行 `import time, uuid, tempfile` 之后）：

```python
import time, uuid, tempfile
import sqlite3
import json
```

(2) import 区追加 `NEXT_STYLE`（修改第 21 行 `from app.reply.generator import ...`）：

```python
from app.reply.generator import generate_reply, regenerate_reply, NEXT_STYLE
```

(3) 把 `_embedding_ready` 与 `_chroma_store` 改为 app 参数版本，并提取 `_get_chroma_store`（替换第 37-74 行整段）：

```python
def _embedding_ready(app) -> bool:
    """等待模型预热完成 (有超时)。无 lifespan/无预热机制视为已就绪。"""
    ready = getattr(app.state, "embedding_ready", None)
    if ready is None:
        return True
    return ready.wait(WARMUP_TIMEOUT_SEC)


def _get_chroma_store(app) -> ChromaStore:
    """返回进程级 chroma 单例 (首次访问惰性创建, 复用 embedding_fn 便于测试替换)。

    模型预热未就绪时按 WARMUP_TIMEOUT_SEC 等待, 超时抛错由调用方降级。
    request 无关: worker 线程同样经此访问共享 chroma (审计 H)。
    """
    if not getattr(app.state, "chroma_store", None):
        if not _embedding_ready(app):
            raise RuntimeError("embedding 模型预热超时未就绪, 请稍后重试")
        emb = getattr(app.state, "embedding", None) or get_embedding()
        app.state.chroma_store = ChromaStore(embedding_fn=emb.embed)
    return app.state.chroma_store


def _chroma_store(request: Request) -> ChromaStore:
    return _get_chroma_store(request.app)
```

(4) `_reply_params` 增加 session_id（替换第 287-293 行）：

```python
async def _reply_params(request: Request) -> dict:
    """从 JSON body 或表单解析 {customer_id, chat_id, message, style, session_id}。"""
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
    else:
        body = await request.form()
    return {k: (body.get(k) or "") for k in ("customer_id", "chat_id", "message", "style", "session_id")}
```

(5) `_render_reply_result` 增加 session_id（替换第 296-304 行）：

```python
def _render_reply_result(request: Request, customer_id: str, chat_id: str,
                         message: str, result: dict, session_id: str | None = None):
    return request.app.state.templates.TemplateResponse(
        request, "reply_result.html",
        {"customer_id": customer_id, "chat_id": chat_id, "message": message,
         "reply": result.get("reply", ""),
         "sources": result.get("sources", []), "style": result.get("style", "default"),
         "session_id": session_id, "error": result.get("error")},
    )
```

(6) 新增 `_reply_session` helper（放在 `_render_reply_result` 之后）：

```python
async def _reply_session(request: Request, customer_id: str, chat_id: str,
                         session_id: str | None = None) -> str:
    """D4: 每 chat 一个会话; 显式 session_id 存在则沿用, 否则按 customer_id+chat_id find-or-create。"""
    store = _store(request)
    if session_id:
        row = store.conn.execute("SELECT id FROM reply_sessions WHERE id=?", (session_id,)).fetchone()
        if row:
            return session_id
    return store.find_or_create_reply_session(customer_id, chat_id)
```

(7) 重写 `POST /api/reply`（替换第 307-319 行）：

```python
@router.post("/api/reply")
async def reply(request: Request):
    p = await _reply_params(request)
    store = _store(request)
    session_id = await _reply_session(request, p["customer_id"], p["chat_id"], p.get("session_id"))
    task_id = store.create_reply_task(p["customer_id"], p["chat_id"], p["message"],
                                      p.get("style") or "default", session_id, mode="generate")
    return request.app.state.templates.TemplateResponse(
        request, "reply_polling.html", {"task_id": task_id})
```

(8) 重写 `POST /api/reply/regenerate`（替换第 322-335 行）：

```python
@router.post("/api/reply/regenerate")
async def reply_regenerate(request: Request):
    """reply-assist: 重生成任务 (mode=regenerate, worker 不追加会话历史)。"""
    p = await _reply_params(request)
    store = _store(request)
    session_id = await _reply_session(request, p["customer_id"], p["chat_id"], p.get("session_id"))
    next_style = NEXT_STYLE.get(p.get("style") or "default", "default")
    task_id = store.create_reply_task(p["customer_id"], p["chat_id"], p["message"],
                                      next_style, session_id, mode="regenerate")
    return request.app.state.templates.TemplateResponse(
        request, "reply_polling.html", {"task_id": task_id})
```

(9) 新增 status 端点（放在 regenerate 端点之后）：

```python
@router.get("/api/reply/status/{task_id}")
async def reply_status(request: Request, task_id: str):
    """D2: 轮询端点。pending/running → 处理中片段(继续轮询);
    done → 完整结果(停止轮询); failed → 错误片段。"""
    store = _store(request)
    task = store.get_reply_task(task_id)
    if task is None:
        return HTMLResponse('<p class="muted">任务不存在或已过期</p>')
    if task["status"] in ("pending", "running"):
        return request.app.state.templates.TemplateResponse(
            request, "reply_polling.html", {"task_id": task_id})
    if task["status"] == "failed":
        return _render_reply_result(request, task["customer_id"], task["chat_id"],
                                    task["message"],
                                    {"reply": "", "sources": [], "style": task["style"],
                                     "error": task["error"]},
                                    session_id=task["session_id"])
    result = json.loads(task["result"] or "{}")
    return _render_reply_result(request, task["customer_id"], task["chat_id"],
                                task["message"], result, session_id=task["session_id"])
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/web/test_reply_async.py -v`
Expected: 1 passed（提交侧，无需 worker）。

- [ ] **Step 5: 提交**

```bash
git add app/web/routes.py tests/web/test_reply_async.py
git commit -m "feat: /api/reply 与 /api/reply/regenerate 异步化 + /api/reply/status 轮询端点"
```

---


### Task 6: lifespan 启动 worker + D7 清理 + app.state.llm

**Files:**
- Modify: `app/web/app.py`

**Interfaces:**
- Consumes: Task 4 `worker_loop`；Task 2 `mark_legacy_reply_tasks_failed`；`CloudLLM`（app/llm/cloud_llm.py，Task 1 已改）。
- Produces: lifespan 启动时设置 `app.state.llm`（D3 单例）、调用 `mark_legacy_reply_tasks_failed()`（D7）、启动 `app.state.reply_worker` daemon 线程。

- [ ] **Step 1: 写失败测试（追加到 `tests/web/test_reply_async.py`）**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/web/test_reply_async.py -v`
Expected: `test_stale_tasks_marked_failed_on_startup` FAIL（`AttributeError: 'State' object has no attribute 'llm'`，且遗留任务未被清理）；`test_reply_full_lifecycle` FAIL（worker 未启动，轮询永不 done）。

- [ ] **Step 3: 最小实现**

`app/web/app.py` 修改：

(1) import 区追加：

```python
from app.config import settings
from app.llm.cloud_llm import CloudLLM
from app.web.routes import router, _build_store
from app.web.worker import worker_loop
```

(2) lifespan 修改（替换第 60-81 行）：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Web 进程级单例: 持有 sqlite store / llm 单例, 启动 reply worker, 关闭时释放连接。

    D3: app.state.llm 为进程级 CloudLLM 单例 (worker 与路由复用, client 懒加载)。
    D7: 启动时清理遗留 pending/running 任务为 failed (进程重启残留)。
    D1: 常驻 daemon worker 线程消费 reply_tasks。
    chroma store 由 routes/worker 首次访问时惰性创建并缓存 (embedding_fn 需走 routes 的
    get_embedding 以便测试 monkeypatch), 此处仅预置占位 None。
    embedding/reranker 在后台线程预热 (3.3), 不阻塞启动。
    """
    store = _build_store()
    app.state.sqlite_store = store
    app.state.chroma_store = None
    app.state.embedding = None
    app.state.reranker = None
    app.state.llm = CloudLLM()
    app.state.embedding_ready = threading.Event()
    store.mark_legacy_reply_tasks_failed()  # D7 (worker 起跑前清理)
    app.state.reply_worker = threading.Thread(target=worker_loop, args=(app,), daemon=True)
    app.state.reply_worker.start()
    threading.Thread(target=_warmup_models, args=(app,), daemon=True).start()
    try:
        yield
    finally:
        try:
            store.conn.close()
        except Exception:
            pass
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/web/test_reply_async.py -v`
Expected: 3 passed（提交侧 + 遗留清理 + 完整链路）。

同时跑 worker 与 generator 测试验证 Task 3/4 的集成：

Run: `pytest tests/reply/ tests/storage/test_reply_store.py tests/llm/test_cloud_llm.py -q`
Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add app/web/app.py tests/web/test_reply_async.py
git commit -m "feat: lifespan 启动 reply worker + 遗留任务清理 + app.state.llm 单例"
```

---

### Task 7: 前端模板与一键复制

**Files:**
- Create: `app/web/templates/reply_polling.html`
- Modify: `app/web/templates/reply_result.html`
- Modify: `app/web/templates/chat_messages.html`
- Modify: `app/web/static/js/app.js`

**Interfaces:**
- Consumes: Task 5 的 `reply_polling.html` 模板名、`_render_reply_result` 传入的 `session_id` 变量；routes `customer_chat_messages` 需新增 `session_id` context 变量。
- Produces:
  - `reply_polling.html`：轮询片段（`id="reply-task-{task_id}"`, `hx-get` + `hx-trigger="every 1s"` + `hx-swap="outerHTML"`）
  - `reply_result.html`：`[data-copy]` 复制按钮、regenerate `hx-vals` 携带 `session_id`
  - `chat_messages.html`：生成回复按钮 `hx-vals` 携带 `session_id`
  - `app.js`：document 级 click 事件委托处理 `[data-copy]`

- [ ] **Step 1: 写失败测试（HTML 渲染断言，追加到 `tests/web/test_reply_async.py`）**

```python
def test_reply_polling_template_has_every_1s(tmp_data):
    """2.5: 轮询片段含 every 1s 触发与 outerHTML 交换。"""
    from fastapi.templating import Jinja2Templates
    from pathlib import Path
    from app.web.app import create_app
    client = TestClient(create_app())
    t = Jinja2Templates(directory=str(Path("app/web/templates")))
    html = t.TemplateResponse("reply_polling.html", {"request": {}, "task_id": "abc123"}).body.decode()
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
        # regenerate 按钮透传 session_id (uuid hex 形式)
        import re
        assert re.search(r'"session_id"\s*:\s*"[0-9a-f]+"', done.text)


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
    html = client.get("/customers/cust1/chat/c1").text
    sid = store.find_or_create_reply_session("cust1", "c1")
    assert sid in html  # 页面携带 session_id
    assert "session_id" in html  # 生成回复按钮 hx-vals 透传
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/web/test_reply_async.py -v`
Expected: 3 个新用例 FAIL（模板缺 `reply_polling.html`、`reply_result.html` 缺 data-copy、chat 页缺 session_id）。

- [ ] **Step 3: 最小实现**

(1) 新建 `app/web/templates/reply_polling.html`：

```html
<div class="result-card" id="reply-task-{{ task_id }}"
     hx-get="/api/reply/status/{{ task_id }}"
     hx-trigger="every 1s"
     hx-swap="outerHTML">
  <p class="muted">正在生成回复…</p>
</div>
```

(2) `app/web/templates/reply_result.html` 全文替换为：

```html
<div class="result-card">
  {% if error %}
  <p><strong>回复生成失败</strong></p>
  <p class="muted">{{ error }}</p>
  {% else %}
  <p><strong>建议回复</strong> <span class="tag">风格: {{ style }}</span></p>
  <textarea class="input" id="reply-text" rows="4" style="width:100%">{{ reply }}</textarea>
  <div class="btn-row">
    <button class="btn" type="button" data-copy="reply-text">复制</button>
    <button class="btn" hx-post="/api/reply/regenerate"
            hx-vals='{"customer_id": "{{ customer_id }}", "chat_id": "{{ chat_id }}", "message": "{{ message|default('', true) }}", "style": "{{ style }}", "session_id": "{{ session_id|default('', true) }}" }'
            hx-target="closest div" hx-swap="innerHTML">重新生成</button>
  </div>
  <details class="sources">
    <summary>检索来源 ({{ sources|length }})</summary>
    <ul>
      {% for s in sources %}
      <li><small>{{ s.get('text', '')[:120] }}</small></li>
      {% else %}
      <li>无来源</li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}
</div>
```

(3) `app/web/templates/chat_messages.html` 生成回复按钮（第 21-23 行）改为携带 session_id：

```html
      <div id="reply-{{ m.id }}">
        <button class="btn btn-sm" hx-post="/api/reply"
                hx-vals='{"customer_id": "{{ customer_id }}", "chat_id": "{{ chat_id }}", "message": "{{ (m.body or '')|replace('"', '&quot;') }}", "session_id": "{{ session_id }}" }'
                hx-target="#reply-{{ m.id }}" hx-swap="innerHTML">生成回复</button>
      </div>
```

(4) `app/web/routes.py` 的 `customer_chat_messages`（第 160-182 行）context 增加 session_id：在 `return request.app.state.templates.TemplateResponse(...)` 的 dict 中追加 `"session_id": store.find_or_create_reply_session(customer_id, chat_id),`。

(5) `app/web/static/js/app.js` 末尾追加（DOMContentLoaded 之后的事件委托）：

```js
// reply-workflow-optimization: 一键复制 (事件委托, 兼容 htmx 动态插入的 DOM)
document.addEventListener("click", function (e) {
  var btn = e.target.closest ? e.target.closest("[data-copy]") : null;
  if (!btn) return;
  var target = document.getElementById(btn.getAttribute("data-copy"));
  if (!target) return;
  var text = (target.value !== undefined) ? target.value : target.textContent;
  function copied() {
    var old = btn.textContent;
    btn.textContent = "已复制";
    setTimeout(function () { btn.textContent = old; }, 1200);
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(copied);
  } else {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    copied();
  }
});
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/web/test_reply_async.py -v`
Expected: 6 passed。

- [ ] **Step 5: 提交**

```bash
git add app/web/templates/reply_polling.html app/web/templates/reply_result.html app/web/templates/chat_messages.html app/web/static/js/app.js app/web/routes.py tests/web/test_reply_async.py
git commit -m "feat: 前端轮询片段 + 一键复制 + session_id 透传"
```

---


### Task 8: 既有 reply 测试迁移 + 会话集成测试

**Files:**
- Modify: `tests/conftest.py`（helper）
- Modify: `tests/web/test_routes.py`

**Interfaces:**
- Consumes: Task 5/6 的异步端点与 lifespan；Task 3 的 history 上下文；helper `reply_task_id` / `wait_reply_done`（本任务 Step 1 写入 conftest）。
- Produces: 迁移后的 4 个既有测试（`test_reply_accepts_form_and_regenerate`、`test_reply_llm_failure_degrades_with_error`、`test_regenerate_failure_degrades_with_error`、`test_reply_degrades_when_embedding_warmup_times_out`）改为「提交→轮询→断言」；新增会话上下文集成测试。

> 说明：`tests/web/test_reply_async.py` 已在 Task 5/6/7 中引用 `reply_task_id` / `wait_reply_done`。为满足各任务自包含，helper 需在 Task 5 Step 1 之前就已存在——**本任务 Step 1 的 conftest 修改应作为 Task 5 的前置提交提前落地**；若按顺序执行，把本任务 Step 1 放到 Task 5 之前完成即可。

- [ ] **Step 1: 在 `tests/conftest.py` 追加 helper**

```python
import re
import time


def reply_task_id(html: str) -> str:
    """从轮询片段 HTML 提取 task_id (uuid4 hex)。"""
    m = re.search(r"/api/reply/status/([0-9a-f]+)", html)
    assert m, f"未找到 task_id: {html[:200]}"
    return m.group(1)


def wait_reply_done(client, task_id, timeout=8.0):
    """轮询 /api/reply/status 直到返回非"处理中"片段 (done/failed)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/reply/status/{task_id}")
        if "正在生成回复" not in r.text:
            return r
        time.sleep(0.2)
    raise AssertionError(f"task {task_id} 未在 {timeout}s 内完成")
```

- [ ] **Step 2: 写迁移测试（替换 `tests/web/test_routes.py` 中第 292-325 行、第 359-417 行、第 445-465 行的 4 个既有测试）**

替换后内容如下（4 个测试整体替换）：

```python
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
```

- [ ] **Step 3: 写会话上下文集成测试（追加到 `tests/web/test_routes.py` 末尾）**

```python
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
```

- [ ] **Step 4: 运行迁移 + 新增测试确认通过**

Run: `pytest tests/web/test_routes.py -v`
Expected: 全部通过（含 5 个 reply 相关测试 + 其余回归）。

- [ ] **Step 5: 提交**

```bash
git add tests/conftest.py tests/web/test_routes.py
git commit -m "test: reply 测试迁移为提交→轮询→断言, 新增会话上下文集成测试"
```

---

### Task 9: 回归验证

**Files:** 无（验证 + 走读）

**Interfaces:** 无。

- [ ] **Step 1: 全量测试**

Run: `pytest -q`
Expected: 全部通过（含迁移后 4 个既有 reply 测试、Task 1-8 全部新增测试）。

- [ ] **Step 2: 字节码编译检查**

Run: `python -m compileall -q app`
Expected: 无输出（exit 0）。

- [ ] **Step 3: 代码走读（审计清单）**

逐项确认（用 grep 验证，无残留）：
- **审计 A（共享 llm）**：`grep -rn "CloudLLM(" app/web/routes.py` —— routes 内不应再有 `CloudLLM()` 每请求新建（`/api/knowledge/upload` 的 Wiki 索引处可保留，属独立用途）；`app.state.llm` 在 lifespan 建立。
- **审计 H（worker 不依赖 Request）**：`app/web/worker.py` 全文无 `Request`；`_resources` 仅用 `app.state` 与 store。
- **审计 J（find-or-create）**：`_reply_session` 按 customer_id+chat_id 幂等。
- **审计 F（事件委托）**：`app.js` 的 `[data-copy]` 为 `document.addEventListener` 委托绑定。
- **审计 L（遗留清理）**：`mark_legacy_reply_tasks_failed()` 在 lifespan 于 worker 启动前调用（`app.py` 顺序：先清理后 `start()`）。
- **审计 I（TestClient lifespan）**：所有 reply 测试用 `with TestClient(create_app())`。
- 无每请求新建 client 残留：`grep -rn "CloudLLM(" app/ | grep -v "app.state.llm"`。

Run: `git status` —— 确认仅本 change 相关文件改动。

- [ ] **Step 4: 提交（如有测试代码调整）**

若 Step 1 有失败需修复，修复后：

```bash
git add -A
git commit -m "fix: 回归问题修复"
```

否则跳过本步骤。

---

## Self-Review 记录

**1. Spec coverage（对照 Design Doc 决策）**
- D1 串行 worker → Task 4/6 ✓；D2 提交即返回+轮询 → Task 5/7 ✓；D3 client 复用+app.state.llm → Task 1/6 ✓；D4 多轮会话 → Task 2/3/5/8 ✓；D5 一键复制 → Task 7 ✓；D6 测试策略与既有迁移 → Task 8 ✓；D7 遗留清理 → Task 6 ✓。Open Question（空循环 1s）→ Task 4 `POLL_INTERVAL_SEC = 1.0` ✓。
- Data Flow 全部端点覆盖：POST reply（Task 5）、worker 执行（Task 4）、status 端点（Task 5）、reply_result 渲染（Task 5/7）。
- Migration Plan 回归项（`pytest -q` + `compileall -q app`）→ Task 9。

**2. Placeholder scan**：所有代码步骤均含完整可粘贴实现；无 "TBD/TODO" 或"实现适当错误处理"类占位。

**3. Type consistency**：
- Store 方法名在 Task 2 定义，Task 4/5/6 使用一致（`create_reply_task`/`get_reply_task`/`next_pending_reply_task`/`update_reply_task`/`mark_legacy_reply_tasks_failed`/`find_or_create_reply_session`/`append_session_message`/`get_session_history`）。
- `_get_chroma_store(app)` 在 Task 5 定义、Task 4 worker 消费，签名一致；`_render_reply_result` 的 `session_id` 参数在 Task 5 定义、Task 7 模板消费，命名一致。
- generator 的 `history: list[dict] | None` 与 Store 的 `get_session_history` 返回类型一致。
- helper `reply_task_id` / `wait_reply_done` 在 conftest 定义，Task 5/6/7/8 全量使用，命名一致。

**4. 执行顺序提示**：conftest helper（Task 8 Step 1）与 routes 异步端点（Task 5）存在前置依赖，建议按 `Task 8 Step 1 → Task 1 → Task 2 → Task 3 → Task 5 → Task 4 → Task 6 → Task 7 → Task 8 Step 2-5 → Task 9` 执行；若用 subagent 并行，注意 Task 4 依赖 Task 5 的 `_get_chroma_store`。

