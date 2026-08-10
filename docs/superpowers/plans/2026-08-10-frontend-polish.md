# frontend-polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

---
change: frontend-polish
design-doc: docs/superpowers/specs/2026-08-10-frontend-polish-design.md
base-ref: 9c429ad35cd8049f04400b43950784f6a886cba6
---

**Goal:** 全站前端改版 —— 本地静态样式 + 客户头像自动抓取/占位 + 卡片网格 + 搜索筛选 + 仪表盘 + 详情/知识库/回复美化。

**Architecture:** 采集器在 `scan_all_chats` 顺带抓 WhatsApp 头像落盘 `data/avatars/` 并更新 `customers.avatar_path`；Web 挂载 `/avatars` 静态目录 + 新增 `GET /api/stats` 仪表盘聚合；前端以手写 `static/css/app.css` + `static/js/app.js` + 本地 htmx 提供统一样式与实时过滤。

**Tech Stack:** FastAPI + Jinja2 + HTMX（本地化 2.x）、SQLite（WAL+FTS5）、Playwright page.evaluate、bge 本地嵌入（未涉及）。

## Global Constraints

- 本地优先：所有静态资源本地化，不引外部 CDN（含 htmx）
- 采集器只读：头像抓取用 `page.evaluate` 内 `fetch`（GET），不改 WhatsApp 状态
- 头像按 `customer_id` 归属，存 `data/avatars/<customer_id>.<ext>`，`avatar_path` 存相对 URL `/avatars/...`
- 旧库 `ALTER TABLE` 迁移必须幂等（try/except OperationalError）
- 头像抓取失败/无客户映射/超 2MB → 静默跳过
- 模板改造保持既有 HTMX swap 结构（partial 分页、行内编辑、closest swap）
- 输出语言：中文

---

### Task 1: 数据层 —— avatars_dir 配置 + avatar_path 迁移 + conftest

**Files:**
- Modify: `app/config.py`
- Modify: `app/storage/schema.sql`
- Modify: `app/storage/sqlite_store.py`
- Modify: `tests/conftest.py`
- Test: `tests/storage/test_sqlite_store.py`

**Interfaces:**
- Produces: `settings.avatars_dir: Path`（默认 `Path("data/avatars")`）；`customers.avatar_path` 列（`SqliteStore` 连接后自动迁移）

- [ ] **Step 1: 写失败测试**（旧库迁移幂等 + 新库含列）

```python
def test_old_schema_gets_avatar_path_column(tmp_data):
    """旧 schema 库 (无 avatar_path) 打开后自动迁移出该列, 且幂等。"""
    from app.storage.sqlite_store import SqliteStore
    from pathlib import Path
    import sqlite3
    store = SqliteStore()  # 新库已含列
    # 模拟旧库: 重新建一个不含 avatar_path 的库
    p = tmp_data / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE customers(id TEXT PRIMARY KEY, display_name TEXT, phone TEXT, company TEXT, country TEXT, created_at INTEGER)")
    c.commit(); c.close()
    store2 = SqliteStore(p)
    cols = [r[1] for r in store2.conn.execute("PRAGMA table_info(customers)").fetchall()]
    assert "avatar_path" in cols
    store2.conn.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_sqlite_store.py -q`
Expected: 新测试 FAIL（迁移未实现，列不存在）

- [ ] **Step 3: 实现迁移**

`app/config.py` 加：
```python
avatars_dir: Path = Path("data/avatars")
```
`app/storage/schema.sql` 的 `customers` CREATE 追加 `avatar_path TEXT`：
```sql
CREATE TABLE IF NOT EXISTS customers(
  id TEXT PRIMARY KEY, display_name TEXT, phone TEXT, company TEXT, country TEXT, created_at INTEGER,
  avatar_path TEXT);
```
`app/storage/sqlite_store.py` 顶部已有 `import sqlite3`；`_init_schema` 的 `executescript` 后追加：
```python
try:
    self.conn.execute("ALTER TABLE customers ADD COLUMN avatar_path TEXT")
    self.conn.commit()
except sqlite3.OperationalError:
    pass  # 列已存在 (新库 schema.sql 已含) — 幂等
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/storage/test_sqlite_store.py -q`
Expected: PASS

- [ ] **Step 5: conftest 补 avatars_dir 隔离**

`tests/conftest.py` `tmp_data` 追加：
```python
monkeypatch.setattr(config.settings, "avatars_dir", tmp_path / "avatars")
```

- [ ] **Step 6: 提交**

```bash
git add app/config.py app/storage/schema.sql app/storage/sqlite_store.py tests/conftest.py tests/storage/test_sqlite_store.py
git commit -m "feat: avatars_dir 配置 + customers.avatar_path 迁移 (旧库幂等 ALTER)"
```

