# WhatsApp 双向文字收发 + 实时会话列表 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有只读采集器升级为个人自用的双向文字聊天客户端 —— 网页实时看到新消息（含左栏未读红点+最后一句），并可直接发送纯文字消息（默认只读 + 发送开关 + 发送前确认）。

**Architecture:** 继续用 SQLite 意图表在 Web 与采集器两进程间传指令（与 `backfill_requests`/`scan_requests` 同构）：新增 `send_requests`（发送任务）+ `chat_previews`（会话实时预览）。采集器主循环 `run()` 每轮消费发送任务、跟随 `collector_follow_chat` 目标、并刷新 `chat_previews`。发送用 `send_enabled` 运行时开关（默认 false）在 Web 层与采集器层双重把关。

**Tech Stack:** Python 3.11、FastAPI、SQLite(WAL+FTS5)、Playwright、HTMX + vanilla JS、Jinja2。

---

## 文件结构

**新建：**
- `app/collector/sender.py` — 页面操作：`send_text`（写输入框+发送）、`open_chat`（搜索框切会话），选择器集中一处
- `app/collector/chat_list.py` — `read_chat_list(cdp)`：用 `Runtime.evaluate` 读左栏会话列表（name/unread/preview）
- `app/web/templates/send_polling.html` — 发送状态轮询片段（镜像 `reply_polling.html`）
- `tests/collector/test_sender.py`、`tests/collector/test_chat_list.py`
- `tests/storage/test_send_store.py`、`tests/web/test_send_api.py`

**修改：**
- `app/storage/schema.sql` — 加 `send_requests`、`chat_previews` 表
- `app/storage/sqlite_store.py` — 加发送任务 + 会话预览方法
- `app/storage/runtime_settings.py` — `DEFAULTS` 加 `send_enabled: False`
- `app/collector/scanner.py` — `run()` 接入 `_drain_send_requests` / `_sync_follow` / `_sync_chat_previews`
- `app/web/routes.py` — `SETTING_VALIDATORS` 加 send_enabled；新增 `/api/send`、`/api/send/status`；`workspace_chat` 写 `collector_follow_chat` 并传 `send_enabled`
- `app/web/templates/workspace_chat.html` — 底部输入框+发送按钮
- `app/web/templates/reply_result.html` — 「直接发送」按钮
- `app/web/templates/workspace_customers.html` — 左栏预览 + 实时未读
- `app/web/static/js/app.js` — 发送处理 + 确认 + 轮询降到 1s
- `docs/RISK.md` — 发送风险提示

---

## Task 1: schema 增加两张表

**Files:**
- Modify: `app/storage/schema.sql`（文件末尾追加）

- [ ] **Step 1: 追加建表语句**

在 `app/storage/schema.sql` 末尾追加（现有 104 行之后）：

```sql
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
```

- [ ] **Step 2: 验证新库建表**

Run: `.venv\Scripts\python.exe -c "from app.storage.sqlite_store import SqliteStore; s = SqliteStore(); print([r[0] for r in s.conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('send_requests','chat_previews')\").fetchall()])"`
Expected: `['send_requests', 'chat_previews']`

- [ ] **Step 3: Commit**

```bash
git add app/storage/schema.sql
git commit -m "feat: send_requests + chat_previews 表"
```

---

## Task 2: SqliteStore 发送任务方法

**Files:**
- Create: `tests/storage/test_send_store.py`
- Modify: `app/storage/sqlite_store.py`（在 scan_requests 方法块附近追加）

- [ ] **Step 1: 写失败测试**

