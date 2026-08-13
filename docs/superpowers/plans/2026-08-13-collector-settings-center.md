---
change: collector-settings-center
design-doc: docs/superpowers/specs/2026-08-13-collector-settings-center-design.md
base-ref: c9cc227400afe984e3ce277508e2813ebca265dd
---
# 采集器设置中心 (collector-settings-center) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 非技术用户可通过 Web 配置采集器运行参数（同步频次 + 自动扫描参数），参数持久化到 SQLite 并即时生效（无需重启）；首页可一键触发全量扫描并实时查看进度；全站视觉改版统一为简约清爽风格。

**Architecture:** 沿用既有双进程架构，全部通过 SQLite 共享状态，无新增进程间通信。Web 进程写 `settings` 表 + `scan_requests` 表；采集器主循环每轮 `RuntimeSettings.refresh()` 读 DB 覆盖 `.env` 默认（即时生效），并新增 `_drain_scan_requests()` 轮询消费全量扫描意图（与 `_drain_backfill_requests` 同构，scanner.py:443 参照）。扫描进度经 `on_progress` 回调合并写入 `status.json` 的 `scan` 字段，Web 复用 `GET /api/collector/status` 轮询渲染。

**Tech Stack:** FastAPI / Jinja2 / HTMX / SQLite (WAL + busy_timeout) / pytest (TestClient + monkeypatch + FakeStore/FakePage 既有模式)

## Global Constraints

- 复用既有 SQLite 连接（`store.conn`，scanner 与 Web 各持进程级单例）；并发安全沿用 WAL + `busy_timeout=5000` 现有模式，不引入连接池。
- `settings` 表**只存用户显式配置过的项**；未配置项 DB 无行，回退 `.env` 默认值（设计 §3.1）。
- `RuntimeSettings.get_typed` 解析失败（脏数据）一律回退默认值——单条配置错误不得搞崩采集器（设计 §4、D2 校验双层防御）。
- 手动扫描走「意图表 + 轮询消费」，`scan_requests` 与 `backfill_requests` 职责分离（全量 vs 单会话），各自独立 drain，不合并（设计 D1、§5.4）。
- 手动扫描与自动扫描同线程串行互斥（设计 D3）：`_drain_scan_requests` 执行前设置 `last_scan=now` 跳过自动周期分支；重复触发由 Web 层以 `scan_requests` 未完成行判定返回 `409 busy`。
- 扫描异常 `attempts+1`，`attempts<3` 时下轮重试，达上限标 `failed`（设计 §8）。
- 校验规则统一：数值参数 `float>0`；`auto_scan_max_chats` 为整数 `1..1000`；`auto_scan_settle_sec` 为 `0.1..30`；`auto_scan_chats` 为布尔。非法值 → `400 {"error": "字段: 提示", "field": key}`，全通过才写库（原子，设计 §6.1）。
- 采集器离线时不拦截扫描请求（意图表排队语义，设计 §6.2）。
- 旧 `status.json` 无 `scan` 字段：Web 响应 `scan: null`，前端 `(scan || {})` 容错（设计 §8）。
- 前端改版**不引入新依赖**（htmx 已本地化），保留 htmx 与既有类名兼容，逐页改版（设计 D5）。
- 回归基线：`.venv/Scripts/python.exe -m compileall -q app` 通过 + `.venv/Scripts/python.exe -m pytest -q` 全量通过。
- base-ref: `c9cc227400afe984e3ce277508e2813ebca265dd`（当前 HEAD）。

## 与 tasks.md 的任务边界对应关系

| tasks.md 区段 | 对应 Task |
| --- | --- |
| §1.1 迁移逻辑新增 settings / scan_requests 表 | Task 1.1 |
| §1.2 RuntimeSettings 读写层 + 单测 | Task 1.2 |
| §1.3 scan_requests 存储方法 + 单测 | Task 1.3 |
| §2.1 scan_all_chats 进度回调 | Task 2.1 |
| §2.2 _drain_scan_requests + 进度/结果写 status.json | Task 2.2 |
| §2.3 run() 接入 + last_scan 互斥 + 重复拒绝 | Task 2.3 |
| §2.4 运行时参数替换 settings 常量 + 解析回退 | Task 2.4 |
| §3.1 GET/POST /api/settings + /api/settings/reset + 校验 | Task 3.1 |
| §3.2 POST /api/collector/scan + status 加 scan 字段 | Task 3.2 |
| §3.3 路由对接既有接口 + 接口测试 | Task 3.3 |
| §4.1 settings.html 设置页 | Task 4.1 |
| §4.2 首页状态控制区 + 扫描进度 | Task 4.2 |
| §4.3 app.js 设置提交/扫描触发/进度轮询 | Task 4.3 |
| §5.1 app.css 设计变量与组件收敛 | Task 5.1 |
| §5.2 全站模板统一版式 | Task 5.2 |
| §5.3 base.html 导航升级 + 离线可用验证 | Task 5.3 |
| §6.1 单元/接口测试聚合 | Task 6.1 |
| §6.2 手动验证清单 | Task 6.2 |
| §6.3 全量回归 | Task 6.3 |

## 文件结构总览

| 文件 | 变更 | 职责 |
| --- | --- | --- |
| `app/storage/schema.sql` | 修改 | 追加 `settings`、`scan_requests` 建表语句 |
| `app/storage/runtime_settings.py` | 新建 | `RuntimeSettings`（DEFAULTS / refresh / get / set / reset / all / get_typed） |
| `app/storage/sqlite_store.py` | 修改 | scan_requests 存储方法（insert / pending / running / done / attempts / busy 判定） |
| `app/collector/scanner.py` | 修改 | `scan_all_chats` 进度回调、`_drain_scan_requests`、`run()` 接入 + 互斥 + 运行时参数 |
| `app/web/routes.py` | 修改 | `GET/POST /api/settings`、`POST /api/settings/reset`、`POST /api/collector/scan`、`GET /api/collector/status` 加 scan、`GET /settings` 页路由 |
| `app/web/templates/settings.html` | 新建 | 设置中心页（分组表单 + 保存/恢复默认 + 校验错误展示） |
| `app/web/templates/base.html` | 修改 | 导航升级（图标 + 标签 + 「设置」入口） |
| `app/web/templates/home.html` | 修改 | 采集器状态控制区 + 「立即全量扫描」按钮 + 扫描进度区 |
| `app/web/templates/customers.html`、`chat.html`、`chat_messages.html`、`knowledge.html`、`knowledge_docs.html`、`search.html`、`search_results.html`、`cleanup.html`、`profile_list.html`、`reply_result.html` 等 | 修改 | 套用统一版式（页面标题区 / 卡片 / 操作区） |
| `app/web/static/css/app.css` | 修改 | 设计变量演进 + 组件收敛（nav/card/btn/form/table/tag/empty/status-pill/progress）+ 移动端 |
| `app/web/static/js/app.js` | 修改 | 设置表单读写、扫描触发 + 确认、采集器状态与扫描进度轮询渲染 |
| `tests/storage/test_runtime_settings.py` | 新建 | RuntimeSettings 单测（get/set/reset/all/类型转换/脏数据回退） |
| `tests/storage/test_sqlite_store.py` | 修改 | 迁移幂等 + scan_requests 存储方法单测 |
| `tests/collector/test_scanner.py` | 修改 | 进度回调 / _drain_scan_requests / 互斥 / 运行时参数回退 |
| `tests/web/test_settings.py` | 新建 | /api/settings 三端点（成功/非法/未知 key）+ /settings 页 |
| `tests/web/test_scan_api.py` | 新建 | /api/collector/scan（accepted / 409 busy）+ status scan 字段 |

---

### Task 1: 存储层 — settings 表与 scan_requests 表

**Files:**
- Modify: `app/storage/schema.sql`
- Create: `app/storage/runtime_settings.py`
- Modify: `app/storage/sqlite_store.py`
- Create: `tests/storage/test_runtime_settings.py`
- Modify: `tests/storage/test_sqlite_store.py`

**设计依据:** `settings` 表 key 用 config 字段名下划线形式；仅存显式配置项；`scan_requests` 表一次一条请求、status 由采集器更新、不存 chat_id（全量无参数）（设计 §3）。`RuntimeSettings` 复用 `SqliteStore` 连接，`get_typed` 解析失败回退默认值（设计 §4）。

#### 1.1 schema.sql 新增两表 + 迁移幂等测试

- [x] **Step 1: 写失败测试**（追加到 `tests/storage/test_sqlite_store.py` 末尾）

