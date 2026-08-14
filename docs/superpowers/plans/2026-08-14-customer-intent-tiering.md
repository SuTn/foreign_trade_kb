---
change: customer-intent-tiering
design-doc: docs/superpowers/specs/2026-08-14-customer-intent-tiering-design.md
base-ref: e2a1b9e5eb93acc9630e45ee1d7e1bf8ec2fe3ab
archived-with: 2026-08-14-customer-intent-tiering
---

# 客户自动分层标签体系 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增客户意向分层分析能力，LLM 按初版规则将客户划分为 A/B/C/D 意向等级并生成业务标签，记录分层历史，支持人工覆盖与分层范围筛选，前端展示等级徽章与筛选。

**Architecture:** 独立分层模块 `app/profile/tiering.py` 复用 `build_customer_summary` 生成摘要 → LLM 输出结构化 JSON（`intent_level` + `tags`）→ 写入 `profiles`（auto 来源，复用 manual 保护）→ 追加 `customer_tier_history`。异步执行通过新增 `tiering_tasks` 表 + 扩展现有 `worker_loop` 串行消费（回复优先）。前端纯 JS 等级筛选叠加现有国家/公司筛选。

**Tech Stack:** Python 3 / FastAPI / SQLite / Jinja2 / HTMX / 纯前端 JS

## Global Constraints

- 分层等级仅允许 `A`/`B`/`C`/`D` 四档，解析失败或无数据回退「未分层」（不写 `intent_level` 或写空）。
- `profiles` 写入必须走 `upsert_profile_field(..., source="auto")`，复用 manual 保护（`sqlite_store.py:60`）。
- 分层历史 `source` 取值仅 `auto`（自动分层）或 `manual`（人工调整）。
- 标签以逗号分隔字符串存 `profiles` 的 `tags` 字段；预定义标签集 + LLM 自由补充。
- 分层任务在 worker 中执行时，每处理一个客户先检查 pending `reply_tasks`，有则先消费回复（回复优先）。
- 单次分层任务客户数上限可配（默认 50），超限分批。
- 摘要长度复用 `settings.profile_summary_messages`（默认 30）。
- 近期活跃默认 30 天可配（`settings.tiering_active_days`）。
- 所有 SQL 迁移幂等（`CREATE TABLE IF NOT EXISTS`）。

---

### Task 1: 存储层 — 分层历史表 + 分层任务表

**Files:**
- Modify: `app/storage/schema.sql`（追加两表定义）
- Modify: `app/storage/sqlite_store.py`（新增读写方法）
- Test: `tests/storage/test_tiering_store.py`（新建）

**Interfaces:**
- Consumes: 现有 `SqliteStore`、`settings`。
- Produces:
  - `store.add_tier_history(customer_id: str, intent_level: str, tags: str, source: str) -> None`
  - `store.get_tier_history(customer_id: str) -> list[dict]`（含 `id, customer_id, intent_level, tags, source, created_at`，按 `created_at` 升序）
  - `store.create_tiering_task(customer_ids: list[str]) -> str`（返回 task_id）
  - `store.get_tiering_task(task_id: str) -> dict | None`
  - `store.next_pending_tiering_task() -> dict | None`
  - `store.update_tiering_task(task_id: str, *, status=None, progress=None, result=None, error=None) -> None`
  - `store.list_recent_active_customers(days: int) -> list[str]`（近期活跃客户 id，有消息且最近消息在 N 天内）

- [ ] **Step 1: 写失败测试**

创建 `tests/storage/test_tiering_store.py`：