```python
# tests/storage/test_send_store.py
from app.storage.sqlite_store import SqliteStore


def test_send_request_lifecycle(tmp_data):
    store = SqliteStore()
    rid = store.create_send_request("c1", "hello")
    assert isinstance(rid, int) and rid > 0
    req = store.get_send_request(rid)
    assert req["status"] == "pending" and req["text"] == "hello"
    assert store.next_pending_send_request()["id"] == rid
    store.mark_send_request_running(rid)
    assert store.get_send_request(rid)["status"] == "running"
    store.mark_send_request_done(rid)
    assert store.get_send_request(rid)["status"] == "done"
    assert store.next_pending_send_request() is None


def test_send_request_retries_up_to_three(tmp_data):
    store = SqliteStore()
    rid = store.create_send_request("c1", "hi")
    store.bump_send_request_attempts(rid, "boom")
    store.bump_send_request_attempts(rid, "boom")
    assert store.next_pending_send_request()["id"] == rid  # 2 次失败仍可重试
    store.bump_send_request_attempts(rid, "boom")
    assert store.next_pending_send_request() is None  # 满 3 次不再取


def test_send_request_mark_failed_terminal(tmp_data):
    store = SqliteStore()
    rid = store.create_send_request("c1", "hi")
    store.mark_send_request_failed(rid, "发送功能未开启")
    r = store.get_send_request(rid)
    assert r["status"] == "failed" and r["done"] == 1
    assert store.next_pending_send_request() is None


def test_chat_previews_upsert_and_lookup(tmp_data):
    store = SqliteStore()
    # 建客户与映射
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,NULL,NULL,?,NULL)",
                       ("cust1", "Alice", "10086", 0))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    store.upsert_chat_previews([{"chat_id": "c1", "unread_count": 3, "preview": "need price"}])
    p = store.get_customers_chat_preview(["cust1"])
    assert p["cust1"]["unread"] == 3
    assert p["cust1"]["preview"] == "need price"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/storage/test_send_store.py -v`
Expected: FAIL（`SqliteStore` 无 `create_send_request` 等属性）

- [ ] **Step 3: 实现方法**

在 `app/storage/sqlite_store.py` 的 `bump_scan_request_attempts` 方法之后追加：

```python
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
        """按显示名反查 chat_id (chats + contacts)。返回 {name: chat_id}。"""
        out = {}
        for r in self.conn.execute(
                "SELECT id, display_name FROM chats WHERE display_name IS NOT NULL").fetchall():
            out.setdefault(r["display_name"], r["id"])
        for r in self.conn.execute(
                "SELECT jid, display_name FROM contacts WHERE display_name IS NOT NULL").fetchall():
            out.setdefault(r["display_name"], r["jid"])
        return out
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/storage/test_send_store.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/storage/sqlite_store.py tests/storage/test_send_store.py
git commit -m "feat: send_requests + chat_previews 存储方法"
```

---

## Task 3: send_enabled 运行时开关

**Files:**
- Modify: `app/storage/runtime_settings.py`（`DEFAULTS`）
- Modify: `app/web/routes.py`（`SETTING_VALIDATORS`）

- [ ] **Step 1: DEFAULTS 加 send_enabled**

在 `app/storage/runtime_settings.py` 的 `DEFAULTS` 字典里 `"auto_scan_chats": settings.auto_scan_chats,` 之后加一行：

```python
        "send_enabled": False,
```

- [ ] **Step 2: SETTING_VALIDATORS 加 send_enabled**

在 `app/web/routes.py` 的 `SETTING_VALIDATORS` 字典里 `"auto_scan_chats": {"kind": "bool"},` 之后加一行：

```python
    "send_enabled":         {"kind": "bool"},
```

- [ ] **Step 3: 验证**

Run: `.venv\Scripts\python.exe -c "from app.storage.runtime_settings import RuntimeSettings; from app.config import settings; print('send_enabled' in RuntimeSettings.DEFAULTS, RuntimeSettings.DEFAULTS['send_enabled'])`
Expected: `True False`

- [ ] **Step 4: Commit**

```bash
git add app/storage/runtime_settings.py app/web/routes.py
git commit -m "feat: send_enabled 运行时开关 (默认关闭)"
```

---

## Task 4: sender.py 页面操作

**Files:**
- Create: `app/collector/sender.py`
- Create: `tests/collector/test_sender.py`

- [ ] **Step 1: 写失败测试（假 page）**

