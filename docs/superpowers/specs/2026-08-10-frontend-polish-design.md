---
comet_change: frontend-polish
role: technical-design
canonical_spec: openspec
archived-with: 2026-08-10-frontend-polish
status: final
---

# frontend-polish 技术设计

> 需求与范围见 `openspec/changes/frontend-polish/proposal.md`，行为契约见其 `specs/*/spec.md`。本文只讲 HOW。

## 1. 背景与约束

当前全站前端为裸 HTML（无样式、无静态资源、htmx 走 unpkg CDN），客户列表/仪表盘/详情/知识库/回复页均为朴素表格。架构为双进程：采集器子进程（`app.collector`）+ Web 主进程（`app.web`），共享 SQLite（WAL+FTS5）与 Chroma。硬约束：本地优先（不依赖外部网络）、纯静态无构建工具、采集器经 `ReadOnlyCDP` 白名单保持架构级只读。

## 2. 模块与数据流

```
采集器 (scan_all_chats 逐会话打开)
  └─ 头像抓取: header img src → page.evaluate fetch(GET) → base64 → data/avatars/<customer_id>.<ext>
       └─ 更新 customers.avatar_path
Web
  ├─ /avatars 静态挂载 → 头像文件
  ├─ /api/stats → 客户统计 + 知识库统计 + 采集状态 + 近期活跃会话
  ├─ /customers → 卡片网格 + data-search 画像字段预聚合
  └─ static/{css/app.css, js/app.js, js/htmx.min.js}
```

## 3. 详细设计

### 3.1 头像抓取（采集器，`app/collector/scanner.py`）

`scan_all_chats` 现有循环：`click(row) → sleep(settle) → capture_snapshot → merge_idb_dom → _upsert_one`。在 capture_snapshot 之后插入头像抓取：

```python
async def _capture_avatar(self, chat_id: str) -> None:
    """打开会话后抓取头像; 失败静默。"""
    if self.page is None: return
    customer = <查 customer_chat_map 得 customer_id>   # 无映射 → 返回
    src = await self.page.evaluate(<读 conversation-header 内首个 img src>)  # 多种选择器探测
    if not src: return
    data_url = await self.page.evaluate(<fetch(src) → blob → base64(dataUrl)>)
    ext = <从 dataUrl content-type 映射: image/jpeg→jpg, image/png→png, image/webp→webp>
    path = settings.avatars_dir / f"{customer_id}.{ext}"
    path.write_bytes(<dataUrl 解码后的 bytes>)
    self.store.conn.execute("UPDATE customers SET avatar_path=? WHERE id=?", (f"/avatars/{customer_id}.{ext}", customer_id))
    self.store.conn.commit()
```

关键点：
- **只读语义**：`page.evaluate` 内 `fetch` 是 GET 网络读，不改 WhatsApp 状态；与既有 `page.locator().click()` 同层，不引入 CDP 白名单改动。
- **页面上下文 fetch 的必要性**：头像 src 常为 `blob:https://web.whatsapp.com/...` 或签名 CDN URL，只能由页面自身 fetch（带 cookie/referer/blob 权限）。
- **选择器探测**：`header[data-testid="conversation-header"] img`、`img[data-testid]`、fallback `header img`；src 取首个可用的；版本漂移 → 返回空 → 静默跳过。
- **大小上限**：base64 解码后 >2MB 丢弃（防 CDP 回传放大）。
- **customer 归属**：chat_id → `customer_chat_map.customer_id`；未匹配则本次跳过，匹配后下次扫描补抓。
- **helper 独立成方法**便于单测（mock `self.page.evaluate`）。

### 3.2 数据层（`app/config.py`、`app/storage/schema.sql`、`sqlite_store.py`）