```python
# tests/storage/test_tiering_store.py
import time
from app.storage.sqlite_store import SqliteStore


def test_tier_history_roundtrip(tmp_data):
    store = SqliteStore()
    store.add_tier_history("cust1", "A", "已购,议价中", "auto")
    store.add_tier_history("cust1", "B", "待跟进", "manual")
    hist = store.get_tier_history("cust1")
    assert len(hist) == 2
    assert hist[0]["intent_level"] == "A"
    assert hist[0]["source"] == "auto"
    assert hist[1]["intent_level"] == "B"
    assert hist[1]["source"] == "manual"
    assert hist[0]["tags"] == "已购,议价中"


def test_tier_history_empty(tmp_data):
    store = SqliteStore()
    assert store.get_tier_history("nobody") == []


def test_tiering_task_lifecycle(tmp_data):
    store = SqliteStore()
    tid = store.create_tiering_task(["cust1", "cust2"])
    t = store.get_tiering_task(tid)
    assert t["status"] == "pending"
    assert t["customer_ids"] == ["cust1", "cust2"]
    assert store.next_pending_tiering_task()["id"] == tid
    store.update_tiering_task(tid, status="running", progress=1)
    store.update_tiering_task(tid, status="done", progress=2,
                              result='{"tiered": 2}')
    done = store.get_tiering_task(tid)
    assert done["status"] == "done"
    assert done["progress"] == 2
    assert done["result"] == '{"tiered": 2}'
    assert store.next_pending_tiering_task() is None


def test_recent_active_customers(tmp_data):
    store = SqliteStore()
    now = int(time.time())
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "A", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c2", "B", "2", None, None, 0, None))
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c3", "C", "3", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch1", "c1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch2", "c2", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch1", "a1", "ch1", "A", "single", 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch2", "a1", "ch2", "B", "single", 0))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m1", "a1", "ch1", 0, "x", now - 100, "chat", "hi", 1, 0, None))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m2", "a1", "ch2", 0, "x", now - 100 * 86400, "chat", "old", 1, 0, None))
    store.conn.commit()
    active = store.list_recent_active_customers(days=30)
    assert active == ["c1"]  # c1 近期活跃; c2 超 30 天; c3 无消息
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/storage/test_tiering_store.py -v`
Expected: FAIL（`AttributeError: 'SqliteStore' object has no attribute 'add_tier_history'`）

- [ ] **Step 3: 实现 schema 迁移**

在 `app/storage/schema.sql` 末尾追加：

```sql
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
```

- [ ] **Step 4: 实现存储方法**

在 `app/storage/sqlite_store.py` 末尾（`bump_scan_request_attempts` 之后）追加：

```python
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

    def get_tiering_task(self, task_id):
        r = self.conn.execute("SELECT * FROM tiering_tasks WHERE id=?", (task_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["customer_ids"] = json.loads(d["customer_ids"] or "[]")
        return d

    def next_pending_tiering_task(self):
        r = self.conn.execute(
            "SELECT * FROM tiering_tasks WHERE status='pending' "
            "ORDER BY created_at ASC, rowid ASC LIMIT 1").fetchone()
        if not r:
            return None
        d = dict(r)
        d["customer_ids"] = json.loads(d["customer_ids"] or "[]")
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
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_tiering_store.py -v`
Expected: PASS（4 个用例全绿）

- [ ] **Step 6: 提交**

```bash
git add app/storage/schema.sql app/storage/sqlite_store.py tests/storage/test_tiering_store.py
git commit -m "feat: 分层历史表 + 分层任务表存储层 (customer-intent-tiering)"
```

---

### Task 2: 分层分析模块 `tiering.py`

**Files:**
- Create: `app/profile/tiering.py`
- Modify: `app/config.py`（新增 `tiering_active_days`、`tiering_max_customers`）
- Test: `tests/profile/test_tiering.py`（新建）

**Interfaces:**
- Consumes: `build_customer_summary`（`app/profile/service.py:40`）、`store.upsert_profile_field`、`store.add_tier_history`、`store.list_recent_active_customers`（Task 1）。
- Produces:
  - `PREDEFINED_TAGS: list[str]`
  - `tier_customer(store, llm, customer_id) -> dict`（返回 `{"intent_level": str, "tags": str}`；无数据/解析失败返回 `{"intent_level": "", "tags": ""}`）
  - `tier_customers(store, llm, customer_ids: list[str]) -> dict`（返回 `{"tiered": int, "untiered": int}`）

- [ ] **Step 1: 写失败测试**

创建 `tests/profile/test_tiering.py`：