```python
# tests/collector/test_sender.py
import asyncio
from app.collector.sender import send_text, open_chat


class FakeLocator:
    def __init__(self, found=True):
        self._found = found

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self._found else 0

    async def click(self):
        self._clicked = True


class FakePage:
    def __init__(self, box_found=True, row_found=True):
        self.typed = []
        self.pressed = []
        self._box_found = box_found
        self._row_found = row_found

    def locator(self, sel):
        if "row" in sel:
            return FakeLocator(self._row_found)
        return FakeLocator(self._box_found)

    @property
    def keyboard(self):
        return self

    async def type(self, text, delay=0):
        self.typed.append(text)

    async def press(self, key):
        self.pressed.append(key)

    async def wait_for_timeout(self, ms):
        pass


def test_send_text_types_and_enters():
    page = FakePage()
    assert asyncio.run(send_text(page, "hello")) is True
    assert page.typed == ["hello"]
    assert page.pressed == ["Enter"]


def test_send_text_no_box_raises():
    import pytest
    page = FakePage(box_found=False)
    with pytest.raises(RuntimeError):
        asyncio.run(send_text(page, "hello"))


def test_open_chat_types_query_and_clicks_row():
    page = FakePage()
    assert asyncio.run(open_chat(page, "Alice")) is True
    assert page.typed == ["Alice"]
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/collector/test_sender.py -v`
Expected: FAIL（`app.collector.sender` 不存在）

- [ ] **Step 3: 实现 sender.py**

```python
# app/collector/sender.py
"""发送文字 + 打开会话 (搜索框切换) 的页面操作。

只做页面 DOM 操作, 不持有 store。选择器集中在此, WhatsApp Web 改版时单点修补。
"""
from playwright.async_api import Page

MESSAGE_BOX_SELECTORS = [
    'footer div[contenteditable="true"][data-tab="10"]',
    'footer div[contenteditable="true"]',
    'div[contenteditable="true"][data-tab="10"]',
]
SEARCH_BOX_SELECTORS = [
    'div[contenteditable="true"][data-tab="3"]',
    'div[contenteditable="true"][data-testid="chat-list-search"]',
]
CHAT_LIST_ROW_SELECTOR = "[data-testid='chat-list'] div[role='row']"


async def _first(page: Page, selectors: list[str]):
    """按顺序返回第一个存在的元素; 找不到抛 RuntimeError。"""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0:
                return loc
        except Exception:
            continue
    raise RuntimeError(f"未找到元素: {selectors}")


async def send_text(page: Page, text: str) -> bool:
    """在当前打开的会话输入框写入文字并回车发送。返回是否成功。"""
    box = await _first(page, MESSAGE_BOX_SELECTORS)
    await box.click()
    await page.keyboard.type(text)
    await page.keyboard.press("Enter")
    return True


async def open_chat(page: Page, query: str) -> bool:
    """通过搜索框定位并打开会话 (query 为显示名或手机号)。返回是否成功。"""
    search = await _first(page, SEARCH_BOX_SELECTORS)
    await search.click()
    await page.keyboard.type(query)
    await page.wait_for_timeout(800)  # 等搜索结果出现
    row = page.locator(CHAT_LIST_ROW_SELECTOR).first
    if await row.count() == 0:
        return False
    await row.click()
    return True
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/collector/test_sender.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/collector/sender.py tests/collector/test_sender.py
git commit -m "feat: sender.py 发送与切会话页面操作"
```

---

## Task 5: chat_list.py 读左栏会话列表

**Files:**
- Create: `app/collector/chat_list.py`
- Create: `tests/collector/test_chat_list.py`

- [ ] **Step 1: 写失败测试（假 cdp）**

```python
# tests/collector/test_chat_list.py
import asyncio
from app.collector.chat_list import read_chat_list


class FakeCdp:
    def __init__(self, result):
        self._result = result
        self.expr = None

    async def eval_async_readonly(self, expression):
        self.expr = expression
        return self._result


def test_read_chat_list_returns_rows():
    rows = [{"name": "Alice", "unread": 2, "preview": "need price"}]
    cdp = FakeCdp(rows)
    assert asyncio.run(read_chat_list(cdp)) == rows
    assert "chat-list" in cdp.expr  # JS 表达式里含 chat-list 容器


def test_read_chat_list_null_returns_empty():
    cdp = FakeCdp(None)
    assert asyncio.run(read_chat_list(cdp)) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/collector/test_chat_list.py -v`
