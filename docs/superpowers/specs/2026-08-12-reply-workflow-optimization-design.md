---
comet_change: reply-workflow-optimization
role: technical-design
canonical_spec: openspec
archived-with: 2026-08-12-reply-workflow-optimization
status: final
---

# reply-workflow-optimization Design Doc

> OpenSpec 产物是上游事实源（proposal/design/delta spec），本文件是深度技术设计。

## Context

回复链路四问题：`POST /api/reply` 同步阻塞（routes.py:307），LLM 生成期间用户等待；`CloudLLM.generate()` 每次调用新建 anthropic/openai client（cloud_llm.py:30）；回复无会话概念，多轮独立；回复结果仅 textarea 手动复制。

约束：本地单用户应用，无跨进程/水平扩展需求，不引入新外部依赖（无 Celery/Redis），任务在 Web 进程内执行。

## Goals / Non-Goals

**Goals:**
- 回复/重生成异步化：提交即返回 task_id，常驻串行 worker 后台执行，前端 HTMX 轮询
- CloudLLM client 懒加载复用（跨任务共享实例）
- 多轮会话：每 chat 一个会话，最近 10 轮历史作为 LLM 上下文
- 建议回复一键复制

**Non-Goals:**
- 不实现自动发送回复到 WhatsApp
- 不引入新外部依赖（任务队列 = SQLite 表 + 常驻 worker 线程）
- 不重构 RAG 管线本身
- 不做任务跨进程恢复（启动时遗留任务清理为 failed）
- 不实现并发任务（串行 worker，后提交排队）

## Decisions

### D1: 常驻串行 worker 线程消费 reply_tasks 表

lifespan 启动时创建 daemon worker 线程（`app.state.reply_worker`），循环从 `reply_tasks` 表按序取 pending 任务顺序执行，标记 running → 执行 → done/failed。任务执行 = RAG + LLM 生成。串行保证一次只有一个 LLM 调用，消除并发风暴与 DB 写竞争。

- worker 以 **app 引用** 持 app.state 访问共享资源（chroma_store/embedding/reranker/llm），不依赖 Request（审计 H）
- worker 独立 SQLite 连接（`check_same_thread=False` + WAL），按任务复用，不跨请求线程
- **备选**：每任务一线程——并发 LLM 风暴 + 写竞争。放弃。
- **备选**：线程池——维护生命周期复杂，单用户无并行需求。放弃。

### D2: 提交即返回 + HTMX 轮询

- `POST /api/reply` 与 `/api/reply/regenerate`：插入任务（status=pending）→ 返回 HTML 片段含 `task_id` 与轮询区域（`hx-trigger="every 1s"` hx-get `/api/reply/status/{task_id}`）
- `GET /api/reply/status/{task_id}`：pending/running → 渲染"处理中"片段（含继续轮询触发）；done → 复用 `_render_reply_result` 渲染完整结果（停止轮询）；failed → 渲染错误片段
- 前端纯 HTMX，无需新 JS 轮询逻辑

### D3: 共享 CloudLLM 实例 + client 懒加载复用

- lifespan 创建 `app.state.llm = CloudLLM()` 单例；worker 与路由复用（审计 A）
- `CloudLLM._client` 懒加载缓存（首次 generate 按 provider 创建），`threading.Lock` 防竞态
- 配置实例化后固定，不处理动态变更

### D4: 多轮会话

- 表：`reply_sessions`（id, customer_id, chat_id, created_at, updated_at）+ `reply_session_messages`（session_id, role, content, ts）
- **每 chat 一个会话**：POST 传 customer_id/chat_id，服务器 find-or-create（审计 J）；可选传 session_id 沿用既有
- **仅主 generate 追加** user+assistant 一轮；regenerate 只读历史做上下文，不追加（替代候选不污染历史）
- 历史上限：最近 10 轮（user+assistant 各算一条），作为额外 system 上下文拼接进 REPLY_SYSTEM
- 响应 HTML 携带 session_id 供后续请求透传

### D5: 一键复制

- `reply_result.html` 加「复制」按钮（`data-copy`）
- `app.js` 用**事件委托**绑定（htmx 动态插入 DOM，普通绑定失效，审计 F）：`navigator.clipboard.writeText()` + `execCommand('copy')` 回退
- 用户自行粘贴到 WhatsApp

### D6: 测试策略与既有测试迁移

- 4 个既有 reply 测试改写为「提交→轮询→断言结果」；**用 `with TestClient(create_app())`** 触发 lifespan 启动 worker（审计 I，方案①）
- 新增：任务状态流转（pending→running→done/failed）、会话历史持久化与上下文传递、CloudLLM client 复用（含并发首建）、遗留任务启动清理
- 回归：全量 `pytest -q` + `compileall -q app`

### D7: 遗留任务清理

lifespan 启动、worker 起跑前，把 pending/running 任务统一置 failed（进程重启残留，审计 L）。

## Data Flow

```
用户点击"生成回复"
  → POST /api/reply (customer_id, chat_id, message, session_id?)
      → find-or-create reply_session
      → INSERT reply_tasks(status=pending)
      → 返回 HTML 片段: [轮询区域 hx-get /api/reply/status/{task_id} every 1s]
  → worker 线程
      → 取 next pending task → UPDATE status=running
      → 读会话最近 10 轮历史 → RagPipeline.generate_reply(system 含历史)
      → 主 generate: INSERT reply_session_messages(user) + (assistant)
      → UPDATE status=done (result, sources) / failed (error)
  → GET /api/reply/status/{task_id}
      → done → reply_result.html (回复 + 来源 + 复制 + 重新生成) [停止轮询]
```

## Schema

```sql
CREATE TABLE IF NOT EXISTS reply_tasks(
  id TEXT PRIMARY KEY, customer_id TEXT, chat_id TEXT, message TEXT, style TEXT,
  session_id TEXT, status TEXT, result TEXT, error TEXT, created_at INTEGER, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS reply_sessions(
  id TEXT PRIMARY KEY, customer_id TEXT, chat_id TEXT, created_at INTEGER, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS reply_session_messages(
  id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, ts INTEGER);
```

## Risks / Trade-offs

- **[串行排队延迟]** → 后提交任务等待前序完成；本地单用户低频，排队窗口短。记录为已知限制。
- **[worker 与请求并发写 SQLite]** → WAL + 独立连接 + busy_timeout；worker 串行写收敛竞争面。
- **[Web 进程重启丢任务]** → 启动时遗留 pending/running 置 failed（D7）。
- **[regenerate 不追加会话]** → 上下文与最终选择可能脱节；权衡后接受（替代候选不污染历史）。
- **[clipboard 安全上下文]** → 127.0.0.1 视为 secure context；`execCommand` 回退。
- **[HTML 轮询无干净 JSON]** → status 返回 HTML 供 htmx；如需 JSON 调试可后续加 `Accept` 协商，本期不做。
- **[`_chroma_store` 懒创建守卫非线程安全]** → `if not getattr` 无锁；worker 首次访问可能重复创建 ChromaStore，幂等但浪费；本地单用户低风险。记录为已知限制。

## Migration Plan

1. schema.sql 新增三张表（CREATE IF NOT EXISTS 幂等）
2. 新功能走 feature 分支，build 阶段逐任务提交
3. 回归：`pytest -q` 全量；`compileall -q app`
4. 回滚：均为增量改动，无破坏性 schema 变更；移除新表不影响既有功能

## Open Questions

- worker 轮询 reply_tasks 的间隔（空循环 sleep 时长）——build 阶段定为 1s，与前端轮询一致，避免空转。