```python
# tests/profile/test_tiering.py
import time
from app.profile.tiering import tier_customer, tier_customers, PREDEFINED_TAGS
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Message
from app.llm.interfaces import LLM


class FakeLLM(LLM):
    def __init__(self, resp): self.resp = resp
    def generate(self, s, u, max_tokens=1024): return self.resp


def _msg(mid, chat, from_me, body, ts):
    return Message(mid, "a1", chat, from_me, None, ts, "chat", body, bool(body), int(time.time()))


def _link(store, chat, cust):
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", chat, cust, 0.9, 0, 0))
    store.conn.commit()


def test_predefined_tags_defined():
    assert "已购" in PREDEFINED_TAGS
    assert "议价中" in PREDEFINED_TAGS


def test_tier_customer_writes_profile_and_history(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "确认车型 LED-100, 谈付款", 100))
    _link(store, "c1", "cust1")
    llm = FakeLLM('{"intent_level": "A", "tags": "已购,议价中"}')
    r = tier_customer(store, llm, "cust1")
    assert r["intent_level"] == "A"
    assert r["tags"] == "已购,议价中"
    prof = {p.field: p.value for p in store.get_profile("cust1")}
    assert prof["intent_level"] == "A"
    assert prof["tags"] == "已购,议价中"
    hist = store.get_tier_history("cust1")
    assert len(hist) == 1
    assert hist[0]["intent_level"] == "A"
    assert hist[0]["source"] == "auto"


def test_tier_customer_does_not_override_manual(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "want LED price", 100))
    _link(store, "c1", "cust1")
    store.upsert_profile_field("cust1", "intent_level", "B", "manual")
    llm = FakeLLM('{"intent_level": "A", "tags": "已购"}')
    tier_customer(store, llm, "cust1")
    prof = {p.field: p.value for p in store.get_profile("cust1")}
    assert prof["intent_level"] == "B"  # manual 不被 auto 覆盖
    # 历史仍记录本次 auto 分层结果
    hist = store.get_tier_history("cust1")
    assert hist[0]["intent_level"] == "A"
    assert hist[0]["source"] == "auto"


def test_tier_customer_no_data_marks_untiered(tmp_data):
    store = SqliteStore()
    llm = FakeLLM('{"intent_level": "A", "tags": "已购"}')
    r = tier_customer(store, llm, "cust1")  # 无消息
    assert r["intent_level"] == ""
    assert r["tags"] == ""
    assert store.get_tier_history("cust1") == []


def test_tier_customer_parse_failure_falls_back(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "hello", 100))
    _link(store, "c1", "cust1")
    llm = FakeLLM("不是 JSON")
    r = tier_customer(store, llm, "cust1")
    assert r["intent_level"] == ""
    assert r["tags"] == ""
    assert store.get_tier_history("cust1") == []


def test_tier_customers_batch(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "确认车型, 谈付款", 100))
    store.upsert_message(_msg("m2", "c2", False, "一般询价", 100))
    _link(store, "c1", "cust1")
    _link(store, "c2", "cust2")
    llm = FakeLLM('{"intent_level": "A", "tags": "已购"}')
    r = tier_customers(store, llm, ["cust1", "cust2"])
    assert r["tiered"] == 2
    assert r["untiered"] == 0
    assert len(store.get_tier_history("cust1")) == 1
    assert len(store.get_tier_history("cust2")) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/profile/test_tiering.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.profile.tiering'`）

- [ ] **Step 3: 新增配置项**

在 `app/config.py` 的「客户画像/分析」区块追加：

```python
    # 客户分层 (customer-intent-tiering)
    tiering_active_days: int = 30   # 近期活跃客户默认天数
    tiering_max_customers: int = 50  # 单次分层任务客户数上限
```

- [ ] **Step 4: 实现 tiering.py**

创建 `app/profile/tiering.py`：

```python
# app/profile/tiering.py
"""客户意向分层: 复用摘要构建 → LLM 输出 A/B/C/D 等级 + 标签 → 写 profiles + 历史表。"""
import json
from app.storage.interfaces import StructuredStore
from app.llm.interfaces import LLM
from app.profile.service import build_customer_summary

PREDEFINED_TAGS = ["已购", "意向车型", "议价中", "待跟进", "需回访", "沉睡", "垃圾询盘"]

TIER_PROMPT = """你是外贸客户意向分层助手。根据客户聊天摘要, 判定意向等级并生成业务标签。

等级规则:
- A 类 (高意向): 明确确认车型 / 议价 / 索要单证 / 约定看车 / 谈付款
- B 类 (中意向): 详细询价 / 多次沟通 / 询问物流交期
- C 类 (低意向): 一般询价 / 简单咨询
- D 类 (无效/沉睡): 垃圾询盘 / 长期无回复

标签: 从预定义集 [{tags}] 中选择, 可补充自定义标签, 用逗号分隔。

只输出 JSON 对象, 格式: {{"intent_level": "A|B|C|D", "tags": "标签1,标签2"}}
聊天摘要: {summary}"""


def _parse_result(resp: str) -> dict:
    """解析 LLM 输出; 失败返回空 dict (回退未分层)。"""
    try:
        data = json.loads(resp)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    level = str(data.get("intent_level", "")).strip().upper()
    if level not in ("A", "B", "C", "D"):
        return {}
    tags = str(data.get("tags", "")).strip()
    return {"intent_level": level, "tags": tags}


def tier_customer(store: StructuredStore, llm: LLM, customer_id: str) -> dict:
    """对单个客户分层: 摘要 → LLM → 写 profiles(auto) + 历史(auto)。
    无聊天数据或解析失败回退未分层, 不阻塞其他客户。"""
    summary = build_customer_summary(store, customer_id)
    if not summary:
        return {"intent_level": "", "tags": ""}
    resp = llm.generate("你是外贸客户意向分层助手",
                        TIER_PROMPT.format(tags=",".join(PREDEFINED_TAGS), summary=summary))
    result = _parse_result(resp)
    if not result:
        return {"intent_level": "", "tags": ""}
    store.upsert_profile_field(customer_id, "intent_level", result["intent_level"], "auto")
    store.upsert_profile_field(customer_id, "tags", result["tags"], "auto")
    store.add_tier_history(customer_id, result["intent_level"], result["tags"], "auto")
    return result


def tier_customers(store: StructuredStore, llm: LLM, customer_ids: list[str]) -> dict:
    """批量分层入口。返回 {tiered, untiered}。单个失败不阻塞其余。"""
    tiered = 0
    untiered = 0
    for cid in customer_ids:
        try:
            r = tier_customer(store, llm, cid)
        except Exception:
            r = {"intent_level": "", "tags": ""}
        if r["intent_level"]:
            tiered += 1
        else:
            untiered += 1
    return {"tiered": tiered, "untiered": untiered}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/profile/test_tiering.py -v`