Expected: FAIL（`app.collector.chat_list` 不存在）

- [ ] **Step 3: 实现 chat_list.py**

```python
# app/collector/chat_list.py
"""通过 Runtime.evaluate 只读读取左栏会话列表 (name / unread / preview)。

不打开任何会话, 只读取左侧列表 DOM。相比解析平铺的 DOMSnapshot, JS 直读更稳健。
"""
from app.collector.readonly_cdp import ReadOnlyCDP

_CHAT_LIST_JS = """
(function() {
  var list = document.querySelector('[data-testid="chat-list"]');
  if (!list) return [];
  var rows = list.querySelectorAll('div[role="row"]');
  var out = [];
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    var t = r.querySelector('span[title]');
    var name = t ? t.getAttribute('title') : null;
    var unread = 0;
    var badge = r.querySelector('span[aria-label*="unread"], span[aria-label*="未读"]');
    if (badge) {
      var n = parseInt((badge.textContent || '').trim(), 10);
      unread = isNaN(n) ? 1 : n;
    }
    var p = r.querySelector('span[dir="auto"]');
    var preview = p ? (p.textContent || '').trim() : '';
    out.push({name: name, unread: unread, preview: preview});
  }
  return out;
})()
"""


async def read_chat_list(cdp: ReadOnlyCDP) -> list[dict]:
    """返回 [{name, unread, preview}]; 失败返回 []。"""
    try:
        rows = await cdp.eval_async_readonly(_CHAT_LIST_JS)
    except Exception:
        return []
    return rows if isinstance(rows, list) else []
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/collector/test_chat_list.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/collector/chat_list.py tests/collector/test_chat_list.py
git commit -m "feat: chat_list.py 读左栏会话列表"
```

---

## Task 6: Scanner 接入发送/跟随/预览

**Files:**
- Modify: `app/collector/scanner.py`
- Create: `tests/collector/test_send_drain.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/collector/test_send_drain.py
import asyncio
from app.collector.scanner import Scanner


class FakeRt:
    def get_typed(self, key, default):
        if key == "send_enabled":
            return True
        return default

    def get(self, key, default=None):
        return None


class Store:
    def __init__(self):
        self.calls = []
        self._pending = None

    def next_pending_send_request(self):
        return self._pending

    def mark_send_request_running(self, rid):
        self.calls.append(("running", rid))

    def mark_send_request_done(self, rid):
        self.calls.append(("done", rid))

    def mark_send_request_failed(self, rid, err):
        self.calls.append(("failed", rid, err))

    def bump_send_request_attempts(self, rid, err):
        self.calls.append(("bump", rid, err))


class FakePage:
    def __init__(self):
        self.opened = []
        self.sent = []

    def locator(self, sel):
        class L:
            @property
            def first(self):
                return self
            async def count(self):
                return 1
            async def click(self):
                pass
        return L()

    @property
    def keyboard(self):
        class K:
            async def type(self, t):
                pass
            async def press(self, k):
                pass
        return K()

    async def wait_for_timeout(self, ms):
        pass


def test_drain_send_when_enabled(tmp_data, monkeypatch):
    store = Store()
    store._pending = {"id": 1, "chat_id": "c1", "text": "hi"}
    sc = Scanner(None, store, None)
    sc._rt = FakeRt()
    sc.page = FakePage()
    sc._chat_lookup_query = lambda chat_id: "Alice"
    async def fake_open(page, query):
        sc.page.opened.append(query)
        return True
    async def fake_send(page, text):
        sc.page.sent.append(text)
        return True
    monkeypatch.setattr("app.collector.sender.open_chat", fake_open)
    monkeypatch.setattr("app.collector.sender.send_text", fake_send)
    asyncio.run(sc._drain_send_requests())
    assert ("done", 1) in store.calls
    assert sc.page.opened == ["Alice"]
    assert sc.page.sent == ["hi"]


def test_drain_send_skipped_when_disabled(tmp_data):
    store = Store()
    store._pending = {"id": 1, "chat_id": "c1", "text": "hi"}
    sc = Scanner(None, store, None)
    sc._chat_lookup_query = lambda chat_id: "Alice"
    class DisabledRt:
        def get_typed(self, key, default):
            return False
    sc._rt = DisabledRt()
    sc.page = FakePage()
    asyncio.run(sc._drain_send_requests())
    assert ("failed", 1, "发送功能未开启") in store.calls
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/collector/test_send_drain.py -v`
Expected: FAIL（`Scanner` 无 `_drain_send_requests`）