---

### Task 2: 采集器头像抓取

**Files:**
- Modify: `app/collector/scanner.py`
- Test: `tests/collector/test_scanner.py`

**Interfaces:**
- Consumes: `settings.avatars_dir: Path`、`SqliteStore`（`conn`）、`self.page.evaluate(expr)` → 返回 `{"src": str}` 或 dataURL
- Produces: `Scanner._capture_avatar(chat_id: str) -> None`（内部读 header img src → fetch dataURL → 落盘 → UPDATE customers.avatar_path；失败静默）

- [ ] **Step 1: 写失败测试**（mock `page.evaluate` 返回 dataURL）

```python
def test_capture_avatar_writes_file_and_path(tmp_data, monkeypatch):
    """打开会话后抓取头像: 文件落盘 + customers.avatar_path 更新。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("me", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    png = b"\x89PNG\r\n\x1a\nfakedata"
    data_url = "data:image/png;base64," + __import__("base64").b64encode(png).decode()
    class FakePage:
        def __init__(self): self.calls = 0
        async def evaluate(self, expr):
            self.calls += 1
            if self.calls == 1:   # 第一次调用: 读 header img src
                return {"src": "blob:https://web.whatsapp.com/xyz"}
            return data_url       # 第二次: fetch → dataURL
    sc = Scanner(FakeCDP([{}]), store, FakeVector(), page=FakePage())
    import asyncio
    asyncio.run(sc._capture_avatar("c1"))
    path = settings.avatars_dir / "cust1.png"
    assert path.read_bytes() == png
    row = store.conn.execute("SELECT avatar_path FROM customers WHERE id='cust1'").fetchone()
    assert row["avatar_path"] == "/avatars/cust1.png"
```

```python
def test_capture_avatar_skips_when_no_customer(tmp_data):
    """无客户映射的会话不抓取, 不报错。"""
    from app.storage.sqlite_store import SqliteStore
    class FakePage:
        async def evaluate(self, expr): return {"src": "blob:x"}
    sc = Scanner(FakeCDP([{}]), SqliteStore(), FakeVector(), page=FakePage())
    import asyncio
    asyncio.run(sc._capture_avatar("no_map"))  # 不应抛异常
    av = settings.avatars_dir
    assert not av.exists() or not list(av.glob("*"))  # 未写任何头像文件
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/collector/test_scanner.py -k "avatar" -q`
Expected: FAIL（`_capture_avatar` 不存在）

- [ ] **Step 3: 实现 `_capture_avatar` + 在 `scan_all_chats` 接线**

