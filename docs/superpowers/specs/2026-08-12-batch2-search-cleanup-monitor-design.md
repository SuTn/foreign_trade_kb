---
comet_change: batch2-search-cleanup-monitor
role: technical-design
canonical_spec: openspec
archived-with: 2026-08-12-batch2-search-cleanup-monitor
status: final
---

# batch2-search-cleanup-monitor Design Doc

> OpenSpec 产物是上游事实源（proposal/design/delta spec），本文件是深度技术设计。

## Context

Web 端三处增强（优化计划书第 6/7/8 项）：无全局搜索（客户/消息/知识/画像分散）；聊天数据无手动清理入口；采集器异常仅首页状态卡可见。均落在 Web 层，无新外部依赖。

## Goals / Non-Goals

**Goals:**
- 全局搜索页：客户/消息/知识库/画像四源检索分组展示
- 手动清理：按会话或按天数删除聊天消息 + 向量，保留知识库与画像
- 采集器异常全局横幅：前端定时轮询，离线红色提示

**Non-Goals:**
- 不引入全文检索引擎（复用 FTS5）
- 不做清理可恢复（删除不可恢复，前端确认）
- 不做自动清理
- 不改采集器进程行为

## Decisions

### D1: 全局搜索 — JSON 分组 + 页面渲染

`GET /api/search?q=` 返回 `{query, customers[], messages[], knowledge[], profiles[]}`：
- **客户**：`customers` 表 `WHERE display_name LIKE ? OR phone LIKE ? OR company LIKE ? OR country LIKE ?`（escape `%`/`_`）
- **消息**：`messages_fts`（复用 `search_fts`），FTS rowid join 回 `messages` 取 chat_id/body/ts（`messages_fts.content_rowid` 即 messages 的 rowid）
- **知识库**：`doc_chunks_fts`（复用 `search_fts`），join 回 `doc_chunks` 取 doc_id（参照 `knowledge_search` 的 `doc_lookup` 模式）
- **画像**：`profiles` 表 `WHERE field LIKE ? OR value LIKE ?`，附带 customer_id

`/search` 页：输入框 + htmx `hx-get="/api/search"` + `hx-trigger="keyup changed delay:300ms, search"` 分组渲染；空查询显示提示。JSON 返回利于后续扩展与测试。

### D2: 手动清理 — messages + FTS rebuild + 向量按 chat_id 删

`POST /api/cleanup`（body: `mode: chat|days`；chat 需 `chat_id`，days 需 `days`）：
- **定位待删行**：chat 模式 `WHERE chat_id=?`；days 模式 `WHERE ts < ?`（`now - days*86400`）
- **删除 messages** + FTS 同步：`DELETE FROM messages WHERE ...` 后 `INSERT INTO doc_chunks_fts...` 不适用——FTS 外部内容表用 `DELETE FROM messages_fts WHERE rowid IN (...)`（先记录受影响 rowid）或 `INSERT INTO messages_fts(messages_fts) VALUES('rebuild')`（参照 `delete_document`）。选定：**先收集受影响 rowid，删 messages 后 rebuild FTS**（`INSERT INTO messages_fts(messages_fts) VALUES('rebuild')` 一次性重建，简单可靠）
- **向量删除**：days 模式先 `SELECT DISTINCT chat_id FROM messages WHERE ts < ?` 收集 chat_id 集合，再逐个 `delete_message_vectors(chat_id)`；chat 模式直接删单个。`ChromaStore.delete_message_vectors(chat_id)` 用 `msg_col.delete(where={"chat_id": chat_id})`
- **保留**：profiles / documents / doc_chunks 完全不动
- 返回 `{deleted_rows, affected_chats}`；前端删除前 `confirm()`

### D3: 采集器异常横幅 — 复用 status 端点 + 自适应轮询

`base.html` 顶部加 `<div id="collector-banner" hidden>`；`app.js` `setInterval` 轮询 `/api/collector/status`：常规 15s，`alive=false` 时切换 5s 快查；`alive=false` 显示红色横幅「采集器异常」，恢复隐藏。首页已有 `hx-trigger="every 5s"` 的 status 卡，横幅与其并存（首页仍每 5s 刷新卡，横幅按 15s/5s 自适应）。不新增端点。

### D4: 接口与错误处理

- 搜索/清理均包 try/except：搜索失败返回空分组 + `error` 字段；清理失败返回 `{error}` 可读信息（不 500）
- 清理参数校验：chat 模式缺 chat_id、days 模式缺 days 或非正数 → 400 可读错误
- 搜索 LIKE 转义 `%`/`_` 防通配符误匹配

## Data Flow

```
搜索:
  /search 页输入 → hx-get /api/search?q=xx → routes 聚合四源 → JSON 分组 → 前端分组渲染

清理:
  管理页选择 chat/days → confirm() → POST /api/cleanup
    → 定位待删 messages → 删行 + FTS rebuild → 收集 chat_id → delete_message_vectors(chat_id)
    → 返回 {deleted_rows, affected_chats} → 页面提示

横幅:
  setInterval 15s → GET /api/collector/status → alive=false → 红色横幅 + 5s 快查
```

## Files

- `app/storage/sqlite_store.py`: `search_customers`/`search_profiles`/`delete_messages_by_chat`/`delete_messages_before`、FTS rebuild 辅助
- `app/storage/chroma_store.py`: `delete_message_vectors(chat_id)`
- `app/storage/interfaces.py`: `VectorStore.delete_message_vectors` 抽象
- `app/web/routes.py`: `GET /api/search`、`GET /search`、`POST /api/cleanup`、`/cleanup` 管理页
- `app/web/templates/search.html`（新）、`cleanup.html`（新）、`base.html`（横幅）
- `app/web/static/js/app.js`: 横幅轮询
- `tests/`: 搜索四源、清理保留断言、接口测试

## Risks / Trade-offs

- **[清理不可恢复]** → 前端确认 + 只清聊天不动知识库/画像；本地工具可接受
- **[FTS rebuild 开销]** → 手动低频，复用 delete_document 模式
- **[Chroma 双进程写锁]** → Web 进程清理，冲突面小；失败返回可读错误
- **[搜索 LIKE 性能]** → 本地单用户数据量小，无索引可接受；FTS 走索引
- **[横幅轮询开销]** → 15s/5s 自适应，避免全局高轮询

## Migration Plan

1. 无 schema 变更（复用既有表与 FTS）
2. 新功能走 feature 分支，build 阶段逐任务提交
3. 回归：`pytest -q` 全量；`compileall -q app`
4. 回滚：功能代码可回退；已清理数据不可恢复（操作层面）

## Open Questions

- FTS rebuild 与 delete_document 相同模式是否可直接复用辅助函数——build 阶段若 extract 出共享 helper 更佳，否则各自实现（保持 delete_document 不动）。