```python
def test_old_schema_gets_settings_and_scan_requests_tables(tmp_data):
    """旧库打开后自动建出 settings / scan_requests 表 (schema.sql IF NOT EXISTS 幂等)。"""
    store = SqliteStore()
    for t in ("settings", "scan_requests"):
        assert store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
    store.conn.close()
    p = tmp_data / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE chats(id TEXT, account_id TEXT, PRIMARY KEY(id, account_id))")
    c.commit(); c.close()
    for _ in range(2):  # 同一旧库重复打开 → 迁移幂等
        s2 = SqliteStore(p)
        for t in ("settings", "scan_requests"):
            assert s2.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
        s2.conn.close()
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_sqlite_store.py -q`
Expected: `test_old_schema_gets_settings_and_scan_requests_tables` FAIL（两表不存在）。

- [x] **Step 3: 最小实现**

`app/storage/schema.sql` 末尾（`backfill_requests` 建表之后、FTS5 之前或文件末尾均可）追加：

```sql
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
```

`SqliteStore._init_schema()` 已 `executescript(schema.sql)`，`IF NOT EXISTS` 使新旧库均幂等，无需额外 ALTER（`schema.sql:20`）。

- [x] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_sqlite_store.py -q`
Expected: 既有用例 + 新增 1 个全部 PASS。

- [x] **Step 5: 提交**

```bash
git add app/storage/schema.sql tests/storage/test_sqlite_store.py
git commit -m "feat: schema 新增 settings / scan_requests 表 (IF NOT EXISTS 幂等迁移)"
```

#### 1.2 RuntimeSettings 读写层 + 单测

- [x] **Step 1: 写失败测试 `tests/storage/test_runtime_settings.py`**

```python
import time
from app.storage.sqlite_store import SqliteStore
from app.storage.runtime_settings import RuntimeSettings
from app.config import settings