- `config.py`：`avatars_dir: Path = Path("data/avatars")`
- `schema.sql`：`customers` CREATE 定义追加 `avatar_path TEXT`
- `sqlite_store._init_schema`：executescript 后追加幂等迁移：
  ```python
  try:
      self.conn.execute("ALTER TABLE customers ADD COLUMN avatar_path TEXT")
      self.conn.commit()
  except sqlite3.OperationalError:
      pass  # 列已存在 (新库 schema.sql 已含)
  ```
- `data/` 已整体 gitignored，`avatars_dir` 无需额外规则。

### 3.3 Web 静态挂载与 `/api/stats`（`app/web/app.py`、`routes.py`）

- `create_app` 新增：
  ```python
  app.mount("/avatars", StaticFiles(directory=str((settings.avatars_dir).resolve())), name="avatars")
  ```
  （`avatars_dir.mkdir(parents=True, exist_ok=True)` 先建目录，避免 StaticFiles 对缺失目录报错。）
- `GET /api/stats`：
  - 客户：`COUNT(customers)`、`COUNT(DISTINCT profiles.customer_id)`、`COUNT(customer_chat_map)`
  - 知识库：`COUNT(documents)`、`COUNT(doc_chunks)`、`COUNT(wiki_pages)`
  - 采集：`read_status(settings.status_path)` + `is_alive(...)`
  - 近期活跃：`SELECT chat_id, MAX(ts) t FROM messages GROUP BY chat_id ORDER BY t DESC LIMIT 10`，join `chats`（display_name）+ `customer_chat_map`（customer_id）→ 返回 `[{chat_id, display_name, last_ts, customer_id}]`，页面链接 `/customers/{customer_id}/chat/{chat_id}`

### 3.4 客户列表搜索/筛选（`routes.py` + `customers.html`）

`customers()` 路由增强：一次查 `SELECT customer_id, field, value FROM profiles` 拼 `{customer_id: "field=value ..."}`，为每客户生成：
```html
<div class="card" data-search="alice 10086 acme usd company=acme country=usa ...">
  <img-or-span-avatar> 名称 电话 公司 国家
</div>
```
- 搜索框 `oninput`：按 `data-search` 子串匹配过滤
- 国家/公司下拉 `onchange`：前端从已渲染卡片提取 distinct 值；与搜索 AND 叠加
- 空结果态提示

### 3.5 静态资源与模板

- `static/css/app.css`：CSS 变量（蓝系主色 + 灰阶）；导航/卡片网格/头像（圆形）/首字母占位/聊天气泡（`from_me` 左右）/仪表盘卡片/表单按钮统一；响应式 `grid-template-columns: repeat(auto-fill, minmax(220px,1fr))`
- `static/js/app.js`：`initCustomerFilter()`（搜索+筛选叠加）、`avatarColor(name)`（hash→8 色）、占位首字母取 `display_name[0]`
- `static/js/htmx.min.js`：固定 htmx 2.x，从 unpkg 下载一次入仓
- 模板改造（保持 HTMX swap 结构）：见 `openspec/changes/frontend-polish/design.md` D6 表

## 4. 测试策略

| 层 | 用例 | 手法 |
|---|---|---|
| 迁移 | 旧 schema 库打开后含 `avatar_path` 列；重复打开幂等 | 临时库手建旧表 → `SqliteStore()` → `PRAGMA table_info` |
| 采集器 | `_capture_avatar` 成功落盘 + avatar_path 更新；无映射跳过；evaluate 抛异常静默 | 扩展 FakePage.evaluate 返回 dataUrl |
| stats | 造数据后 `/api/stats` 各统计值正确 | TestClient + tmp_data |
| Web | customers 卡片含头像/占位、`/api/stats` 页面渲染、搜索 data-search 存在 | TestClient + 临时库 |

## 5. 风险与回滚

- 头像选择器漂移 → 静默跳过 + 占位兜底；探测多选择器。
- base64 过大 → 丢弃重试。
- htmx 本地化 → 固定版本 + README 注明。
- 回滚：代码回退即可；新增列/文件无破坏性副作用。

## 6. 需用户确认的开放点

无 —— 关键决策均已在 brainstorming + 审计中确认。