`app/collector/scanner.py` 新增方法（放在 `_upsert_one` 之后）：
```python
async def _capture_avatar(self, chat_id: str) -> None:
    """打开会话后抓取头像 (只读 GET → base64 → 落盘 → 更新 avatar_path); 失败静默。"""
    if self.page is None or not chat_id:
        return
    try:
        row = self.store.conn.execute(
            "SELECT customer_id FROM customer_chat_map WHERE account_id=? AND chat_id=?",
            (self.account_id, chat_id)).fetchone()
        if not row:
            return  # 未匹配客户, 跳过
        customer_id = row["customer_id"]
        r = await self.page.evaluate(
            "(function(){var h=document.querySelector('header[data-testid=\"conversation-header\"]');"
            "var imgs=h?h.querySelectorAll('img'):[];"
            "for(var i=0;i<imgs.length;i++){var s=imgs[i].src;if(s&&s.indexOf('data:')!==0)return {src:s};}"
            "return {src:''};})()")
        src = (r or {}).get("src")
        if not src:
            return
        data_url = await self.page.evaluate(
            "fetch(%r).then(function(r){return r.blob()}).then(function(b){return new Promise(function(res){"
            "var f=new FileReader();f.onloadend=function(){res(f.result)};f.readAsDataURL(b);})})" % src)
        if not data_url or not isinstance(data_url, str) or not data_url.startswith("data:"):
            return
        mime = data_url.split(";", 1)[0].split(":", 1)[1]
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(mime)
        if not ext:
            return
        raw = __import__("base64").b64decode(data_url.split(",", 1)[1])
        if len(raw) > 2 * 1024 * 1024:
            return  # 超 2MB 丢弃
        path = settings.avatars_dir / f"{customer_id}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.store.conn.execute(
            "UPDATE customers SET avatar_path=? WHERE id=?",
            (f"/avatars/{customer_id}.{ext}", customer_id))
        self.store.conn.commit()
    except Exception:
        pass  # 静默跳过, 下次扫描重试
```
`scan_all_chats` 循环内（`self._merge_idb_dom` 之前或之后）调用：在 `dom_msgs` 抓取后追加：
```python
await self._capture_avatar(self._current_chat_id)
```
（注意 `_current_chat_id` 在 merge 后可能更新；若为 None 则 `_capture_avatar` 内部直接返回。）

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/collector/test_scanner.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/collector/scanner.py tests/collector/test_scanner.py
git commit -m "feat: scan_all 顺带抓取 WhatsApp 头像 (只读 fetch→落盘→avatar_path, 失败静默)"
```

---

### Task 3: `/api/stats` 仪表盘端点

**Files:**
- Modify: `app/web/routes.py`
- Test: `tests/web/test_routes.py`

**Interfaces:**
- Produces: `GET /api/stats` → `{"customers": {...}, "knowledge": {...}, "collector": {...}, "recent_chats": [{chat_id, display_name, last_ts, customer_id}]}`

- [ ] **Step 1: 写失败测试**

```python
def test_stats_endpoint(tmp_data):
    from fastapi.testclient import TestClient
    from app.web.app import create_app
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.executemany(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        [("c1","A","1",None,None,0,None),("c2","B","2",None,None,0,None)])
    store.conn.executemany(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)",
        [("m1","me","ch1",0,"x",1000,"chat","hi",1,0),
         ("m2","me","ch1",0,"x",2000,"chat","yo",1,0),
         ("m3","me","ch2",0,"y",1500,"chat","a",1,0)])
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",("ch1","me","ch1","Alice","single",0))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",("me","ch1","c1",0.9,0,0))
    store.conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)",("d1","a.pdf","pdf","docreader","done",0))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.get("/api/stats")
    assert r.status_code == 200
    j = r.json()
    assert j["customers"]["total"] == 2
    assert j["knowledge"]["documents"] == 1
    assert j["recent_chats"][0]["chat_id"] == "ch1"   # last_ts=2000 最新
    assert j["recent_chats"][0]["display_name"] == "Alice"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_routes.py -k stats -q`
Expected: FAIL（404）

- [ ] **Step 3: 实现端点**

`app/web/routes.py` 新增（放在 `collector_status` 之后）：
```python
@router.get("/api/stats")
async def stats():
    store = _store()
    customers = {
        "total": store.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "with_profile": store.conn.execute("SELECT COUNT(DISTINCT customer_id) FROM profiles").fetchone()[0],
        "linked_chats": store.conn.execute("SELECT COUNT(*) FROM customer_chat_map").fetchone()[0],
    }
    knowledge = {
        "documents": store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "chunks": store.conn.execute("SELECT COUNT(*) FROM doc_chunks").fetchone()[0],
        "wiki_pages": store.conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0],
    }
    recent = store.conn.execute(
        "SELECT chat_id, MAX(ts) AS last_ts FROM messages GROUP BY chat_id ORDER BY last_ts DESC LIMIT 10"
    ).fetchall()
    chat_names = {r["id"]: r["display_name"] for r in
                  store.conn.execute("SELECT id, display_name FROM chats").fetchall()}
    cust_map = {r["chat_id"]: r["customer_id"] for r in
                store.conn.execute("SELECT chat_id, customer_id FROM customer_chat_map").fetchall()}
    recent_chats = [{"chat_id": r["chat_id"], "display_name": chat_names.get(r["chat_id"]),
                     "last_ts": r["last_ts"], "customer_id": cust_map.get(r["chat_id"])} for r in recent]
    s = read_status(settings.status_path)
    return {"customers": customers, "knowledge": knowledge,
            "collector": {"alive": is_alive(settings.status_path), "status": s or {}},
            "recent_chats": recent_chats}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_routes.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/web/routes.py tests/web/test_routes.py