Expected: PASS（6 个用例全绿）

- [ ] **Step 6: 提交**

```bash
git add app/profile/tiering.py app/config.py tests/profile/test_tiering.py
git commit -m "feat: 分层分析模块 tier_customer/tier_customers (customer-intent-tiering)"
```

---

### Task 3: Web API — 分层触发 + 历史查询 + 等级筛选

**Files:**
- Modify: `app/web/routes.py`
- Test: `tests/web/test_tiering_api.py`（新建）

**Interfaces:**
- Consumes: `tier_customers`（Task 2）、`store.create_tiering_task`/`get_tiering_task`/`get_tier_history`/`list_recent_active_customers`（Task 1）、`settings.tiering_active_days`/`tiering_max_customers`（Task 2）。
- Produces:
  - `POST /api/tiering/analyze`：body 可选 `customer_ids`（list[str]），缺省=近期活跃客户；返回 `{"task_id": str}`。
  - `GET /api/tiering/status/{task_id}`：返回 `{"status", "progress", "result", "error"}`。
  - `GET /api/tiering/history/{customer_id}`：返回 `{"history": [dict]}`。
  - `GET /customers` 渲染新增 `tier_levels`（去重等级列表）供前端下拉。

- [ ] **Step 1: 写失败测试**

创建 `tests/web/test_tiering_api.py`：

```python
# tests/web/test_tiering_api.py
from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore


def test_analyze_creates_task(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/tiering/analyze", json={"customer_ids": ["c1"]})
    assert r.status_code == 200
    assert "task_id" in r.json()
    task = store.get_tiering_task(r.json()["task_id"])
    assert task["status"] == "pending"
    assert task["customer_ids"] == ["c1"]


def test_analyze_defaults_to_recent_active(tmp_data):
    import time
    store = SqliteStore()
    now = int(time.time())
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch1", "c1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch1", "a1", "ch1", "A", "single", 0))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m1", "a1", "ch1", 0, "x", now - 1000, "chat", "hi", 1, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/tiering/analyze", json={})
    assert r.status_code == 200
    task = store.get_tiering_task(r.json()["task_id"])
    assert task["customer_ids"] == ["c1"]


def test_tiering_status_endpoint(tmp_data):
    store = SqliteStore()
    tid = store.create_tiering_task(["c1"])
    store.update_tiering_task(tid, status="done", progress=1, result='{"tiered": 1}')
    client = TestClient(create_app())
    r = client.get(f"/api/tiering/status/{tid}")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "done"
    assert j["progress"] == 1
    assert j["result"] == '{"tiered": 1}'


def test_tiering_history_endpoint(tmp_data):
    store = SqliteStore()
    store.add_tier_history("c1", "A", "已购", "auto")
    store.add_tier_history("c1", "B", "待跟进", "manual")
    client = TestClient(create_app())
    r = client.get("/api/tiering/history/c1")
    assert r.status_code == 200
    j = r.json()
    assert len(j["history"]) == 2
    assert j["history"][0]["intent_level"] == "A"


def test_customers_page_has_tiering_levels(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",
                       ("c1", "intent_level", "A", "auto", 0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/customers").text
    assert 'value="A"' in html  # 等级下拉含 A
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/web/test_tiering_api.py -v`
Expected: FAIL（404 或断言失败，路由未实现）

- [ ] **Step 3: 实现路由**

在 `app/web/routes.py` 末尾（`export_v` 之后）追加：