- [ ] **Step 3: 实现 Scanner 三个方法 + 接入 run()**

在 `app/collector/scanner.py` 的 `_drain_scan_requests` 方法之后追加三个方法：

```python
    def _chat_lookup_query(self, chat_id: str) -> str | None:
        """发送/跟随时用于搜索框的查询串: 显示名, 回退手机号。"""
        from app.profile.matcher import phone_from_jid
        try:
            r = self.store.conn.execute(
                "SELECT display_name FROM chats WHERE id=?", (chat_id,)).fetchone()
            if r and r["display_name"]:
                return r["display_name"]
        except Exception:
            pass
        return phone_from_jid(chat_id)

    async def _drain_send_requests(self):
        """消费发送任务 (纯文字)。send_enabled 关闭时直接 failed (防绕过)。"""
        if self.page is None:
            return
        enabled = (self._rt.get_typed("send_enabled", False)
                   if self._rt is not None else False)
        req = self.store.next_pending_send_request()
        if not req:
            return
        if not enabled:
            self.store.mark_send_request_failed(req["id"], "发送功能未开启 (send_enabled=false)")
            return
        self.store.mark_send_request_running(req["id"])
        try:
            from app.collector.sender import open_chat, send_text
            query = self._chat_lookup_query(req["chat_id"])
            if query:
                await open_chat(self.page, query)
            await send_text(self.page, req["text"])
            self.store.mark_send_request_done(req["id"])
        except Exception as e:
            self.store.bump_send_request_attempts(req["id"], str(e)[:300])

    async def _sync_follow(self):
        """读取 Web 设置的 follow_chat, 与当前会话不同则切换过去。"""
        if self.page is None or self._rt is None:
            return
        follow = self._rt.get("collector_follow_chat")
        if not follow or follow == self._current_chat_id:
            return
        query = self._chat_lookup_query(follow)
        if not query:
            return
        from app.collector.sender import open_chat
        try:
            await open_chat(self.page, query)
            self._current_chat_id = follow
        except Exception:
            pass  # 切换失败下轮重试

    async def _sync_chat_previews(self):
        """读左栏会话列表 → 映射 chat_id → 写 chat_previews。失败静默。"""
        if self.cdp is None:
            return
        try:
            from app.collector.chat_list import read_chat_list
            rows = await read_chat_list(self.cdp)
        except Exception:
            return
        if not rows:
            return
        try:
            name_to_id = self.store.resolve_chat_ids_by_names(
                [r.get("name") for r in rows if r.get("name")])
        except Exception:
            return
        previews = []
        for r in rows:
            cid = name_to_id.get(r.get("name"))
            if not cid:
                continue
            previews.append({"chat_id": cid, "unread_count": r.get("unread") or 0,
                             "preview": r.get("preview")})
        if previews:
            try:
                self.store.upsert_chat_previews(previews)
            except Exception:
                pass
```

然后在 `run()` 方法里，`await self._drain_scan_requests()` 这一行之后、`await self._drain_backfill_requests()` 之前插入：

```python
                await self._drain_send_requests()
                await self._sync_follow()
                await self._sync_chat_previews()
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/collector/test_send_drain.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/collector/scanner.py tests/collector/test_send_drain.py
git commit -m "feat: 采集器消费发送任务 + 跟随 + 会话预览"
```

---

## Task 7: Web 发送 API + follow_chat + send_enabled 传递

**Files:**
- Create: `tests/web/test_send_api.py`
- Modify: `app/web/routes.py`
- Create: `app/web/templates/send_polling.html`

- [ ] **Step 1: 写失败测试**