git commit -m "feat: 新增 GET /api/stats 仪表盘聚合端点"
```

---

### Task 4: Web 静态挂载 + htmx 本地化

**Files:**
- Modify: `app/web/app.py`
- Create: `app/web/static/js/htmx.min.js`（下载 htmx 2.x）

**Interfaces:**
- Produces: `/avatars/*` 静态目录；`/static/js/htmx.min.js` 本地引用

- [ ] **Step 1: 实现挂载**

`app/web/app.py`：
```python
from app.config import settings
from fastapi.staticfiles import StaticFiles
...
settings.avatars_dir.mkdir(parents=True, exist_ok=True)
app.mount("/avatars", StaticFiles(directory=str(settings.avatars_dir.resolve())), name="avatars")
```
（放在 `app.mount("/static", ...)` 之后。）

- [ ] **Step 2: 下载 htmx 2.x 到本地**

Run（PowerShell）:
```powershell
Invoke-WebRequest -Uri "https://unpkg.com/htmx.org@2/dist/htmx.min.js" -OutFile "app/web/static/js/htmx.min.js"
```
Verify: 文件非空且 >50KB；`(Get-Content app/web/static/js/htmx.min.js -Raw).Length` 输出 > 50000。

- [ ] **Step 3: 提交**

```bash
git add app/web/app.py app/web/static/js/htmx.min.js
git commit -m "feat: 挂载 /avatars 静态目录 + htmx 2.x 本地化"
```

---

### Task 5: 前端静态资源（CSS + JS）

**Files:**
- Create: `app/web/static/css/app.css`
- Create: `app/web/static/js/app.js`

**Interfaces:**
- Produces: `.avatar`（圆形 40px img / 占位 span）、`.card-grid`、`.chat-bubble`（`.mine`/`.theirs`）、`.stat-card`、`.filter-bar`；`initCustomerFilter()`、`avatarColor(name)`、`placeholderAvatar(name)` 函数

- [ ] **Step 1: 写 CSS**（浅色简洁、蓝系、圆角+轻阴影、卡片网格、头像、气泡、仪表盘、表单/按钮）

创建 `app/web/static/css/app.css`，内容覆盖：
- CSS 变量：`--primary:#2563eb; --bg:#f5f7fa; --card:#fff; --text:#1f2937; --muted:#6b7280; --border:#e5e7eb; --radius:10px; --shadow:0 1px 3px rgba(0,0,0,.08)`
- `body` 基础排版；`.nav`（顶栏，白底，链接 hover）
- `.card-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:16px }`
- `.customer-card`（白卡片，圆角+阴影，hover 提亮）
- `.avatar`（40px 圆形，object-fit:cover；`.avatar.fallback`（彩色圆 + 白字居中））
- `.chat-bubble`（max-width:70%; padding:10px 14px; border-radius:12px）`.mine`（右对齐，蓝底白字）`.theirs`（左对齐，白底黑字）
- `.stat-grid`/`.stat-card`（仪表盘数字卡）
- `.filter-bar`（搜索框 + 下拉）
- 表单 `.btn`、`.input` 统一样式；`table` 样式（知识库/回复页）
- 空态 `.empty` 提示

- [ ] **Step 2: 写 JS**

创建 `app/web/static/js/app.js`：
```javascript
function avatarColor(name) {
  var hash = 0;
  var s = String(name || "?");
  for (var i = 0; i < s.length; i++) { hash = (hash * 31 + s.charCodeAt(i)) | 0; }
  var palette = ["#2563eb","#0ea5e9","#10b981","#f59e0b","#ef4444","#8b5cf6","#ec4899","#14b8a6"];
  return palette[Math.abs(hash) % palette.length];
}
function placeholderAvatar(name) {
  var el = document.createElement("span");
  el.className = "avatar fallback";
  el.style.background = avatarColor(name);
  el.textContent = (String(name || "?")[0] || "?").toUpperCase();
  return el;
}
function initCustomerFilter() {
  var input = document.getElementById("search-input");
  var country = document.getElementById("filter-country");
  var company = document.getElementById("filter-company");
  if (!input) return;
  function apply() {
    var q = (input.value || "").trim().toLowerCase();
    var cc = country ? country.value : "";
    var cp = company ? company.value : "";
    document.querySelectorAll(".customer-card").forEach(function (card) {
      var hay = (card.getAttribute("data-search") || "").toLowerCase();
      var ok = (!q || hay.indexOf(q) >= 0)
        && (!cc || hay.indexOf("country=" + cc.toLowerCase()) >= 0)
        && (!cp || hay.indexOf("company=" + cp.toLowerCase()) >= 0);
      card.style.display = ok ? "" : "none";
    });
  }
  input.addEventListener("input", apply);
  if (country) country.addEventListener("change", apply);
  if (company) company.addEventListener("change", apply);
}
document.addEventListener("DOMContentLoaded", function () {
  initCustomerFilter();
  document.querySelectorAll(".avatar-holder[data-name]").forEach(function (h) {
    if (!h.querySelector("img")) { h.appendChild(placeholderAvatar(h.getAttribute("data-name"))); }
  });
});
```

- [ ] **Step 3: 提交**

```bash
git add app/web/static/css/app.css app/web/static/js/app.js
git commit -m "feat: 前端静态资源 (app.css 样式系统 + app.js 搜索/筛选/占位头像)"
```

---

### Task 6: base.html + customers.html（卡片网格 + 搜索筛选 + 头像）

**Files:**
- Modify: `app/web/templates/base.html`
- Modify: `app/web/templates/customers.html`
- Modify: `app/web/routes.py`（`customers()` 预聚合画像字段 + 头像）

**Interfaces:**
- Consumes: `initCustomerFilter()`（app.js）、`settings` 无新依赖
- Produces: `customers()` 上下文新增 `profiles_by_customer: dict[str, str]`

- [ ] **Step 1: base.html 引入本地资源 + 统一导航**

```html
<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><title>{% block title %}外贸客户知识库{% endblock %}</title>
<link rel="stylesheet" href="/static/css/app.css">
<script src="/static/js/htmx.min.js"></script>
<script src="/static/js/app.js"></script></head>
<body>
<nav class="nav">
  <span class="brand">外贸客户知识库</span>
  <a href="/">首页</a><a href="/customers">客户</a><a href="/knowledge">知识库</a>
</nav>
<main class="container">{% block content %}{% endblock %}</main>
</body>
</html>
```

- [ ] **Step 2: routes.customers() 预聚合画像字段**

`app/web/routes.py` `customers` 视图改为：
```python
@router.get("/customers")
async def customers(request: Request):
    store = _store()
    rows = store.conn.execute("SELECT * FROM customers").fetchall()
    profiles_by_customer: dict[str, str] = {}
    for r in store.conn.execute("SELECT customer_id, field, value FROM profiles").fetchall():
        s = profiles_by_customer.setdefault(r["customer_id"], "")
        profiles_by_customer[r["customer_id"]] = f"{s} {r['field']}={r['value']}"
    return request.app.state.templates.TemplateResponse(
        request, "customers.html",
        {"customers": rows, "profiles_by_customer": profiles_by_customer})
```

- [ ] **Step 3: customers.html 卡片网格 + 搜索/筛选**

`customers.html` 改为继承 base：
```html
{% extends "base.html" %}
{% block content %}
<h1>客户列表</h1>
<div class="filter-bar">
  <input id="search-input" class="input" type="text" placeholder="搜索名称/电话/公司/国家/画像字段...">
  <select id="filter-country" class="input">
    <option value="">全部国家</option>
    {% for c in customers|selectattr('country')|map(attribute='country')|unique if c %}
      <option value="{{ c }}">{{ c }}</option>
    {% endfor %}
  </select>
  <select id="filter-company" class="input">
    <option value="">全部公司</option>
    {% for c in customers|selectattr('company')|map(attribute='company')|unique if c %}
      <option value="{{ c }}">{{ c }}</option>
    {% endfor %}
  </select>
</div>
<div class="card-grid">
  {% for c in customers %}
  <div class="customer-card" data-search="{{ c['display_name'] or '' }} {{ c['phone'] or '' }} {{ c['company'] or '' }} {{ c['country'] or '' }} {{ profiles_by_customer.get(c['id'], '') }}">
    <a href="/customers/{{ c['id'] }}" class="card-link">
      <div class="avatar-holder" data-name="{{ c['display_name'] or c['id'] }}">
        {% if c['avatar_path'] %}<img class="avatar" src="{{ c['avatar_path'] }}" alt="头像">{% endif %}
      </div>
      <div class="card-body">
        <strong>{{ c['display_name'] or c['id'] }}</strong>
        <div class="muted">{{ c['phone'] or '-' }}</div>
        <div class="muted">{{ c['company'] or '-' }}</div>
        <div class="muted">{{ c['country'] or '-' }}</div>
      </div>
    </a>
  </div>
  {% else %}
  <p class="empty">暂无客户</p>
  {% endfor %}
</div>
{% endblock %}
```
（`data-search` 小写由 JS 处理；头像无 `avatar_path` 时 `avatar-holder` 由 app.js 注入首字母占位。）

- [ ] **Step 4: 测试**（渲染含 avatar 与 data-search）

`tests/web/test_routes.py` 新增：
```python
def test_customers_page_has_search_data(tmp_data):
    from fastapi.testclient import TestClient
    from app.web.app import create_app
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",("c1","Alice","10086","ACME","USA",0,"/avatars/c1.png"))
    store.conn.execute("INSERT INTO profiles VALUES(?,?,?,?,?)",("c1","country","USA","auto",0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/customers").text
    assert "data-search" in html and "ACME" in html
    assert 'src="/avatars/c1.png"' in html
```

- [ ] **Step 5: 运行测试**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_routes.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add app/web/templates/base.html app/web/templates/customers.html app/web/routes.py tests/web/test_routes.py
git commit -m "feat: 客户卡片网格 + 头像/占位 + 实时搜索筛选 (含画像字段)"
```

---

### Task 7: chat.html + chat_messages.html（大头像 + 画像卡片 + 聊天气泡）

**Files:**
- Modify: `app/web/templates/chat.html`
- Modify: `app/web/templates/chat_messages.html`

**Interfaces:**
- Consumes: `customer.avatar_path`、`profile`（ProfileField 列表）
- Produces: 保持 `#profile`、`#messages`、`#analysis` 的 HTMX swap 目标不变

- [ ] **Step 1: chat.html 继承 base + 大头像 + 画像卡片**

```html
{% extends "base.html" %}
{% block title %}客户 {{ customer['display_name'] or customer_id }}{% endblock %}
{% block content %}
<h1>
  <span class="avatar-holder avatar-lg" data-name="{{ customer['display_name'] or customer_id }}">
    {% if customer and customer['avatar_path'] %}<img class="avatar avatar-lg" src="{{ customer['avatar_path'] }}" alt="头像">{% endif %}
  </span>
  {{ customer['display_name'] or customer_id if customer else customer_id }}
</h1>
{% if customer %}
<p class="muted">电话: {{ customer['phone'] or '-' }} · 公司: {{ customer['company'] or '-' }} · 国家: {{ customer['country'] or '-' }}</p>
{% endif %}
<div class="two-col">
  <section>
    <h2>画像</h2>
    <div id="profile">{% include "profile_list.html" %}</div>
    <button class="btn" hx-post="/customers/{{ customer_id }}/refresh-profile" hx-target="#profile" hx-swap="innerHTML">重新抽取画像</button>
  </section>
  <section>
    <h2>客户分析</h2>
    <div id="analysis" hx-post="/customers/{{ customer_id }}/analyze" hx-trigger="click from:#analyze-btn" hx-swap="innerHTML">
      <p>点击下方按钮生成分析</p>
    </div>
    <button class="btn" id="analyze-btn">生成客户分析</button>
  </section>
</div>
<h2>关联会话</h2>
<ul>{% for c in chats %}<li>{{ c['chat_id'] }} (置信度 {{ c['match_confidence'] }}) <a class="btn btn-sm" href="/customers/{{ customer_id }}/chat/{{ c['chat_id'] }}">打开聊天</a></li>{% else %}<li>暂无关联会话</li>{% endfor %}</ul>
{% endblock %}
```

- [ ] **Step 2: chat_messages.html 气泡化（保持 partial swap，独立模板不继承 base）**

`chat_messages.html`：Jinja2 不允许条件化 `extends`，故保持独立完整页结构（引用本地 static），partial 部分仍为 `#messages`：
```html
{% if not partial %}
<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><title>会话 {{ chat_id }}</title>
<link rel="stylesheet" href="/static/css/app.css">
<script src="/static/js/htmx.min.js"></script>
<script src="/static/js/app.js"></script></head>
<body>
<nav class="nav"><span class="brand">外贸客户知识库</span><a href="/">首页</a><a href="/customers">客户</a><a href="/knowledge">知识库</a></nav>
<main class="container">
<p><a href="/customers/{{ customer_id }}">← 返回客户</a></p>
{% endif %}
<div id="messages">
  {% for m in messages %}
  <div class="chat-row {{ 'mine' if m.from_me else 'theirs' }}">
    <div class="chat-bubble {{ 'mine' if m.from_me else 'theirs' }}">
      <div class="chat-meta">{{ '我' if m.from_me else '客户' }} · {{ m.ts }}</div>
      <div class="chat-text">{{ m.body or '(无正文)' }}</div>
      <div id="reply-{{ m.id }}">
        <button class="btn btn-sm" hx-post="/api/reply"
                hx-vals='{"customer_id": "{{ customer_id }}", "chat_id": "{{ chat_id }}", "message": "{{ (m.body or '')|replace('"', '&quot;') }}" }'
                hx-target="#reply-{{ m.id }}" hx-swap="innerHTML">生成回复</button>
      </div>
    </div>
  </div>
  {% else %}
  <p class="empty">该会话暂无消息</p>
  {% endfor %}
  {% if older_ts %}
  <button class="btn" hx-get="/customers/{{ customer_id }}/chat/{{ chat_id }}?before_ts={{ older_ts }}&partial=1"
          hx-target="#messages" hx-swap="outerHTML">加载更早消息</button>
  {% endif %}
</div>
{% if not partial %}
</main>
</body>
</html>
{% endif %}
```

- [ ] **Step 3: 提交**

```bash
git add app/web/templates/chat.html app/web/templates/chat_messages.html
git commit -m "feat: 客户详情大头像+画像卡片 + 聊天气泡左右分列"
```

---

### Task 8: home.html 仪表盘

**Files:**
- Modify: `app/web/templates/home.html`

**Interfaces:**
- Consumes: `GET /api/stats` 响应 `{customers, knowledge, collector, recent_chats}`；`/api/collector/status` 5s 轮询

- [ ] **Step 1: 实现仪表盘模板**

`home.html` 继承 base：
```html
{% extends "base.html" %}
{% block content %}
<h1>外贸客户知识库</h1>
<div class="stat-grid">
  <div class="stat-card"><div class="stat-num">{{ customers.total }}</div><div class="stat-label">客户总数</div></div>
  <div class="stat-card"><div class="stat-num">{{ customers.with_profile }}</div><div class="stat-label">有画像客户</div></div>
  <div class="stat-card"><div class="stat-num">{{ knowledge.documents }}</div><div class="stat-label">知识文档</div></div>
  <div class="stat-card"><div class="stat-num">{{ knowledge.wiki_pages }}</div><div class="stat-label">Wiki 页面</div></div>
</div>
<div class="two-col">
  <section>
    <h2>采集器状态</h2>
    <div id="collector-status" hx-get="/api/collector/status" hx-trigger="load, every 5s" hx-swap="innerHTML">
      {% if status %}<p>连接: <strong>{{ '在线' if alive else '离线' }}</strong> · 状态: {{ status.state or '未知' }}</p>{% else %}<p>采集器未启动 (无 status.json)</p>{% endif %}
    </div>
  </section>
  <section>
    <h2>近期活跃会话</h2>
    <ul class="recent-list">
      {% for rc in recent_chats %}
      <li>
        {% if rc.customer_id %}<a href="/customers/{{ rc.customer_id }}/chat/{{ rc.chat_id }}">{% endif %}
        {{ rc.display_name or rc.chat_id }} · {{ rc.last_ts }}
        {% if rc.customer_id %}</a>{% endif %}
      </li>
      {% else %}
      <li class="empty">暂无消息</li>
      {% endfor %}
    </ul>
  </section>
</div>
{% endblock %}
```

- [ ] **Step 2: home 路由改用 /api/stats 数据**

`app/web/routes.py` `index` 视图改为：
```python
@router.get("/")
async def index(request: Request):
    store = _store()
    customers = {"total": store.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
                 "with_profile": store.conn.execute("SELECT COUNT(DISTINCT customer_id) FROM profiles").fetchone()[0]}
    knowledge = {"documents": store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                 "wiki_pages": store.conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0]}
    recent = store.conn.execute(
        "SELECT chat_id, MAX(ts) AS last_ts FROM messages GROUP BY chat_id ORDER BY last_ts DESC LIMIT 10").fetchall()
    chat_names = {r["id"]: r["display_name"] for r in store.conn.execute("SELECT id, display_name FROM chats").fetchall()}
    cust_map = {r["chat_id"]: r["customer_id"] for r in store.conn.execute("SELECT chat_id, customer_id FROM customer_chat_map").fetchall()}
    recent_chats = [{"chat_id": r["chat_id"], "display_name": chat_names.get(r["chat_id"]),
                     "last_ts": r["last_ts"], "customer_id": cust_map.get(r["chat_id"])} for r in recent]
    s = read_status(settings.status_path)
    return request.app.state.templates.TemplateResponse(
        request, "home.html",
        {"customers": customers, "knowledge": knowledge, "recent_chats": recent_chats,
         "status": s or {}, "alive": is_alive(settings.status_path)})
```

- [ ] **Step 3: 测试**（首页渲染含统计）

`tests/web/test_routes.py` 新增：
```python
def test_home_shows_stats(tmp_data):
    from fastapi.testclient import TestClient
    from app.web.app import create_app
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",("c1","Alice","1",None,None,0,None))
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)",("m1","me","ch1",0,"x",1000,"chat","hi",1,0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)",("ch1","me","ch1","Alice","single",0))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",("me","ch1","c1",0.9,0,0))
    store.conn.commit()
    client = TestClient(create_app())
    html = client.get("/").text
    assert "客户总数" in html and "近期活跃会话" in html and "Alice" in html
```

- [ ] **Step 4: 运行测试**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_routes.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/web/templates/home.html app/web/routes.py tests/web/test_routes.py
git commit -m "feat: 首页仪表盘 (统计卡 + 近期活跃会话)"
```

---

### Task 9: 知识库/回复页统一美化

**Files:**
- Modify: `app/web/templates/knowledge.html`
- Modify: `app/web/templates/knowledge_docs.html`
- Modify: `app/web/templates/knowledge_search.html`
- Modify: `app/web/templates/reply_result.html`

**Interfaces:**
- Consumes: 现有 HTMX 端点不变（`/api/knowledge/*`、`/api/reply`）

- [ ] **Step 1: knowledge.html 继承 base**

`knowledge.html` 改为：
```html
{% extends "base.html" %}
{% block content %}
<h1>知识库</h1>
<form class="toolbar" hx-post="/api/knowledge/upload" hx-encoding="multipart/form-data" method="post" enctype="multipart/form-data"
      hx-target="#doc-list" hx-swap="outerHTML" hx-on::after-request="this.reset()">
  <input class="input" type="file" name="file" required>
  <input class="input" type="text" name="filename" placeholder="文件名(含扩展名)" required>
  <button class="btn" type="submit">上传</button>
  <button class="btn" type="button" hx-post="/api/knowledge/export-vault">导出 Vault</button>
</form>
<h2>文档列表</h2>
{% include "knowledge_docs.html" %}
<h2>检索测试</h2>
<form class="toolbar" hx-post="/api/knowledge/search" hx-target="#search-results" hx-swap="innerHTML">
  <input class="input" type="text" name="message" placeholder="输入查询语句" required>
  <button class="btn" type="submit">检索</button>
</form>
<div id="search-results"></div>
{% endblock %}
```

- [ ] **Step 2: knowledge_docs.html 表格样式**

`knowledge_docs.html` 包一层 `.table-wrap` 并保留原 `hx-delete` swap：
```html
<div id="doc-list">
  <table class="data-table">
    <tr><th>文件名</th><th>格式</th><th>状态</th><th>chunks</th><th>wiki 页</th><th>操作</th></tr>
    {% for d in docs %}
    <tr>
      <td>{{ d['filename'] }}</td><td>{{ d['format'] }}</td><td>{{ d['status'] }}</td>
      <td>{{ d['chunk_count'] }}</td><td>{{ d['wiki_count'] }}</td>
      <td><button class="btn btn-sm btn-danger" hx-delete="/api/knowledge/{{ d['id'] }}"
                  hx-target="closest tr" hx-swap="outerHTML"
                  hx-confirm="确认删除文档 {{ d['filename'] }}?">删除</button></td>
    </tr>
    {% else %}
    <tr><td colspan="6" class="empty">暂无文档</td></tr>
    {% endfor %}
  </table>
</div>
```

- [ ] **Step 3: knowledge_search.html + reply_result.html 卡片化**

`knowledge_search.html`（保持 `#search-results` HTMX 目标不变）改为：
```html
<div>
  <p><strong>查询:</strong> {{ query }} — 命中 {{ results|length }} 条</p>
  {% for r in results %}
  <div class="result-card">
    <div class="muted">[{{ r['source'] }}] doc={{ r['doc_id'] or '-' }}</div>
    <p>{{ r['text'][:200] }}</p>
  </div>
  {% else %}
  <p class="empty">无匹配结果</p>
  {% endfor %}
</div>
```

`reply_result.html`（保持 `closest div` swap 与 `hx-vals` 结构不变）改为：
```html
<div class="result-card">
  <p><strong>建议回复</strong> <span class="tag">风格: {{ style }}</span></p>
  <textarea class="input" rows="4" style="width:100%">{{ reply }}</textarea>
  <button class="btn" hx-post="/api/reply/regenerate"
          hx-vals='{"customer_id": "{{ customer_id }}", "chat_id": "{{ chat_id }}", "message": "{{ message|default('', true) }}", "style": "{{ style }}" }'
          hx-target="closest div" hx-swap="innerHTML">重新生成</button>
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
</div>
```

- [ ] **Step 4: 提交**

```bash
git add app/web/templates/knowledge.html app/web/templates/knowledge_docs.html app/web/templates/knowledge_search.html app/web/templates/reply_result.html
git commit -m "feat: 知识库与回复页统一样式"
```

---

### Task 10: README + 收尾验证

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README 使用流程补头像/仪表盘**

`README.md` 「客户与回复」节补一段：
> - 客户列表以卡片网格展示，含 WhatsApp 自动抓取的头像（无头像时为首字母占位），支持按名称/电话/公司/国家/画像字段实时搜索与筛选。
> - 首页为仪表盘，概览采集器状态、客户与知识库统计、近期活跃会话。

- [ ] **Step 2: 全量测试**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全部 PASS（原 94 + 新增，预计 100+）

- [ ] **Step 3: 构建检查**

Run: `.venv/Scripts/python.exe -m compileall -q app tests`
Expected: 无输出（成功）

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: README 补前端改版 (头像/仪表盘) 说明"
```

---

### Task 11: tasks.md 勾选 + 退出守卫

**Files:**
- Modify: `openspec/changes/frontend-polish/tasks.md`

- [ ] **Step 1: 勾选全部任务**

将 `openspec/changes/frontend-polish/tasks.md` 所有 `- [ ]` 改为 `- [x]`。

- [ ] **Step 2: 提交**

```bash
git add openspec/changes/frontend-polish/tasks.md
git commit -m "chore: frontend-polish tasks 全部完成"
```

- [ ] **Step 3: 运行 build 守卫**

Run（git-bash）:
```bash
"$COMET_BASH" "$COMET_GUARD" frontend-polish build --apply
```
Expected: ALL CHECKS PASSED → phase=verify