```python
# ---- customer-intent-tiering: 分层分析 API ----
@router.post("/api/tiering/analyze")
async def tiering_analyze(request: Request):
    """创建分层任务。body 可选 customer_ids (缺省=近期活跃客户)。"""
    store = _store(request)
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    customer_ids = body.get("customer_ids") if isinstance(body, dict) else None
    if not customer_ids:
        customer_ids = store.list_recent_active_customers(settings.tiering_active_days)
    if not customer_ids:
        return {"task_id": None, "error": "无待分层客户"}
    customer_ids = customer_ids[:settings.tiering_max_customers]
    task_id = store.create_tiering_task(customer_ids)
    return {"task_id": task_id}


@router.get("/api/tiering/status/{task_id}")
async def tiering_status(task_id: str, request: Request):
    store = _store(request)
    task = store.get_tiering_task(task_id)
    if task is None:
        return {"status": "not_found"}
    return {"status": task["status"], "progress": task["progress"],
            "result": task["result"], "error": task["error"]}


@router.get("/api/tiering/history/{customer_id}")
async def tiering_history(customer_id: str, request: Request):
    store = _store(request)
    return {"customer_id": customer_id, "history": store.get_tier_history(customer_id)}
```

- [ ] **Step 4: 扩展 /customers 响应**

修改 `app/web/routes.py` 的 `customers` 路由（约 343-360 行），在返回模板前追加等级列表：

```python
    tiering_levels = [r[0] for r in store.conn.execute(
        "SELECT DISTINCT value FROM profiles WHERE field='intent_level' "
        "AND value IS NOT NULL AND value != '' ORDER BY value").fetchall()]
    return request.app.state.templates.TemplateResponse(
        request, "customers.html",
        {"customers": rows, "profiles_by_customer": profiles_by_customer,
         "countries": countries, "companies": companies,
         "tiering_levels": tiering_levels})
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_tiering_api.py -v`
Expected: PASS（5 个用例全绿）

- [ ] **Step 6: 提交**

```bash
git add app/web/routes.py tests/web/test_tiering_api.py
git commit -m "feat: 分层分析/状态/历史 API + 客户列表等级下拉 (customer-intent-tiering)"
```

---

### Task 4: Worker 扩展 — 串行消费 tiering_tasks（回复优先）

**Files:**
- Modify: `app/web/worker.py`
- Test: `tests/reply/test_worker_tiering.py`（新建）

**Interfaces:**
- Consumes: `tier_customers`（Task 2）、`store.next_pending_tiering_task`/`update_tiering_task`/`next_pending_reply_task`（Task 1）。
- Produces: `_execute_tiering_task(app, store, task) -> None`（供 worker_loop 调用）。

- [ ] **Step 1: 写失败测试**

创建 `tests/reply/test_worker_tiering.py`：

```python
# tests/reply/test_worker_tiering.py
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
        return '{"intent_level": "A", "tags": "已购"}'


def _make_app(store, llm):
    app = create_app()
    app.state.sqlite_store = store
    app.state.chroma_store = ChromaStore(embedding_fn=lambda t: [1.0] * 8)
    app.state.reranker = FakeReranker()
    app.state.llm = llm
    return app


def test_execute_tiering_task_marks_done(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch1", "c1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch1", "a1", "ch1", "A", "single", 0))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m1", "a1", "ch1", 0, "x", 1, "chat", "want LED", 1, 0, None))
    store.conn.commit()
    llm = FakeLLM()
    app = _make_app(store, llm)
    tid = store.create_tiering_task(["c1"])
    worker._execute_tiering_task(app, store, store.get_tiering_task(tid))
    done = store.get_tiering_task(tid)
    assert done["status"] == "done"
    assert done["progress"] == 1
    assert "tiered" in done["result"]
    assert len(store.get_tier_history("c1")) == 1


def test_execute_tiering_failure_marks_failed(tmp_data):
    class BoomLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            raise RuntimeError("LLM 挂了")

    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "ch1", "c1", 0.9, 0, 0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",
                       ("ch1", "a1", "ch1", "A", "single", 0))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("m1", "a1", "ch1", 0, "x", 100, "chat", "hi", 1, 0, None))
    store.conn.commit()
    app = _make_app(store, BoomLLM())
    tid = store.create_tiering_task(["c1"])
    worker._execute_tiering_task(app, store, store.get_tiering_task(tid))
    t = store.get_tiering_task(tid)
    assert t["status"] == "failed"
    assert "LLM 挂了" in t["error"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/reply/test_worker_tiering.py -v`
Expected: FAIL（`AttributeError: module 'app.web.worker' has no attribute '_execute_tiering_task'`）

- [ ] **Step 3: 实现 worker 扩展**

在 `app/web/worker.py` 中，`_execute_reply_task` 之后、`worker_loop` 之前追加：