```python
# tests/web/test_send_api.py
from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore
from app.storage.runtime_settings import RuntimeSettings


def test_send_rejected_when_disabled(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,NULL,NULL,?,NULL)",
                       ("cust1", "Alice", "10086", 0))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/send", json={"chat_id": "c1", "text": "hi"})
    assert r.status_code == 403


def test_send_creates_task_when_enabled(tmp_data):
    store = SqliteStore()
    RuntimeSettings(store).set("send_enabled", "true")
    client = TestClient(create_app())
    r = client.post("/api/send", json={"chat_id": "c1", "text": "hi"})
    assert r.status_code == 200
    import re
    m = re.search(r"/api/send/status/(\d+)", r.text)
    assert m, f"未找到 task_id: {r.text[:200]}"
    rid = int(m.group(1))
    row = SqliteStore().get_send_request(rid)
    assert row["status"] == "pending" and row["text"] == "hi"


def test_send_status_endpoint(tmp_data):
    store = SqliteStore()
    rid = store.create_send_request("c1", "hi")
    client = TestClient(create_app())
    assert "发送中" in client.get(f"/api/send/status/{rid}").text
    store.mark_send_request_done(rid)
    assert "已发送" in client.get(f"/api/send/status/{rid}").text
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/web/test_send_api.py -v`
Expected: FAIL（`/api/send` 404）

- [ ] **Step 3: 实现路由 + 模板**

在 `app/web/routes.py` 中 `workspace_chat` 路由里，找到 `store.set_last_seen(customer_id, int(time.time()))` 这一行之前插入（写 follow_chat）：

```python
    # whatsapp-bidirectional-chat: 记录 Web 正在查看的会话, 采集器跟随切换
    if chat_id:
        try:
            RuntimeSettings(store).set("collector_follow_chat", chat_id)
        except Exception:
            pass
```

并把该路由最后 `TemplateResponse` 的上下文里加 `send_enabled`（在 `"customer": dict(customer) if customer else None` 之后加一个键）：

```python
         "customer": dict(customer) if customer else None,
         "send_enabled": bool(RuntimeSettings(store).get_typed("send_enabled", False))})
```

（注意：原字典末尾 `"customer": ...}` 之后原来直接是 `)`，需把 `}` 改成 `,` 再加新键。）

在 `app/web/routes.py` 的 `collector_scan` 路由之后追加两个路由：

```python
@router.post("/api/send")
async def send_message(request: Request):
    """whatsapp-bidirectional-chat: 创建发送任务。body: {chat_id, text}。
    send_enabled=false 时拒绝。"""
    body = await _parse_body(request)
    chat_id = (body.get("chat_id") or "").strip()
    text = (body.get("text") or "").strip()
    store = _store(request)
    rt = RuntimeSettings(store)
    if not rt.get_typed("send_enabled", False):
        return JSONResponse({"error": "发送功能未开启"}, status_code=403)
    if not chat_id:
        return JSONResponse({"error": "缺少 chat_id"}, status_code=400)
    if not text:
        return JSONResponse({"error": "消息为空"}, status_code=400)
    task_id = store.create_send_request(chat_id, text)
    return request.app.state.templates.TemplateResponse(
        request, "send_polling.html", {"task_id": task_id})


@router.get("/api/send/status/{task_id}")
async def send_status(task_id: str, request: Request):
    """whatsapp-bidirectional-chat: 发送任务轮询。pending/running → 发送中; done → 已发送; failed → 错误。"""
    store = _store(request)
    task = store.get_send_request(int(task_id)) if task_id.isdigit() else None
    if task is None:
        return HTMLResponse('<p class="muted">任务不存在或已过期</p>')
    if task["status"] in ("pending", "running"):
        return request.app.state.templates.TemplateResponse(
            request, "send_polling.html", {"task_id": task["id"]})
    if task["status"] == "failed":
        return HTMLResponse(f'<p class="error">发送失败: {task["error"] or "未知错误"}</p>')
    return HTMLResponse('<p class="ok">已发送</p>')
```

新建 `app/web/templates/send_polling.html`：