def test_get_returns_default_when_unset(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    assert rt.get("fast_tick_sec") == settings.fast_tick_sec
    assert rt.get("auto_scan_chats") == settings.auto_scan_chats


def test_set_get_roundtrip_stores_string(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    rt.set("fast_tick_sec", "3.5")
    assert rt.get("fast_tick_sec") == "3.5"   # DB 存字符串
    assert rt.all() == {"fast_tick_sec": "3.5"}  # 只含显式配置项


def test_reset_deletes_row_restores_default(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    rt.set("slow_tick_sec", "99")
    rt.reset("slow_tick_sec")
    assert rt.get("slow_tick_sec") == settings.slow_tick_sec
    assert "slow_tick_sec" not in rt.all()


def test_refresh_reloads_db_values(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    rt.set("fast_tick_sec", "4.0")
    rt.refresh()  # 模拟主循环新一轮
    assert rt.get_typed("fast_tick_sec", settings.fast_tick_sec) == 4.0


def test_get_typed_converts_by_default_type(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    rt.set("auto_scan_max_chats", "250")
    assert rt.get_typed("auto_scan_max_chats", settings.auto_scan_max_chats) == 250
    rt.set("auto_scan_chats", "false")
    assert rt.get_typed("auto_scan_chats", settings.auto_scan_chats) is False


def test_get_typed_dirty_value_falls_back_to_default(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    rt.set("fast_tick_sec", "abc")   # 脏数据
    assert rt.get_typed("fast_tick_sec", settings.fast_tick_sec) == settings.fast_tick_sec
    rt.set("auto_scan_max_chats", "oops")
    assert rt.get_typed("auto_scan_max_chats", settings.auto_scan_max_chats) == settings.auto_scan_max_chats


def test_get_typed_unset_returns_default(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    assert rt.get_typed("slow_tick_sec", settings.slow_tick_sec) == settings.slow_tick_sec
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_runtime_settings.py -v`
Expected: 全部 FAIL（`ModuleNotFoundError: No module named 'app.storage.runtime_settings'`）。

- [x] **Step 3: 最小实现**

新建 `app/storage/runtime_settings.py`：

```python
# app/storage/runtime_settings.py
import time
from app.config import settings


class RuntimeSettings:
    """settings 表 (key-value) 读写层。复用调用方 SqliteStore 连接。
    DEFAULTS 以 .env 默认值为准；DB 只存用户显式配置项，未配置项 get 回退默认。"""

    DEFAULTS = {
        "fast_tick_sec": settings.fast_tick_sec,
        "slow_tick_sec": settings.slow_tick_sec,
        "auto_scan_interval_sec": settings.auto_scan_interval_sec,
        "auto_scan_max_chats": settings.auto_scan_max_chats,
        "auto_scan_settle_sec": settings.auto_scan_settle_sec,
        "auto_scan_chats": settings.auto_scan_chats,
    }

    def __init__(self, store):
        self.store = store
        self._cache = {}  # refresh() 后生效

    def refresh(self):
        """主循环每轮调用: 一次 SELECT 拉全量 DB 值入缓存。"""
        rows = self.store.conn.execute("SELECT key, value FROM settings").fetchall()
        self._cache = {r["key"]: r["value"] for r in rows}

    def get(self, key, default=None):
        """DB 值 (字符串)；无行返回 default；default 为 None 时用 DEFAULTS。"""
        if key in self._cache:
            return self._cache[key]
        row = self.store.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row:
            return row["value"]
        return default if default is not None else self.DEFAULTS.get(key)

    def set(self, key, value):
        """UPSERT；value 一律存字符串。"""
        self.store.conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, str(value), int(time.time())))
        self.store.conn.commit()
        self._cache[key] = str(value)

    def reset(self, key):
        """删除该行，恢复 .env 默认。"""
        self.store.conn.execute("DELETE FROM settings WHERE key=?", (key,))
        self.store.conn.commit()
        self._cache.pop(key, None)

    def all(self):
        """DB 全部键值（不含默认，供合并展示）。"""
        rows = self.store.conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def get_typed(self, key, default):
        """按 DEFAULTS 类型转换；解析失败回退 default (单条脏数据不搞崩采集器)。"""
        raw = self.get(key, None)
        if raw is None:
            return default
        default_val = self.DEFAULTS.get(key, default)
        try:
            if isinstance(default_val, bool):
                s = str(raw).strip().lower()
                if s in ("1", "true", "yes", "on"):
                    return True
                if s in ("0", "false", "no", "off"):
                    return False
                raise ValueError(raw)
            if isinstance(default_val, int):
                return int(raw)
            return float(raw)
        except (TypeError, ValueError):
            return default
```

- [x] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_runtime_settings.py -v`
Expected: 7 passed。

- [x] **Step 5: 提交**

```bash
git add app/storage/runtime_settings.py tests/storage/test_runtime_settings.py
git commit -m "feat: RuntimeSettings 读写层 (DB 覆盖 .env, get_typed 解析失败回退默认)"
```

#### 1.3 scan_requests 存储方法 + 单测

- [x] **Step 1: 写失败测试**（追加到 `tests/storage/test_sqlite_store.py` 末尾）

```python
# ---- collector-settings-center: 全量扫描请求 (tasks 1.3) ----
def test_scan_requests_insert_pending_done_attempts(tmp_data):
    s = SqliteStore()
    r1 = s.create_scan_request()
    assert r1 is not None
    row = s.next_pending_scan_request()
    assert row is not None and row["id"] == r1 and row["status"] == "pending"
    assert s.has_active_scan_request() is True
    s.mark_scan_request_running(r1)
    assert s.has_active_scan_request() is True  # running 仍算 active
    s.mark_scan_request_done(r1)
    assert s.next_pending_scan_request() is None
    assert s.has_active_scan_request() is False
    # 失败重试: attempts+1, <3 时仍可被取到
    r2 = s.create_scan_request()
    s.bump_scan_request_attempts(r2)
    row = s.next_pending_scan_request()
    assert row["id"] == r2 and row["attempts"] == 1
    s.bump_scan_request_attempts(r2); s.bump_scan_request_attempts(r2)
    assert s.next_pending_scan_request() is None  # attempts=3 达到上限不再取
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_sqlite_store.py -q`
Expected: 新增用例 FAIL（`AttributeError: 'SqliteStore' object has no attribute 'create_scan_request'`）。

- [x] **Step 3: 最小实现**

`app/storage/sqlite_store.py` 末尾追加（backfill 相关方法之后，参照 `_drain_backfill_requests` 意图表语义）：

```python
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
        """是否存在 pending/running 未完成请求 (Web 层 busy 判定)。"""
        r = self.conn.execute(
            "SELECT id FROM scan_requests WHERE done=0 AND attempts<3 "
            "AND status IN ('pending','running') ORDER BY id LIMIT 1").fetchone()
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
```

- [x] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_sqlite_store.py -q`
Expected: 既有用例 + 新增 1 个全部 PASS。

- [x] **Step 5: 提交**

```bash
git add app/storage/sqlite_store.py tests/storage/test_sqlite_store.py
git commit -m "feat: scan_requests 存储方法 (插入/pending/running/done/attempts/busy 判定)"
```

---

### Task 2: 采集器 — 手动扫描消费与运行时配置

**Files:**
- Modify: `app/collector/scanner.py`
- Modify: `tests/collector/test_scanner.py`

**设计依据:** `_drain_scan_requests` 与 `_drain_backfill_requests` 并列（scanner.py:443 参照），主循环串行执行天然互斥；执行前设置 `last_scan=now` 使自动周期分支条件为假（scanner.py:359），双保险再加 `not self._manual_scan_active` 判断（设计 §5.4）。进度经 `on_progress(current, total, ingested)` 写 `status.json` 的 `scan` 对象（设计 §5.2、D4）。

#### 2.1 scan_all_chats 支持进度回调

- [x] **Step 1: 写失败测试**（追加到 `tests/collector/test_scanner.py` 末尾，复用 `FakePage`/`_FakeLocator` 既有类）

```python
async def test_scan_all_chats_reports_progress(tmp_data, monkeypatch):
    """2.1: scan_all_chats 每处理一个会话回调 on_progress(current, total, ingested)。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    counter = [0]
    def fake_parse(s, chat_id=None):
        counter[0] += 1
        return [{"id": f"HEX{i}", "fromMe": False, "from": None,
                 "timestamp": 0, "body": f"hello{i}", "body_present": True} for i in range(counter[0])]
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", fake_parse)
    async def fake_walk_idb(cdp, acct):
        return {"chats": {}, "contacts": {}, "messages": []}
    monkeypatch.setattr("app.collector.idb_walk.walk_idb", fake_walk_idb)
    class FakeCdp:
        async def capture_snapshot(self): return {}
    page = FakePage(n_rows=3)
    sc = Scanner(FakeCdp(), store, FakeVector(), page=page)
    progress = []
    await sc.scan_all_chats(max_chats=3, settle=0,
                            on_progress=lambda c, t, i: progress.append((c, t, i)))
    assert progress == [(1, 3, 1), (2, 3, 3), (3, 3, 6)]  # current/total/累计 ingested
    assert progress[-1][0] == progress[-1][1] == 3
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/collector/test_scanner.py -q`
Expected: 新增用例 FAIL（`TypeError`，`on_progress` 参数未定义）。

- [x] **Step 3: 最小实现**

`app/collector/scanner.py` 的 `scan_all_chats`（scanner.py:303）改造：

```python
    async def scan_all_chats(self, max_chats: int | None = None, settle: float | None = None,
                             on_progress=None) -> int:
        """自动扫描全部会话: 逐个打开会话读取可见正文入库 (供首次知识构建/周期校准)。
        依赖 Playwright page 原生可信 click; 注意会把未读消息标记为已读。
        on_progress(current, total, ingested): 每处理一个会话回调一次 (D4)。"""
        if self.page is None:
            return 0
        max_chats = max_chats or settings.auto_scan_max_chats
        settle = settle or settings.auto_scan_settle_sec
        from app.collector.idb_walk import walk_idb
        data = await walk_idb(self.cdp, self.account_id)
        self._persist_contacts(data)
        try:
            total = await self.page.eval_on_selector_all(
                "[data-testid='chat-list'] div[role='row']", "els => els.length")
        except Exception:
            return 0
        if on_progress:
            on_progress(0, min(total, max_chats), 0)  # 扫描前先报一次 total 已知
        ingested = 0
        row_sel = "[data-testid='chat-list'] div[role='row']"
        for i in range(min(total, max_chats)):
            if i % 5 == 0:
                write_status(settings.status_path, {"state": "running"})  # 长扫描期间保持心跳
            try:
                await self.page.locator(row_sel).nth(i).click(timeout=8000)
            except Exception:
                if on_progress:
                    on_progress(i + 1, min(total, max_chats), ingested)  # 跳过也推进进度
                continue
            await asyncio.sleep(settle)
            dom_msgs = parse_dom_snapshot_safe(await self.cdp.capture_snapshot(), self._current_chat_id)
            merged = self._merge_idb_dom(data, dom_msgs)
            for m in merged:
                if self._upsert_one(m):
                    ingested += 1
            if merged:
                await self._capture_avatar(self._current_chat_id)
            if on_progress:
                on_progress(i + 1, min(total, max_chats), ingested)
        if ingested:
            write_status(settings.status_path, {"state": "running", "last_sync": time.time()})
        return ingested
```

- [x] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/collector/test_scanner.py -q`
Expected: 既有用例（含 `test_scan_all_chats_opens_each_chat`、`test_scan_all_chats_skips_avatar_when_no_messages`）+ 新增全部 PASS（无回调参数时行为不变，既有测试不受影响）。

- [x] **Step 5: 提交**

```bash
git add app/collector/scanner.py tests/collector/test_scanner.py
git commit -m "feat: scan_all_chats 支持 on_progress 进度回调 (每会话一次)"
```

#### 2.2 _drain_scan_requests 消费 + 进度/结果写 status.json

- [x] **Step 1: 写失败测试**（追加到 `tests/collector/test_scanner.py` 末尾，用真实 `SqliteStore` + 假 page/cdp）

```python
class ScanPage:
    def __init__(self, n_rows=2):
        self.n_rows = n_rows; self.clicks = []
    async def eval_on_selector_all(self, sel, expr): return self.n_rows
    def locator(self, sel): return _FakeLocator(self)

async def test_drain_scan_requests_consumes_and_writes_status(tmp_data, monkeypatch):
    """2.2: 消费 pending 请求 → 执行扫描 → 进度/结果写 status.json → 标 done。"""
    from app.storage.sqlite_store import SqliteStore
    import app.collector.scanner as sc_mod
    store = SqliteStore()
    req_id = store.create_scan_request()
    def fake_parse(s, chat_id=None):
        return [{"id": "HEX1", "fromMe": False, "from": None, "timestamp": 0,
                 "body": "hello", "body_present": True}]
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", fake_parse)
    async def fake_walk_idb(cdp, acct):
        return {"chats": {}, "contacts": {}, "messages": []}
    monkeypatch.setattr("app.collector.idb_walk.walk_idb", fake_walk_idb)
    class FakeCdp:
        async def capture_snapshot(self): return {}
    sc = Scanner(FakeCdp(), store, FakeVector(), page=ScanPage())
    await sc._drain_scan_requests()
    row = store.conn.execute("SELECT * FROM scan_requests WHERE id=?", (req_id,)).fetchone()
    assert row["done"] == 1 and row["status"] == "done"
    s = read_status(settings.status_path)
    assert s["scan"]["running"] is False and s["scan"]["done"] is True
    assert s["scan"]["ingested"] >= 0 and "finished_at" in s["scan"]
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] >= 1


async def test_drain_scan_requests_sets_last_scan_skips_auto(tmp_data, monkeypatch):
    """2.3: 消费期间设置 last_scan=now → 自动周期扫描分支本轮跳过。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.create_scan_request()
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s, chat_id=None: [])
    async def fake_walk_idb(cdp, acct): return {"chats": {}, "contacts": {}, "messages": []}
    monkeypatch.setattr("app.collector.idb_walk.walk_idb", fake_walk_idb)
    class FakeCdp:
        async def capture_snapshot(self): return {}
    sc = Scanner(FakeCdp(), store, FakeVector(), page=ScanPage())
    await sc._drain_scan_requests()
    assert time.time() - sc.last_scan < 5  # 已刷新


async def test_drain_scan_requests_failure_bumps_attempts(tmp_data, monkeypatch):
    """2.2: 扫描中途异常 → attempts+1 不标 done, <3 下轮可重试。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    req_id = store.create_scan_request()
    class BoomPage:
        async def eval_on_selector_all(self, sel, expr): raise RuntimeError("CDP 挂了")
        def locator(self, sel): raise RuntimeError("CDP 挂了")
    sc = Scanner(None, store, FakeVector(), page=BoomPage())
    await sc._drain_scan_requests()   # 不应抛异常
    row = store.conn.execute("SELECT * FROM scan_requests WHERE id=?", (req_id,)).fetchone()
    assert row["done"] == 0 and row["attempts"] == 1 and row["status"] == "failed"
    assert sc._manual_scan_active is False  # finally 已复位
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/collector/test_scanner.py -q`
Expected: 新增用例 FAIL（`AttributeError: 'Scanner' object has no attribute '_drain_scan_requests'`）。

- [x] **Step 3: 最小实现**

`app/collector/scanner.py` 在 `_drain_backfill_requests`（scanner.py:443）之后追加，并新增 `__init__` 标志：

`__init__` 内追加（scanner.py:43 附近）：
```python
        self._manual_scan_active = False  # 手动全量扫描进行中 (防御: Web 层 busy 判定不依赖此标志)
```

新方法：
```python
    async def _drain_scan_requests(self):
        """处理 Web 提交的全量扫描请求 (与 backfill 同构, D1)。
        串行在主循环执行 scan_all_chats (天然与自动扫描互斥);
        执行前设 last_scan=now 跳过自动周期分支; 失败 attempts+1 (<3 下轮重试)。"""
        req = self.store.next_pending_scan_request()
        if not req:
            return
        self._manual_scan_active = True
        self.last_scan = time.time()
        self.store.mark_scan_request_running(req["id"])
        try:
            total = 0
            if self.page is not None:
                try:
                    total = await self.page.eval_on_selector_all(
                        "[data-testid='chat-list'] div[role='row']", "els => els.length")
                except Exception:
                    total = 0
            def on_progress(current, _total, ingested):
                write_status(settings.status_path, {"state": "running",
                    "scan": {"running": True, "current": current, "total": _total,
                             "ingested": ingested}})
            max_chats = self._rt.get_typed("auto_scan_max_chats", settings.auto_scan_max_chats)
            settle = self._rt.get_typed("auto_scan_settle_sec", settings.auto_scan_settle_sec)
            ingested = await self.scan_all_chats(max_chats=max_chats, settle=settle,
                                                 on_progress=on_progress)
            write_status(settings.status_path, {"state": "running", "last_sync": time.time(),
                "scan": {"running": False, "done": True, "ingested": ingested,
                         "finished_at": time.time(), "total": total}})
            self.store.mark_scan_request_done(req["id"])
        except Exception:
            self.store.bump_scan_request_attempts(req["id"])
        finally:
            self._manual_scan_active = False
```

- [x] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/collector/test_scanner.py -q`
Expected: 新增 3 个用例 + 既有全部 PASS。

- [x] **Step 5: 提交**

```bash
git add app/collector/scanner.py tests/collector/test_scanner.py
git commit -m "feat: Scanner._drain_scan_requests 全量扫描消费 (进度写 status.json, 异常 attempts+1)"
```

#### 2.3 run() 接入 _drain_scan_requests + 自动扫描互斥

- [x] **Step 1: 最小实现**

`app/collector/scanner.py` 的 `run()`（scanner.py:340-377）改造：

1. 自动扫描分支（scanner.py:359）追加双保险：
```python
                if (not self._manual_scan_active
                        and self._rt.get_typed("auto_scan_chats", settings.auto_scan_chats)
                        and self.page is not None
                        and time.time() - self.last_scan >= self._rt.get_typed(
                            "auto_scan_interval_sec", settings.auto_scan_interval_sec)):
                    try:
                        await self.scan_all_chats(
                            max_chats=self._rt.get_typed("auto_scan_max_chats", settings.auto_scan_max_chats),
                            settle=self._rt.get_typed("auto_scan_settle_sec", settings.auto_scan_settle_sec))
                    except Exception:
                        pass  # 扫描失败不阻塞主循环
                    self.last_scan = time.time()
```

2. drain 接入（`_drain_backfill_requests` 调用之后）：
```python
                await self._drain_scan_requests()
                await self._drain_backfill_requests()
```

> 注意：`_drain_scan_requests` 应排在 `_drain_backfill_requests` 之前（扫描优先语义，两者本身互斥不可同轮并行开会话；主循环串行已保证）。

- [x] **Step 2: 验证**

Run: `.venv/Scripts/python.exe -m pytest tests/collector/test_scanner.py -q`
Expected: 全部 PASS（`run()` 不直接跑全循环，既有单轮测试不受影响）。

Run: `.venv/Scripts/python.exe -m compileall -q app`
Expected: 无输出（退出码 0）。

- [x] **Step 3: 提交**

```bash
git add app/collector/scanner.py
git commit -m "feat: run() 接入 _drain_scan_requests, 自动扫描加 _manual_scan_active 双保险"
```

#### 2.4 运行时参数读取替换 settings 常量 + 解析回退

- [x] **Step 1: 写失败测试**（追加到 `tests/collector/test_scanner.py`）

```python
async def test_run_uses_runtime_settings_fast_tick(tmp_data, monkeypatch):
    """2.4: run() 每轮经 RuntimeSettings 读取 fast_tick, DB 值覆盖 .env。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO settings(key,value,updated_at) VALUES('fast_tick_sec','0.001',0)")
    store.conn.commit()
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    assert sc._rt.get_typed("fast_tick_sec", settings.fast_tick_sec) == 0.001


async def test_runtime_settings_parse_failure_falls_back(tmp_data):
    """2.4: 脏配置 (非数值) → get_typed 回退 .env 默认, 采集器不崩。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO settings(key,value,updated_at) VALUES('slow_tick_sec','NaN',0)")
    store.conn.commit()
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    assert sc._rt.get_typed("slow_tick_sec", settings.slow_tick_sec) == settings.slow_tick_sec
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/collector/test_scanner.py -q`
Expected: FAIL（`'Scanner' object has no attribute '_rt'`）。

- [x] **Step 3: 最小实现**

`app/collector/scanner.py`：

1. `__init__`（scanner.py:29 附近）追加：
```python
        from app.storage.runtime_settings import RuntimeSettings
        self._rt = RuntimeSettings(store)
        self._rt.refresh()
```

2. `run()` 主循环每轮开头（`while True:` 之后）刷新：
```python
        while True:
            self._rt.refresh()  # 每轮刷新 (即时生效, 设计 §5.1)
```

3. 替换点（scanner.py:347、377）：
```python
                if time.time() - last_slow >= self._rt.get_typed("slow_tick_sec", settings.slow_tick_sec):
```
```python
            await asyncio.sleep(self._rt.get_typed("fast_tick_sec", settings.fast_tick_sec)
                                + random.uniform(0, settings.fast_tick_jitter))
```

> `slow_tick_jitter` 不在 settings 表范围内（设计 §3.1 六项清单），保持读 settings 常量即可。

- [x] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/collector/test_scanner.py -q`
Expected: 新增 2 个 + 既有全部 PASS。

- [x] **Step 5: 提交**

```bash
git add app/collector/scanner.py tests/collector/test_scanner.py
git commit -m "feat: Scanner 运行时参数读取 (RuntimeSettings 覆盖 .env, 解析失败回退默认)"
```

---

### Task 3: Web API — 设置读写与手动扫描触发

**Files:**
- Modify: `app/web/routes.py`
- Create: `tests/web/test_settings.py`、`tests/web/test_scan_api.py`

**设计依据:** Web 层经 `_store(request)` 获取进程级 store，构造 `RuntimeSettings(store)` 读写（设计 §4）；校验「全通过才写库」原子语义（设计 §6.1）；`POST /api/collector/scan` 以 `has_active_scan_request()` 判定 busy，采集器离线不拦截（设计 §6.2）；`GET /api/collector/status` 直接透传 status.json 的 `scan` 对象，缺失为 `null`（设计 §6.3）。

#### 3.1 settings 三端点（GET/POST/reset）+ 校验

- [x] **Step 1: 写失败测试 `tests/web/test_settings.py`**

```python
from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore
from app.storage.runtime_settings import RuntimeSettings


def test_settings_get_returns_effective_and_defaults(tmp_data):
    store = SqliteStore()
    RuntimeSettings(store).set("fast_tick_sec", "3.5")
    client = TestClient(create_app())
    r = client.get("/api/settings")
    assert r.status_code == 200
    j = r.json()
    assert j["values"]["fast_tick_sec"] == "3.5"   # DB 值生效
    assert j["defaults"]["fast_tick_sec"] == 2.0   # .env 默认
    assert set(j["values"]) == set(j["defaults"])  # 六项齐全


def test_settings_post_saves_and_returns_new_values(tmp_data):
    client = TestClient(create_app())
    r = client.post("/api/settings", json={"values": {"fast_tick_sec": "4.0",
                                                       "auto_scan_chats": "false"}})
    assert r.status_code == 200
    j = r.json()
    assert j["values"]["fast_tick_sec"] == "4.0"
    assert j["values"]["auto_scan_chats"] is False
    store = SqliteStore()
    assert RuntimeSettings(store).get("fast_tick_sec") == "4.0"


def test_settings_post_rejects_invalid_and_keeps_original(tmp_data):
    store = SqliteStore()
    RuntimeSettings(store).set("fast_tick_sec", "3.0")
    client = TestClient(create_app())
    r = client.post("/api/settings", json={"values": {"fast_tick_sec": "-1"}})
    assert r.status_code == 400
    j = r.json()
    assert "field" in j and j["field"] == "fast_tick_sec"
    assert RuntimeSettings(store).get("fast_tick_sec") == "3.0"  # 原值未变 (原子)


def test_settings_post_rejects_unknown_key(tmp_data):
    client = TestClient(create_app())
    r = client.post("/api/settings", json={"values": {"nope_key": "1"}})
    assert r.status_code == 400
    assert r.json()["field"] == "nope_key"


def test_settings_reset_restores_default(tmp_data):
    store = SqliteStore()
    RuntimeSettings(store).set("slow_tick_sec", "99")
    client = TestClient(create_app())
    r = client.post("/api/settings/reset", json={"key": "slow_tick_sec"})
    assert r.status_code == 200
    assert r.json()["defaults"]["slow_tick_sec"] == 30.0
    assert "slow_tick_sec" not in RuntimeSettings(store).all()


def test_settings_boundary_validation(tmp_data):
    client = TestClient(create_app())
    cases = [
        {"auto_scan_max_chats": "0"},      # <1
        {"auto_scan_max_chats": "1001"},   # >1000
        {"auto_scan_max_chats": "1.5"},    # 非整数
        {"auto_scan_settle_sec": "0.05"},  # <0.1
        {"auto_scan_settle_sec": "31"},    # >30
        {"auto_scan_chats": "yes"},        # 非布尔
        {"fast_tick_sec": "abc"},          # 非数值
    ]
    for v in cases:
        r = client.post("/api/settings", json={"values": v})
        assert r.status_code == 400, v
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_settings.py -v`
Expected: 全部 FAIL（`404: Not Found`，端点未实现）。

- [x] **Step 3: 最小实现**

`app/web/routes.py` 追加（在 `collector_status` 之后、`/api/stats` 之前；`RuntimeSettings` 导入）：

导入（routes.py 顶部）：
```python
from app.storage.runtime_settings import RuntimeSettings
```

端点与校验（参照现有 `_cleanup_params` 的 JSON/form 兼容 + `JSONResponse` 400 风格）：

```python
SETTING_VALIDATORS = {
    "fast_tick_sec":        {"kind": "float", "min": 1e-9},
    "slow_tick_sec":        {"kind": "float", "min": 1e-9},
    "auto_scan_interval_sec": {"kind": "float", "min": 1e-9},
    "auto_scan_max_chats":  {"kind": "int", "min": 1, "max": 1000},
    "auto_scan_settle_sec": {"kind": "float", "min": 0.1, "max": 30},
    "auto_scan_chats":      {"kind": "bool"},
}


def _validate_setting(key, raw) -> tuple:
    """返回 (ok, 规范化值 or 错误提示)。"""
    spec = SETTING_VALIDATORS.get(key)
    if spec is None:
        return False, "未知参数"
    if spec["kind"] == "bool":
        s = str(raw).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True, "true"
        if s in ("false", "0", "no", "off"):
            return True, "false"
        return False, "必须是布尔值 (true/false)"
    try:
        val = float(raw) if spec["kind"] == "float" else int(raw)
    except (TypeError, ValueError):
        return False, "必须为数值"
    if spec["kind"] == "int" and float(raw) != val:
        return False, "必须为整数"
    if val <= spec.get("min", 1e-9) or val > spec.get("max", float("inf")):
        rng = f"须在 {spec.get('min')}~{spec.get('max')}" if "max" in spec else "须大于 0"
        return False, rng
    return True, str(val)


def _rt(request: Request) -> RuntimeSettings:
    return RuntimeSettings(_store(request))


@router.get("/api/settings")
async def settings_get(request: Request):
    rt = _rt(request)
    db = rt.all()
    values = {}
    for key, default in RuntimeSettings.DEFAULTS.items():
        values[key] = db.get(key, default)
    return {"values": values, "defaults": dict(RuntimeSettings.DEFAULTS)}


@router.post("/api/settings")
async def settings_post(request: Request):
    body = await request.json()
    payload = (body or {}).get("values") or {}
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body.values 必须为对象", "field": None}, status_code=400)
    rt = _rt(request)
    validated = {}
    for key, raw in payload.items():
        ok, msg_or_val = _validate_setting(key, raw)
        if not ok:
            return JSONResponse({"error": f"{key}: {msg_or_val}", "field": key}, status_code=400)
        validated[key] = msg_or_val
    # 全通过才写库 (原子)
    for key, value in validated.items():
        rt.set(key, value)
    db = rt.all()
    return {"values": {k: db.get(k, d) for k, d in RuntimeSettings.DEFAULTS.items()}}


@router.post("/api/settings/reset")
async def settings_reset(request: Request):
    body = await request.json()
    key = (body or {}).get("key")
    if key not in RuntimeSettings.DEFAULTS:
        return JSONResponse({"error": "未知参数", "field": key}, status_code=400)
    rt = _rt(request)
    rt.reset(key)
    return {"defaults": {key: RuntimeSettings.DEFAULTS[key]}}
```

- [x] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_settings.py -v`
Expected: 7 passed。

- [x] **Step 5: 提交**

```bash
git add app/web/routes.py tests/web/test_settings.py
git commit -m "feat: /api/settings 三端点 (GET 生效值+默认 / POST 校验原子保存 / reset 恢复默认)"
```

#### 3.2 POST /api/collector/scan + status 加 scan 字段

- [x] **Step 1: 写失败测试 `tests/web/test_scan_api.py`**

```python
from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore


def test_scan_accepted_then_busy(tmp_data):
    client = TestClient(create_app())
    r1 = client.post("/api/collector/scan")
    assert r1.status_code == 200
    assert r1.json()["accepted"] is True
    r2 = client.post("/api/collector/scan")   # pending 未消费 → busy
    assert r2.status_code == 409
    j = r2.json()
    assert j.get("busy") is True and "已有扫描" in j["error"]


def test_scan_inserts_row_for_collector(tmp_data):
    client = TestClient(create_app())
    client.post("/api/collector/scan")
    store = SqliteStore()
    rows = store.conn.execute("SELECT * FROM scan_requests WHERE done=0").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "pending"


def test_status_returns_scan_null_when_missing(tmp_data):
    client = TestClient(create_app())
    r = client.get("/api/collector/status")
    assert r.status_code == 200
    j = r.json()
    assert "scan" in j and j["scan"] is None
    assert "status" in j and "alive" in j


def test_status_passthrough_scan_when_present(tmp_data):
    from app.config import settings
    import json
    settings.status_path.write_text(json.dumps(
        {"state": "running", "scan": {"running": True, "current": 5, "total": 40, "ingested": 120}}),
        encoding="utf-8")
    client = TestClient(create_app())
    j = client.get("/api/collector/status").json()
    assert j["scan"] == {"running": True, "current": 5, "total": 40, "ingested": 120}
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_scan_api.py -v`
Expected: 全部 FAIL（404 或 status 无 scan 字段）。

- [x] **Step 3: 最小实现**

`app/web/routes.py`：

1. `collector_status` 扩展（routes.py:118）：
```python
@router.get("/api/collector/status")
async def collector_status():
    s = read_status(settings.status_path)
    return {"status": s, "alive": is_alive(settings.status_path),
            "scan": (s or {}).get("scan") or None}
```

2. `POST /api/collector/scan`（追加在 `collector_backfill` 之后，参照其意图表语义）：
```python
@router.post("/api/collector/scan")
async def collector_scan(request: Request):
    """手动触发全量扫描 (意图表排队, 采集器轮询消费)。
    已有 pending/running 未完成请求 → 409 busy; 采集器离线不拦截。"""
    store = _store(request)
    if store.has_active_scan_request():
        return JSONResponse({"busy": True, "error": "已有扫描进行中"}, status_code=409)
    store.create_scan_request()
    return {"accepted": True}
```

- [x] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_scan_api.py -v`
Expected: 4 passed。

同时确认既有 banner/status 依赖无回归（`scan: None` 向后兼容，app.js 只读 `alive`）：

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_banner.py tests/web/test_routes.py::test_stats_endpoint -q`
Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add app/web/routes.py tests/web/test_scan_api.py
git commit -m "feat: POST /api/collector/scan (意图表排队, 409 busy) + status 透传 scan 字段"
```

#### 3.3 路由对接既有接口 + GET /settings 页路由

- [x] **Step 1: 最小实现**

`app/web/routes.py` 追加页路由（`cleanup_page` 之后，与既有页面路由同风格）：
```python
@router.get("/settings")
async def settings_page(request: Request):
    """采集器设置中心页 (htmx/JS 驱动 /api/settings)。"""
    return request.app.state.templates.TemplateResponse(request, "settings.html", {})
```

- [x] **Step 2: 验证（API 层对接既有 collector 状态接口无回归）**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_scan_api.py tests/web/test_settings.py -q`
Expected: 全部 PASS。

- [x] **Step 3: 提交**

```bash
git add app/web/routes.py
git commit -m "feat: GET /settings 页路由 (待 4.1 模板落地)"
```

---

### Task 4: 前端 — 设置中心页面与首页控制区

**Files:**
- Create: `app/web/templates/settings.html`
- Modify: `app/web/templates/home.html`
- Modify: `app/web/static/js/app.js`
- Create: `tests/web/test_settings.py`（追加页渲染用例）、`tests/web/test_scan_api.py`（追加首页扫描区用例）

**设计依据（含 2026-08-13 前端决策）:** 设置页分组（同步频次 / 自动扫描）展示「当前生效值 + 恢复默认」，保存成功提示并刷新生效值、400 展示字段级错误（设计 §7.3）；首页状态卡 +「立即全量扫描」+ **自定义模态框确认**（风险说明：将逐个打开会话并把未读标记为已读）+ busy/accepted 处理 + 进度区（**文本「已扫 current/total 会话 · 新入库 ingested 条」+ 细进度条**，`(scan || {})` 容错）（设计 §7.2、§8）。

**轮询合并（用户决策）:** 首页采集器状态区**不新增独立轮询**。横幅 + 状态区 + 扫描进度统一由 `app.js` 中**单一 fetch 循环**驱动渲染（在线 15s / 离线 5s 自适应节奏，沿用 app.js:69-88 横幅轮询机制扩展）。消除同页双轮询 `/api/collector/status`。

> 注意：现 home.html:13 用 `hx-get="/api/collector/status"` + `hx-swap="innerHTML"` 会把 JSON 对象序列化进 DOM，无法渲染进度。改为 app.js 的 fetch 轮询渲染（统一到横幅轮询，设计 §6.3 已说明「前端 JS 直接渲染进度」）。

#### 4.1 settings.html 设置页

- [x] **Step 1: 写失败测试**（追加到 `tests/web/test_settings.py`）

```python
def test_settings_page_renders(tmp_data):
    html = TestClient(create_app()).get("/settings").text
    assert 'hx-post="/api/settings"' not in html or 'id="settings-form"' in html
    assert "fast_tick_sec" in html and "auto_scan_chats" in html
    assert 'href="/settings">设置</a>' in html
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_settings.py::test_settings_page_renders -v`
Expected: FAIL（404 或断言失败，模板与导航入口缺失）。

- [x] **Step 3: 最小实现**

新建 `app/web/templates/settings.html`：

```html
{% extends "base.html" %}
{% block title %}采集器设置{% endblock %}
{% block content %}
<h1>采集器设置</h1>
<p class="page-sub">参数保存后即时生效，无需重启；未配置项使用 .env 默认值。</p>
<form id="settings-form" class="result-card">
  <div id="settings-error" class="form-error" hidden></div>
  <section>
    <h2>同步频次</h2>
    <p>DOM 增量轮询间隔（秒）<input class="input" data-key="fast_tick_sec" data-type="number" step="0.1" min="0.1"></p>
    <p>IDB 校准轮询间隔（秒）<input class="input" data-key="slow_tick_sec" data-type="number" step="0.1" min="0.1"></p>
  </section>
  <section>
    <h2>自动扫描</h2>
    <p>扫描间隔（秒）<input class="input" data-key="auto_scan_interval_sec" data-type="number" step="1" min="1"></p>
    <p>单次扫描会话数上限 <input class="input" data-key="auto_scan_max_chats" data-type="number" step="1" min="1" max="1000"></p>
    <p>会话停留时长（秒）<input class="input" data-key="auto_scan_settle_sec" data-type="number" step="0.1" min="0.1" max="30"></p>
    <p>启用自动扫描 <input class="input" data-key="auto_scan_chats" data-type="checkbox"></p>
  </section>
  <div class="toolbar">
    <button class="btn" type="submit">保存</button>
    <span class="muted" id="settings-saved" hidden>已保存</span>
  </div>
</form>
{% endblock %}
```

> 每个 `data-key` 输入框旁「恢复默认」按钮由 app.js 动态注入（`POST /api/settings/reset`），保持模板精简。

- [x] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_settings.py -v`
Expected: 8 passed（既有 7 + 页面 1）。

- [x] **Step 5: 提交**

```bash
git add app/web/templates/settings.html tests/web/test_settings.py
git commit -m "feat: settings.html 设置页 (分组表单 + 生效值/默认值展示)"
```

#### 4.2 首页采集器状态控制区 + 扫描进度

- [x] **Step 1: 写失败测试**（追加到 `tests/web/test_scan_api.py`）

```python
def test_home_renders_scan_button_and_status_area(tmp_data):
    html = TestClient(create_app()).get("/").text
    assert "立即全量扫描" in html
    assert "id=\"scan-control\"" in html
    assert "id=\"scan-progress\"" in html
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_scan_api.py::test_home_renders_scan_button_and_status_area -v`
Expected: FAIL（home.html 无扫描区）。

- [x] **Step 3: 最小实现**

`app/web/templates/home.html` 采集器状态 section 重构（替换 home.html:12-15）：

```html
  <section>
    <h2>采集器状态</h2>
    <div id="collector-status">
      {% if status %}<p>连接: <strong>{{ '在线' if alive else '离线' }}</strong> · 状态: {{ status.state or '未知' }}</p>{% else %}<p>采集器未启动 (无 status.json)</p>{% endif %}
    </div>
    <div id="scan-control" class="toolbar" style="margin-top:8px">
      <button class="btn" id="scan-btn">立即全量扫描</button>
      <span class="muted" id="scan-hint"></span>
    </div>
    <div id="scan-progress" class="scan-progress" hidden>
      <span id="scan-progress-text"></span>
      <div class="progress"><div id="scan-progress-bar" class="progress-bar" style="width:0%"></div></div>
    </div>
  </section>

  <!-- 手动扫描确认模态框 -->
  <div id="scan-modal" class="modal" hidden>
    <div class="modal-box">
      <h3>确认全量扫描</h3>
      <p>将逐个打开全部会话并读取可见正文，<strong>未读消息会被标记为已读</strong>。扫描期间自动周期扫描将跳过。</p>
      <div class="toolbar" style="justify-content:flex-end">
        <button class="btn btn-ghost" id="scan-modal-cancel">取消</button>
        <button class="btn" id="scan-modal-confirm">开始扫描</button>
      </div>
    </div>
  </div>
  ```

> 移除原 `hx-get` 轮询属性，改由 app.js `renderCollectorStatus()` fetch 渲染（见 4.3）。

- [x] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_scan_api.py -v`
Expected: 5 passed（既有 4 + 首页 1）。

- [x] **Step 5: 提交**

```bash
git add app/web/templates/home.html tests/web/test_scan_api.py
git commit -m "feat: 首页采集器状态控制区 (立即全量扫描按钮 + 进度容器)"
```

#### 4.3 app.js 设置提交 / 扫描触发 / 状态与进度轮询

- [x] **Step 1: 最小实现**

`app/web/static/js/app.js` 末尾追加（沿用事件委托风格，兼容 htmx 动态 DOM）：

```js
// collector-settings-center: 采集器设置中心 (设置读写 / 扫描触发 / 状态进度渲染)
(function () {
  function fmtValue(v) { return typeof v === "boolean" ? (v ? "true" : "false") : String(v); }

  function initSettings() {
    var form = document.getElementById("settings-form");
    if (!form) return;
    var inputs = form.querySelectorAll("[data-key]");
    var errBox = document.getElementById("settings-error");
    var saved = document.getElementById("settings-saved");
    function showErr(msg) {
      if (!errBox) return;
      errBox.textContent = msg;
      errBox.hidden = !msg;
    }
    function load() {
      fetch("/api/settings").then(function (r) { return r.json(); }).then(function (d) {
        inputs.forEach(function (el) {
          var key = el.getAttribute("data-key");
          var v = d.values[key];
          if (el.getAttribute("data-type") === "checkbox") {
            el.checked = v === true || v === "true";
            var def = document.createElement("span");
            def.className = "muted";
            def.style.cssText = "margin-left:6px;font-size:12px";
            def.textContent = "默认 " + fmtValue(d.defaults[key]);
            if (el.parentNode.querySelector(".rt-default")) el.parentNode.querySelector(".rt-default").textContent = "默认 " + fmtValue(d.defaults[key]);
            else { def.className = "rt-default muted"; el.parentNode.appendChild(def); }
          } else {
            el.value = v;
            if (!el.parentNode.querySelector(".rt-default")) {
              var s = document.createElement("span");
              s.className = "rt-default muted";
              s.style.cssText = "margin-left:6px;font-size:12px";
              s.textContent = "默认 " + d.defaults[key];
              el.parentNode.appendChild(s);
            }
          }
        });
      });
    }
    function resetKey(key, btn) {
      fetch("/api/settings/reset", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: key }) })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          load();
          if (btn) btn.closest("p").querySelector("[data-key]").value = d.defaults[key];
          showErr("");
        });
    }
    inputs.forEach(function (el) {
      var key = el.getAttribute("data-key");
      var p = el.closest("p");
      if (p) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-sm btn-ghost";
        btn.textContent = "恢复默认";
        btn.style.cssText = "margin-left:8px";
        btn.addEventListener("click", function () { resetKey(key, btn); });
        p.appendChild(btn);
      }
    });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var values = {};
      inputs.forEach(function (el) {
        var key = el.getAttribute("data-key");
        values[key] = el.getAttribute("data-type") === "checkbox" ? el.checked : el.value;
      });
      fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values: values }) })
        .then(function (r) {
          return r.json().then(function (j) { return { ok: r.ok, j: j }; });
        })
        .then(function (res) {
          if (!res.ok) {
            showErr(res.j.error || "保存失败");
            if (saved) saved.hidden = true;
            return;
          }
          showErr("");
          if (saved) { saved.hidden = false; setTimeout(function () { saved.hidden = true; }, 2000); }
          load();
        })
        .catch(function () { showErr("网络错误"); });
    });
    load();
  }

  function initScanControl() {
    var btn = document.getElementById("scan-btn");
    if (!btn) return;
    var modal = document.getElementById("scan-modal");
    var hint = document.getElementById("scan-hint");
    function openModal() {
      if (!modal) return;
      modal.hidden = false;
      var cancel = document.getElementById("scan-modal-cancel");
      var ok = document.getElementById("scan-modal-confirm");
      function close() { modal.hidden = true; }
      cancel.onclick = close;
      ok.onclick = function () {
        close();
        btn.disabled = true;
        fetch("/api/collector/scan", { method: "POST" })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
          .then(function (res) {
            if (!res.ok) { hint.textContent = res.j.error || "请求被拒绝"; }
            else {
              hint.textContent = "扫描已排队，进度即将显示";
              var prog = document.getElementById("scan-progress");
              if (prog) prog.hidden = false;
            }
          })
          .finally(function () { btn.disabled = false; });
      };
    }
    btn.addEventListener("click", openModal);
  }

  function renderCollectorStatus() {
    var box = document.getElementById("collector-status");
    var progress = document.getElementById("scan-progress");
    if (!box) return;
    fetch("/api/collector/status").then(function (r) { return r.json(); }).then(function (d) {
      var st = d.status || {};
      box.innerHTML = "<p>连接: <strong>" + (d.alive ? "在线" : "离线") +
        "</strong> · 状态: " + (st.state || "未知") +
        (st.last_sync ? " · 最近同步: " + new Date(st.last_sync * 1000).toLocaleString() : "") + "</p>";
      var scan = d.scan || null;
      if (scan && progress) {
        var text = document.getElementById("scan-progress-text");
        var bar = document.getElementById("scan-progress-bar");
        if (scan.running) {
          progress.hidden = false;
          var pct = (scan.total > 0) ? Math.round((scan.current / scan.total) * 100) : 0;
          if (text) text.textContent = "扫描中: 已扫 " + scan.current + "/" + scan.total + " 会话 · 新入库 " + scan.ingested + " 条";
          if (bar) bar.style.width = pct + "%";
        } else if (scan.done) {
          progress.hidden = false;
          if (text) text.textContent = "扫描完成: 新入库 " + scan.ingested + " 条" +
            (scan.finished_at ? " · 完成于 " + new Date(scan.finished_at * 1000).toLocaleString() : "");
          if (bar) bar.style.width = "100%";
          var hint = document.getElementById("scan-hint");
          if (hint) hint.textContent = "";
        }
      }
    }).catch(function () { box.innerHTML = "<p>状态不可用</p>"; });
  }

  // 统一轮询: 横幅 + 状态区 + 扫描进度 (在线 15s / 离线 5s, 用户决策: 消除双轮询)
  (function () {
    var banner = document.getElementById("collector-banner");
    var NORMAL_MS = 15000, FAST_MS = 5000;
    function check() {
      fetch("/api/collector/status").then(function (r) { return r.json(); }).then(function (d) {
        var down = !d.alive;
        if (banner) banner.hidden = !down;
        renderCollectorStatus();          // 状态区 + 进度渲染共用同一数据
        timer = setTimeout(check, down ? FAST_MS : NORMAL_MS);
      }).catch(function () {
        if (banner) banner.hidden = false;
        timer = setTimeout(check, FAST_MS);
      });
    }
    var timer = setTimeout(check, 0);
  })();

  document.addEventListener("DOMContentLoaded", function () {
    initSettings();
    initScanControl();
    if (document.getElementById("collector-status")) {
      renderCollectorStatus();
    }
  });
})();
```

- [x] **Step 2: 验证（JS 走读 + 回归）**

JS 走读核对关键点：

Run: `Select-String -Path app\web\static\js\app.js -Pattern "api/settings|api/collector/scan|api/collector/status|renderCollectorStatus|scan-modal"`
Expected: 覆盖 3 个端点调用、状态渲染函数、模态框确认（设计 §6.1/§7.2 全部落地）。

> 注意：原有的横幅独立轮询 IIFE（现 app.js:68-88）被本步骤的「统一轮询」替换合并，需移除旧 IIFE 避免双轮询；`#collector-banner` 显隐逻辑迁移到统一轮询的 `check()` 内。

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_scan_api.py tests/web/test_settings.py -q`
Expected: 全部 PASS。

Run: `.venv/Scripts/python.exe -m compileall -q app`
Expected: 无输出（退出码 0）。

- [x] **Step 3: 提交**

```bash
git add app/web/static/js/app.js
git commit -m "feat: app.js 设置读写/扫描触发确认/采集器状态与扫描进度 5s 轮询渲染"
```

---

### Task 5: 前端 — 全站视觉改版

**Files:**
- Modify: `app/web/static/css/app.css`
- Modify: `app/web/templates/base.html` 及全部页面模板（home/customers/chat/chat_messages/knowledge/knowledge_docs/search/search_results/cleanup/profile_list/reply_result/analysis/reply_polling + settings）

**设计依据:** CSS 变量演进（色板/圆角/阴影/间距 token），组件样式收敛（nav/card/btn/form/table/tag/empty/status-pill），现有 768px 断点扩展，本地静态资源离线可用（设计 §7.4、D5）。逐页改版、保留 htmx 与类名兼容。

#### 5.1 app.css 设计变量与组件收敛

- [x] **Step 1: 改造 app.css**

`app/web/static/css/app.css` 的 `:root` 块（app.css:1-10）扩展为语义 token 体系：

```css
:root {
  /* 色板 (primary 保持品牌蓝, 语义化补充) */
  --primary: #2563eb;
  --primary-soft: rgba(37, 99, 235, .1);
  --success: #16a34a;
  --danger: #dc2626;
  --warning: #f59e0b;
  --bg: #f5f7fa;
  --card: #fff;
  --text: #1f2937;
  --muted: #6b7280;
  --border: #e5e7eb;
  /* 圆角 / 阴影 / 间距 */
  --radius-sm: 8px;
  --radius: 10px;
  --radius-lg: 14px;
  --shadow: 0 1px 3px rgba(0, 0, 0, .08);
  --shadow-lg: 0 6px 16px rgba(0, 0, 0, .12);
  --space-1: 4px; --space-2: 8px; --space-3: 12px;
  --space-4: 16px; --space-5: 24px;
}
```

组件收敛要点（保持既有类名兼容，渐进替换）：
- 引入 `.status-pill`（在线绿 / 离线红 / 扫描中蓝）用于首页状态展示。
- 新增 `.scan-progress` + `.progress` / `.progress-bar`：细进度条（高 6px，圆角，蓝底填充），配合扫描进度文本。
- 新增 `.modal` / `.modal-box`：半透明遮罩 + 居中卡片（用于手动扫描确认），`modal[hidden]` 隐藏。
- `.card` 通用卡片 + 既有 `.result-card`/`.stat-card`/`.customer-card` 保持可用（统一 border/radius/shadow 走变量）。
- `.form-error` 校验错误提示（红色，设置页 400 展示用）。
- `.btn` 系列补充 `.btn-success`（绿，用于扫描触发）。
- 表格 `.data-table`、徽章 `.tag`、空态 `.empty`、`.muted` 全部改走 token。
- 移动端：768px 断点内 `.two-col` 已单列；新增 `.filter-bar`/`.toolbar` 在小屏换行（`flex-wrap` 已有），`#scan-control` 按钮全宽。

- [x] **Step 2: 验证（渲染断言 + 走读）**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_scan_api.py tests/web/test_settings.py tests/web/test_banner.py tests/web/test_routes.py::test_home_shows_stats -q`
Expected: 全部 PASS（类名兼容，既有测试不受样式改动影响）。

Run: `.venv/Scripts/python.exe -m compileall -q app`
Expected: 无输出（退出码 0）。

- [x] **Step 3: 提交**

```bash
git add app/web/static/css/app.css
git commit -m "style: app.css 语义 token 体系 (色板/圆角/阴影/间距) + status-pill/form-error 组件"
```

#### 5.2 全站模板统一版式

- [x] **Step 1: 逐页套用版式**

统一「页面标题区（`h1.page-title` + `p.page-sub`）+ 卡片布局 + 操作区（`.toolbar`）」：

- `home.html`：标题区化 + 统计卡（已有 `.stat-grid`）保留。
- `customers.html`：`<h1>` 加 `page-title`，筛选条已有 `.filter-bar`，卡片区对齐 `.card-grid`。
- `chat.html` / `chat_messages.html`：标题区化，操作按钮归入 `.toolbar`。
- `knowledge.html`：上传/导出工具栏归入 `.toolbar`，检索区归入 `.result-card`。
- `search.html` / `search_results.html`：输入区卡片化，结果沿用 `.result-card`。
- `cleanup.html`：两表单区沿用 `.result-card`，标题区化。
- `settings.html`：已按 4.1 使用 `.result-card` + `.page-sub`。
- `profile_list.html` / `reply_result.html` / `analysis.html` / `reply_polling.html` / `knowledge_docs.html`：最小对齐（标题/间距/类名），不重构交互逻辑。

> 约束：仅调整 class 与结构包裹，**不动 htmx 属性与既有端点契约**（保留 htmx + 类名兼容策略，设计 §10 风险缓解）。

- [x] **Step 2: 验证**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全部通过（模板改动不破坏既有渲染断言）。

- [x] **Step 3: 提交**

```bash
git add app/web/templates
git commit -m "style: 全站模板统一版式 (页面标题区/卡片布局/操作区, 保留 htmx 与类名兼容)"
```

#### 5.3 base.html 导航升级（图标 + 标签 + 设置入口）+ 离线验证

- [x] **Step 1: 改造 base.html 导航**

`app/web/templates/base.html` 导航行（base.html:9-11）替换为**内联 SVG 图标**（无外部依赖）+ 标签，并加入「设置」入口（用户决策：SVG 优于 emoji）：

```html
<nav class="nav">
  <span class="brand">外贸客户知识库</span>
  <div class="nav-links">
    <a href="/"><svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10.5 12 3l9 7.5V21h-6v-6h-6v6H3z" fill="currentColor"/></svg> 首页</a>
    <a href="/customers"><svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm0 2c-4 0-8 2-8 5v2h16v-2c0-3-4-5-8-5z" fill="currentColor"/></svg> 客户</a>
    <a href="/knowledge"><svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zm4 1v2h8V6zm0 4v2h8v-2zm0 4v2h5v-2z" fill="currentColor"/></svg> 知识库</a>
    <a href="/search"><svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M10 4a6 6 0 1 0 3.6 10.8l4.3 4.3 1.4-1.4-4.3-4.3A6 6 0 0 0 10 4zm0 2a4 4 0 1 1 0 8 4 4 0 0 1 0-8z" fill="currentColor"/></svg> 搜索</a>
    <a href="/cleanup"><svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6zm3-9h2v8H9zm4 0h2v8h-2zM15.5 4l-1-1h-5l-1 1H5v2h14V4z" fill="currentColor"/></svg> 清理</a>
    <a href="/settings"><svg class="nav-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M19.4 13a7.7 7.7 0 0 0 .1-1c0-.34-.04-.67-.1-1l2.1-1.6a.5.5 0 0 0 .1-.7l-2-3.4a.5.5 0 0 0-.6-.2l-2.5 1a7.6 7.6 0 0 0-1.7-1l-.4-2.6a.5.5 0 0 0-.5-.5h-4a.5.5 0 0 0-.5.5l-.4 2.6c-.6.3-1.2.6-1.7 1l-2.5-1a.5.5 0 0 0-.6.2l-2 3.4a.5.5 0 0 0 .1.7L5.6 11c-.1.33-.1.66-.1 1s.04.67.1 1l-2.1 1.6a.5.5 0 0 0-.1.7l2 3.4c.1.2.4.3.6.2l2.5-1c.5.4 1.1.7 1.7 1l.4 2.6c0 .3.2.5.5.5h4c.3 0 .5-.2.5-.5l.4-2.6c.6-.3 1.2-.6 1.7-1l2.5 1c.2.1.5 0 .6-.2l2-3.4c.1-.2.1-.5-.1-.7zM12 15.5A3.5 3.5 0 1 1 12 8a3.5 3.5 0 0 1 0 7.5z" fill="currentColor"/></svg> 设置</a>
  </div>
</nav>
```

`app.css` 补 `.nav-ico`（宽 16px、高 16px、垂直对齐、`currentColor` 跟随链接颜色）与 `.nav-links` 激活态（当前页高亮可选）。

- [x] **Step 2: 验证（页面渲染 + 离线可用）**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_settings.py::test_settings_page_renders -v`
Expected: PASS（断言含 `href="/settings">设置</a>`）。

全站离线可用核对——静态资源全部本地（htmx/app.css/app.js 均在 `app/web/static/`，无 CDN）：

Run: `Select-String -Path app\web\templates\*.html -Pattern "https?://" -SimpleMatch`
Expected: 无外部 URL 命中（全部本地资源）。

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全部通过。

Run: `.venv/Scripts/python.exe -m compileall -q app`
Expected: 无输出（退出码 0）。

- [x] **Step 3: 提交**

```bash
git add app/web/templates/base.html app/web/static/css/app.css
git commit -m "feat: base.html 导航升级 (图标+标签, 含设置入口) + 本地静态资源离线验证"
```

---

### Task 6: 测试与验证

**Files:** 无（聚合验证）

#### 6.1 单元/接口测试聚合

- [x] **Step 1: 运行全量单元与接口测试**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_runtime_settings.py tests/storage/test_sqlite_store.py tests/collector/test_scanner.py tests/web/test_settings.py tests/web/test_scan_api.py -v`
Expected: 全部 PASS。

核对覆盖（tasks 6.1）：
1. RuntimeSettings：set/get/reset/all、类型转换、脏数据回退、未配置项返回默认。
2. scan_requests：插入/pending/running/done/attempts 递增/`has_active_scan_request` busy 判定。
3. settings API 校验：边界值（0、负、超上限、非数值、非布尔、未知 key）→ 400，原子保存。
4. scan 互斥：Web 层重复触发 409；采集器 `_drain_scan_requests` 期间 last_scan 刷新跳过自动分支。

- [x] **Step 2: 提交**

```bash
git add -A
git commit -m "test: collector-settings-center 单元与接口测试聚合"
```

#### 6.2 手动验证清单

- [x] **Step 1: 采集器运行中触发全量扫描 → 进度推进 → 完成**

启动采集器与 Web，点击「立即全量扫描」→ 确认弹窗 → 前端显示 `扫描中: 已扫 current/total 会话 · 新入库 N 条`（5s 轮询推进）→ 完成显示 `扫描完成: 新入库 N 条`。确认：
- 扫描期间不会误触发自动周期扫描（日志无重叠 scan_all_chats）。
- 再次点击按钮 → `已有扫描进行中` 提示（409 busy）。

- [x] **Step 2: 改频次 → 即时采用 + 重启保留**

设置页把 `fast_tick_sec` 改为 5 → 保存 → 观察采集器日志轮询间隔变为 ~5s（无需重启）；重启采集器 → 值保留；点「恢复默认」→ 回到 .env 默认（2.0）。

- [x] **Step 3: 非法值被拒，提示可见**

提交 `max_chats=0` / `settle=0.05` / `auto_scan_chats=yes` / 未知 key → 字段级错误提示可见，原值未变；同时向 DB 直接写入脏值（如 `fast_tick_sec='abc'`）→ 采集器不崩、按默认值继续跑（get_typed 回退）。

- [x] **Step 4: 采集器离线时扫描排队**

停掉采集器 → 点「立即全量扫描」→ 返回 accepted（不拦截）→ 前端提示排队；重启采集器 → 自动消费执行扫描 → 进度出现并完成。

- [x] **Step 5: 视觉回归走读**

逐页浏览首页/客户/聊天/知识库/搜索/清理/设置，确认统一标题区、卡片、操作区；缩小窗口到 <768px 确认布局可用；DevTools 确认无外部网络请求（离线可用）。

#### 6.3 全量回归

- [x] **Step 1: 全量测试**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全部通过（新增 + 既有）。

- [x] **Step 2: 语法编译检查**

Run: `.venv/Scripts/python.exe -m compileall -q app`
Expected: 无输出（退出码 0）。

- [x] **Step 3: 代码走读核对设计约束**

1. `settings` 表只存显式配置项：`RuntimeSettings.set` UPSERT、`reset` DELETE；Web 校验「全通过才写库」（`rg -n "settings_post" app/web/routes.py` 确认校验在写库前）。
2. `scan_requests` 与 `backfill_requests` 职责分离：`_drain_scan_requests` / `_drain_backfill_requests` 各自独立消费（`rg -n "_drain_scan_requests|_drain_backfill_requests" app/collector/scanner.py`）。
3. status.json `scan` 字段容错：routes `(s or {}).get("scan") or None`；app.js `d.scan || null`（`rg -n "scan" app/web/routes.py app/web/static/js/app.js`）。
4. 手动扫描与自动扫描互斥：`last_scan=now` + `not self._manual_scan_active` 双保险（`rg -n "_manual_scan_active|last_scan" app/collector/scanner.py`）。
5. 无新外部依赖：CSS/JS 全部本地（`rg -n "https?://|cdn" app/web` 无命中）。

- [x] **Step 4: 提交（如有走读修正，合并进本次）**

```bash
git add -A
git commit -m "chore: collector-settings-center 回归验证通过"
```