```python
def _execute_tiering_task(app: FastAPI, store, task: dict) -> None:
    """串行执行单个分层任务: running → 逐客户分层 → done/failed。
    每处理一个客户前检查 pending reply_tasks, 有则先消费回复 (回复优先, D7)。"""
    task_id = task["id"]
    try:
        store.update_tiering_task(task_id, status="running")
        from app.profile.tiering import tier_customers
        llm = getattr(app.state, "llm", None) or CloudLLM()
        customer_ids = task["customer_ids"]
        total = len(customer_ids)
        for i, cid in enumerate(customer_ids, start=1):
            reply = store.next_pending_reply_task()
            if reply is not None:
                _execute_reply_task(app, store, reply)
            tier_customers(store, llm, [cid])
            store.update_tiering_task(task_id, progress=i)
        store.update_tiering_task(task_id, status="done",
                                  result=json.dumps({"tiered": total}, ensure_ascii=False))
    except Exception as e:
        log.warning("tiering task %s 失败: %s", task_id, e)
        store.update_tiering_task(task_id, status="failed", error=str(e)[:300])
```

- [ ] **Step 4: 修改 worker_loop 消费两表**

修改 `app/web/worker.py` 的 `worker_loop`（约 51-63 行），在消费 reply 之前先检查 tiering 任务：

```python
def worker_loop(app: FastAPI) -> None:
    """常驻循环: 串行消费 reply_tasks 与 tiering_tasks (回复优先); 空循环 sleep 1s。"""
    store = _build_store()
    while True:
        try:
            task = store.next_pending_reply_task()
            if task is not None:
                _execute_reply_task(app, store, task)
                continue
            tier_task = store.next_pending_tiering_task()
            if tier_task is not None:
                _execute_tiering_task(app, store, tier_task)
                continue
            time.sleep(POLL_INTERVAL_SEC)
        except Exception:
            log.exception("worker 循环异常")
            time.sleep(POLL_INTERVAL_SEC)
```

> 说明：回复优先由 `_execute_tiering_task` 内部每客户前检查 pending reply 保证；`worker_loop` 外层先取 reply 再取 tiering，二者共同满足 D7。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/reply/test_worker_tiering.py -v`
Expected: PASS（2 个用例全绿）

- [ ] **Step 6: 提交**

```bash
git add app/web/worker.py tests/reply/test_worker_tiering.py
git commit -m "feat: worker 串行消费 tiering_tasks, 回复优先 (customer-intent-tiering)"
```

---

### Task 5: 前端 — 等级徽章 + 筛选 + 历史时间线 + 编辑

**Files:**
- Modify: `app/web/templates/customers.html`
- Modify: `app/web/templates/chat.html`
- Modify: `app/web/templates/profile_list.html`
- Modify: `app/web/static/js/app.js`
- Modify: `app/web/static/css/app.css`
- Test: `tests/web/test_tiering_frontend.py`（新建）

**Interfaces:**
- Consumes: `tiering_levels`（Task 3 的 `/customers` 响应）、`/api/tiering/history/{customer_id}`（Task 3）、`/customers/{id}/profile`（现有 manual 保存路由）。
- Produces: 前端等级徽章、等级筛选下拉、分层历史时间线、等级/标签 manual 编辑。

- [ ] **Step 1: 写失败测试**

创建 `tests/web/test_tiering_frontend.py`：

```python
# tests/web/test_tiering_frontend.py
from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore


def test_customer_card_shows_tier_badge(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",
                       ("c1", "intent_level", "A", "auto", 0))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",
                       ("c1", "tags", "已购,议价中", "auto", 0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/customers").text
    assert 'tier-badge' in html
    assert "A" in html
    assert "已购" in html


def test_customer_page_has_tier_filter_dropdown(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",
                       ("c1", "intent_level", "A", "auto", 0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/customers").text
    assert 'id="filter-tier"' in html
    assert 'value="A"' in html


def test_chat_page_has_tier_history_section(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("c1", "Alice", "1", None, None, 0, None))
    store.add_tier_history("c1", "A", "已购", "auto")
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/customers/c1").text
    assert "分层历史" in html
    assert "A" in html
    assert "已购" in html
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/web/test_tiering_frontend.py -v`
Expected: FAIL（模板未含徽章/下拉/历史区块）

- [ ] **Step 3: 修改 customers.html**

在 `app/web/templates/customers.html` 的 filter-bar 中，`filter-company` 之后追加等级下拉：

```html
  <select id="filter-tier" class="input">
    <option value="">全部等级</option>
    <option value="A">A</option>
    <option value="B">B</option>
    <option value="C">C</option>
    <option value="D">D</option>
    <option value="untiered">未分层</option>
  </select>
```

在客户卡片 `card-body` 内（`country` 行之后）追加等级徽章与标签：

```html
        <div class="muted">{{ c['country'] or '-' }}</div>
        {% set tier = profiles_by_customer.get(c['id'], '') %}
        {% if 'intent_level=' in tier %}
          <div class="tier-row">
            <span class="tier-badge tier-{{ tier.split('intent_level=')[1].split(' ')[0] }}">{{ tier.split('intent_level=')[1].split(' ')[0] }}</span>
            {% if 'tags=' in tier %}
              {% for t in tier.split('tags=')[1].split(' ')[0].split(',') %}
                <span class="tag">{{ t }}</span>
              {% endfor %}
            {% endif %}
          </div>
        {% endif %}
```

> 说明：`profiles_by_customer` 为 `"field=value field=value"` 拼接串，`intent_level=A` 与 `tags=已购,议价中` 均在其中。徽章 class 用 `tier-A` 等控制颜色。

- [ ] **Step 4: 修改 chat.html**

在 `app/web/templates/chat.html` 的 `two-col` 之后、`关联会话` 之前追加分层历史区块：

```html
<h2>分层历史</h2>
<div id="tier-history" hx-get="/api/tiering/history/{{ customer_id }}" hx-trigger="load" hx-swap="innerHTML">
  <p class="muted">加载中…</p>
</div>
```

- [ ] **Step 5: 新增历史时间线模板**

创建 `app/web/templates/tier_history.html`：

```html
{% if history %}
<ul class="tier-timeline">
  {% for h in history %}
  <li>
    <span class="tier-badge tier-{{ h['intent_level'] }}">{{ h['intent_level'] }}</span>
    <span class="tag">{{ h['tags'] }}</span>
    <span class="muted">({{ h['source'] }}, {{ h['created_at'] }})</span>
  </li>
  {% endfor %}
</ul>
{% else %}
<p class="muted">暂无分层历史</p>
{% endif %}
```

- [ ] **Step 6: 修改 profile_list.html 支持等级/标签编辑**

在 `app/web/templates/profile_list.html` 的「新增字段」表单前追加等级/标签专用编辑表单（manual 来源）：

```html
<h3>意向等级 / 标签（人工编辑标记为 manual）</h3>
<form hx-post="/customers/{{ customer_id }}/profile" hx-target="#profile" hx-swap="innerHTML">
  <input type="hidden" name="field" value="intent_level">
  <select name="value">
    <option value="">未分层</option>
    <option value="A">A</option>
    <option value="B">B</option>
    <option value="C">C</option>
    <option value="D">D</option>
  </select>
  <button type="submit">保存等级</button>
</form>
<form hx-post="/customers/{{ customer_id }}/profile" hx-target="#profile" hx-swap="innerHTML">
  <input type="hidden" name="field" value="tags">
  <input type="text" name="value" placeholder="标签,逗号分隔" size="30">
  <button type="submit">保存标签</button>
</form>
```

> 说明：`/customers/{id}/profile` 现有路由（`routes.py:434`）以 `source="manual"` 写入，天然满足「人工值不被 auto 覆盖」。

- [ ] **Step 7: 修改 app.js 增加等级筛选**

在 `app.js` 的 `initCustomerFilter` 中，读取 `filter-tier` 并叠加过滤逻辑：

```js
function initCustomerFilter() {
  var input = document.getElementById("search-input");
  var country = document.getElementById("filter-country");
  var company = document.getElementById("filter-company");
  var tier = document.getElementById("filter-tier");
  if (!input) return;
  function apply() {
    var q = (input.value || "").trim().toLowerCase();
    var cc = country ? country.value : "";
    var cp = company ? company.value : "";
    var tt = tier ? tier.value : "";
    document.querySelectorAll(".customer-card").forEach(function (card) {
      var hay = (card.getAttribute("data-search") || "").toLowerCase();
      var tierMatch = true;
      if (tt) {
        var m = hay.match(/intent_level=([a-d])/);
        var cur = m ? m[1].toUpperCase() : "";
        if (tt === "untiered") { tierMatch = cur === ""; }
        else { tierMatch = cur === tt; }
      }
      var ok = (!q || hay.indexOf(q) >= 0)
        && (!cc || hay.indexOf("country=" + cc.toLowerCase()) >= 0)
        && (!cp || hay.indexOf("company=" + cp.toLowerCase()) >= 0)
        && tierMatch;
      card.style.display = ok ? "" : "none";
    });
  }
  input.addEventListener("input", apply);
  if (country) country.addEventListener("change", apply);
  if (company) company.addEventListener("change", apply);
  if (tier) tier.addEventListener("change", apply);
}
```

- [ ] **Step 8: 修改 app.css 增加徽章样式**

在 `app/web/static/css/app.css` 末尾追加：

```css
/* ---- customer-intent-tiering: 等级徽章 ---- */
.tier-badge {
  display: inline-block;
  min-width: 22px;
  text-align: center;
  border-radius: 6px;
  padding: 1px 8px;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  margin-right: 6px;
}
.tier-A { background: #16a34a; }
.tier-B { background: #2563eb; }
.tier-C { background: #f59e0b; }
.tier-D { background: #6b7280; }
.tier-row { margin-top: 6px; }
.tier-timeline { list-style: none; margin: 0; padding: 0; }
.tier-timeline li { padding: 6px 0; border-bottom: 1px solid var(--border); }
.tier-timeline li:last-child { border-bottom: none; }
```

- [ ] **Step 9: 运行测试确认通过**

Run: `python -m pytest tests/web/test_tiering_frontend.py -v`
Expected: PASS（3 个用例全绿）

- [ ] **Step 10: 提交**

```bash
git add app/web/templates/customers.html app/web/templates/chat.html app/web/templates/profile_list.html app/web/templates/tier_history.html app/web/static/js/app.js app/web/static/css/app.css tests/web/test_tiering_frontend.py
git commit -m "feat: 前端等级徽章/筛选/历史时间线/人工编辑 (customer-intent-tiering)"
```

---

### Task 6: 全量回归验证

**Files:**
- 无新增文件（仅验证）。

**Interfaces:**
- 无。

- [ ] **Step 1: 编译检查**

Run: `python -m compileall app tests`
Expected: 无语法错误输出

- [ ] **Step 2: 全量测试**

Run: `python -m pytest -q`
Expected: 全部通过（含既有用例，无回归）

- [ ] **Step 3: 手动验证清单**

按以下步骤在本地运行 `python -m app.web.app`（或项目启动命令）验证：

1. 打开 `/customers`，确认客户卡片显示等级徽章（A 绿/B 蓝/C 橙/D 灰）与标签。
2. 用等级下拉筛选「A」→ 仅显示 A 类客户；选「未分层」→ 显示无等级客户。
3. 在客户详情页 `/customers/{id}` 确认「分层历史」区块加载出时间线。
4. 在画像区手动编辑等级/标签（manual），再触发分层分析，确认 auto 不覆盖 manual 值。
5. 触发 `POST /api/tiering/analyze`（缺省近期活跃），轮询 `/api/tiering/status/{task_id}` 至 done，确认客户获得等级/标签且历史可查。

- [ ] **Step 4: 提交（如有修复）**

```bash
git add -A
git commit -m "fix: 分层功能回归修复 (customer-intent-tiering)"
```

---

## Self-Review

**Spec 覆盖核对：**
- 客户意向分层分析（A/B/C/D + 标签）→ Task 2 `tier_customer`。
- 分层范围筛选（近期活跃/手动指定）→ Task 1 `list_recent_active_customers` + Task 3 `POST /api/tiering/analyze`。
- 无数据客户标记未分层 → Task 2 `tier_customer` 空摘要回退。
- 异步执行 + 前端轮询 → Task 1 `tiering_tasks` + Task 3 状态端点 + Task 4 worker。
- 分层不阻塞回复（回复优先）→ Task 4 `_execute_tiering_task` 每客户检查 pending reply。
- 分层历史记录/查看/触发方式 → Task 1 `add_tier_history`/`get_tier_history` + Task 3 历史端点 + Task 5 时间线。
- 人工覆盖优先 → Task 2 复用 `upsert_profile_field` manual 保护 + Task 5 manual 编辑。
- 分层规则（A/B/C/D 判定）→ Task 2 `TIER_PROMPT`。
- 标签体系（预定义 + 自由补充）→ Task 2 `PREDEFINED_TAGS` + prompt。
- 前端等级徽章 + 筛选 → Task 5。

**占位符扫描：** 无 TBD/TODO；所有代码步骤含完整实现。

**类型一致性：** `tier_customer`/`tier_customers` 返回类型在 Task 2 定义、Task 4 消费一致；`add_tier_history(customer_id, intent_level, tags, source)` 签名在 Task 1 定义、Task 2/3/5 调用一致；`create_tiering_task`/`get_tiering_task`/`next_pending_tiering_task`/`update_tiering_task` 签名跨 Task 1/3/4 一致；`list_recent_active_customers(days)` 在 Task 1 定义、Task 3 调用一致。`tiering_levels` 在 Task 3 产生、Task 5 消费一致。