```html
<div id="send-task-{{ task_id }}"
     hx-get="/api/send/status/{{ task_id }}"
     hx-trigger="every 1s"
     hx-swap="outerHTML">
  <p class="muted">发送中…</p>
</div>
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/web/test_send_api.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/web/routes.py app/web/templates/send_polling.html tests/web/test_send_api.py
git commit -m "feat: /api/send + /api/send/status + follow_chat"
```

---

## Task 8: 前端 — 发送 UI + 确认 + 左栏预览 + 轮询提速

**Files:**
- Modify: `app/web/templates/workspace_chat.html`
- Modify: `app/web/templates/reply_result.html`
- Modify: `app/web/templates/workspace_customers.html`
- Modify: `app/web/static/js/app.js`
- Modify: `app/web/routes.py`（workspace / workspace_customers 路由传 preview）

- [ ] **Step 1: 左栏预览（workspace_customers.html + 路由）**

在 `app/web/routes.py` 的 `workspace` 与 `workspace_customers` 两个路由里，`activity = store.get_customers_recent_activity(...)` 之后各加一行：

```python
    previews = store.get_customers_chat_preview([r["id"] for r in rows])
```

并把各自 `TemplateResponse` 上下文字典里 `"activity": activity` 后加 `, "previews": previews`（两处）。

在 `app/web/templates/workspace_customers.html` 顶部循环里，`{% set unread = act.get('unread', 0) %}` 之后加：

```jinja
{% set pv = previews.get(c['id'], {}) if previews else {} %}
{% set live_unread = pv.get('unread', 0) %}
{% set preview = pv.get('preview') %}
```

把第 16 行的 `sub` 行改为优先显示预览：

```jinja
    <div class="ws-customer-sub muted">{{ preview or c['company'] or c['country'] or c['phone'] or '-' }}</div>
```

把第 24-25 行的时间/未读改为：

```jinja
    {% if last_ts %}<span class="ws-customer-time muted">{{ last_ts|int|ws_time }}</span>{% endif %}
    {% if live_unread %}<span class="ws-unread-badge">{{ live_unread }}</span>{% endif %}
```

（保持 `has-unread` class 判断不变，因为未读来源已含实时预览，可保留现状。）

- [ ] **Step 2: 聊天输入框（workspace_chat.html）**

在 `workspace_chat.html` 末尾（`<!-- 中栏加载完成后自动加载右栏 -->` 那段之前）加：

```html
{% if send_enabled %}
<div class="ws-send-bar">
  <textarea id="ws-send-text" class="input" rows="2" placeholder="输入要发送的消息…"></textarea>
  <button class="btn" id="ws-send-btn" type="button">发送</button>
  <div id="ws-send-status"></div>
</div>
{% endif %}
```

- [ ] **Step 3: 建议回复「直接发送」（reply_result.html）**

在 `reply_result.html` 的 `复制` 按钮之后、`重新生成` 按钮之前加：

```html
    {% if send_enabled %}
    <button class="btn" type="button"
            data-send-text="{{ reply }}" data-send-chat="{{ chat_id }}"
            data-send-customer="{{ customer_id }}">直接发送</button>
    {% endif %}
```

（注意：`_render_reply_result` 需要把 `send_enabled` 传入上下文；在 `app/web/routes.py` 的 `_render_reply_result` 里，`"session_id": session_id, "error": result.get("error")}` 前加 `"send_enabled": bool(RuntimeSettings(_store(request)).get_typed("send_enabled", False)),`。）

- [ ] **Step 4: JS 发送逻辑 + 轮询提速（app.js）**

在 `app.js` 末尾追加：

```javascript
// whatsapp-bidirectional-chat: 发送 (输入框 + 建议卡片, 均带确认)
(function () {
  function doSend(chatId, text, statusEl, onDone) {
    if (!text) return;
    if (!window.confirm("发送给该客户：\n\n" + text)) return;
    var box = statusEl || document.getElementById("ws-send-status");
    if (box) box.innerHTML = '<div class="muted">发送中…</div>';
    fetch("/api/send", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text: text }) })
      .then(function (r) { return r.text().then(function (t) { return { ok: r.ok, html: t }; }); })
      .then(function (res) {
        if (!res.ok) {
          if (box) box.innerHTML = '<p class="error">发送被拒绝</p>';
          if (onDone) onDone(false);
          return;
        }
        if (box) {
          box.innerHTML = res.html;
          if (window.htmx) htmx.process(box);  // 让轮询片段的 hx-* 生效
        }
        if (onDone) onDone(true);
      })
      .catch(function () { if (box) box.innerHTML = '<p class="error">网络错误</p>'; if (onDone) onDone(false); });
  }
  document.addEventListener("click", function (e) {
    var sendBtn = e.target.closest ? e.target.closest("#ws-send-btn") : null;
    if (sendBtn) {
      var ta = document.getElementById("ws-send-text");
      var chatId = document.getElementById("messages").getAttribute("data-chat-id");
      doSend(chatId, ta ? ta.value.trim() : "");
      if (ta) ta.value = "";
      return;
    }
    var direct = e.target.closest ? e.target.closest("[data-send-text]") : null;
    if (direct) {
      var old = direct.textContent;
      doSend(direct.getAttribute("data-send-chat"), direct.getAttribute("data-send-text"), null,
             function (ok) { direct.textContent = ok ? "已发送" : old; });
    }
  });
})();
```

把 `initWorkspacePoll` 里的 `var POLL_MS = 5000;` 改为：

```javascript
  var POLL_MS = 1000;
```

- [ ] **Step 5: 验证**

Run: `.venv\Scripts\python.exe -c "from app.web.app import create_app; create_app(); print('ok')"`
Expected: `ok`

手工验证（启动后）：`send_enabled=false` 时聊天框无输入框、建议卡无「直接发送」；设置页开 `send_enabled` 后刷新，输入框出现，发送弹确认，状态从「发送中…」→「已发送」。

- [ ] **Step 6: Commit**

```bash
git add app/web/templates/workspace_chat.html app/web/templates/reply_result.html app/web/templates/workspace_customers.html app/web/static/js/app.js app/web/routes.py
git commit -m "feat: 工作台发送 UI + 确认 + 左栏预览 + 轮询提速"
```

---

## Task 9: RISK.md 风险提示

**Files:**
- Modify: `docs/RISK.md`

- [ ] **Step 1: 追加发送风险**

在 `docs/RISK.md` 的「降低风险措施」列表里，`- **全程只读** (...) ` 一行之后加：

```markdown
- **主动发送风险更高**: 发送消息比只读抓取更易被判自动化, 封号风险显著上升 (本项目发送功能默认关闭, 需在设置页手动开启, 且每次发送前有确认)
```

- [ ] **Step 2: Commit**

```bash
git add docs/RISK.md
git commit -m "docs: 主动发送风险提示"
```

---

## 自检清单（对照 spec）

- D1 意图表 → Task 1/2/7 ✅
- D2 发送链路 → Task 2/4/6/7 ✅
- D3 send_enabled → Task 3/6/7/8 ✅
- D4 跟随会话 → Task 6 `_sync_follow` + Task 7 follow_chat 写入 ✅
- D5 会话列表监控 → Task 1/2/5/6/8 ✅
- D6 前端 → Task 8 ✅
- D7 群聊/错误处理 → 群聊同链路（open_chat 按名/手机号）+ `bump_send_request_attempts` 重试 ✅
- RISK.md → Task 9 ✅
- Non-Goals（媒体/内嵌二维码/多账号/SSE）→ 未包含任何任务 ✅

## 风险与需现场验证点

- **WhatsApp 选择器（输入框/搜索框/发送按钮/未读徽标）** 是最易碎点，已集中在 `sender.py` / `chat_list.py` 单点；首次真机跑通时若选择器失效，只改这两个文件。
- **`chat_list.py` 的未读徽标选择器**（`span[aria-label*="unread"]`）可能随语言/版本不同而变，需真机核对；解析失败静默降级为不显示未读，不影响主链路。
- 发送与跟随共用「搜索框点第一个结果」，若同名多会话会点到第一个，需人工确认匹配正确性。
