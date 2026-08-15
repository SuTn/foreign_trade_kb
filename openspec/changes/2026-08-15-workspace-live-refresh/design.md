# Design: 工作台实时消息刷新

## Context

三栏工作台（`/workspace`）已上线：左栏客户列表、中栏聊天窗口、右栏画像+AI建议。但中栏聊天**只在打开时加载一次**（`workspace_chat` 路由 `list_messages(chat_id, limit=50)`），采集器持续写库后页面不更新。左栏客户列表也是静态的（`workspace` 路由直接 `SELECT * FROM customers`），不显示活跃度/未读。

目标：中栏增量拉取新消息、左栏按活跃排序并显示未读，让业务员停留工作台即可看到新消息。

## Goals / Non-Goals

**Goals:**
- 中栏聊天定时增量拉取 `ts > 当前最新` 的新消息并追加气泡，不整页重载
- 左栏客户列表显示最近消息时间 + 未读徽标，按最近活跃排序
- 新消息到达时中栏自动滚动到底部、左栏对应客户高亮
- 右栏提供"刷新画像/摘要"按钮（不自动重算，避免频繁 LLM 调用）

**Non-Goals:**
- 不做 WebSocket/SSE（沿用轮询，与现有 htmx 架构一致）
- 不自动重算右栏摘要/AI 建议（LLM 成本）
- 不改采集器（采集器已实时写库，Web 端只读轮询即可）
- 不做消息已读回写（本期只展示未读，不持久化已读状态）

## Decisions

### D1: 增量拉取用 `list_messages_after(chat_id, after_ts)`
存储层已有 `list_messages_after(chat_id, after_ts, limit=200)`（时间正序，供增量摘要用）。中栏轮询复用此方法：前端记录当前最新消息 `ts`，轮询时传 `after_ts`，仅返回新消息。
- 新增路由 `GET /workspace/customer/{id}/chat/poll?after_ts=<int>`：返回新消息气泡片段（复用 `workspace_chat.html` 的气泡渲染逻辑，抽成 `_render_message_bubbles` 宏/片段）。
- 备选：整页重载聊天 —— 会丢失滚动位置、闪烁，弃用。

### D2. 轮询用 htmx `hx-trigger="every 5s"` + 增量追加
中栏气泡容器加 `hx-get="/workspace/customer/{id}/chat/poll?after_ts=..."`，`hx-trigger="every 5s"`，`hx-swap="beforeend"`。`after_ts` 通过 JS 在每次请求前从 DOM 读取最新消息 `data-ts` 更新到 `hx-vals`。
- 备选：纯 JS `setInterval` + fetch —— htmx 更贴合现有架构，弃用。
- 注意：htmx `every` 触发在 `hx-swap="beforeend"` 时不会重置计时器，需确认；若不可靠则回退 JS `setInterval` 手动 `htmx.ajax`。

### D2. 左栏活跃排序 + 未读徽标
- `GET /workspace` 查询每个客户关联会话的**最近消息时间**（`MAX(ts)`）与**未读数**（`from_me=0 AND ts > last_viewed`）。
- 未读定义：非我方消息且 `ts > 该客户最后查看时间`。最后查看时间持久化到 `settings` 表（key=`ws_last_seen:{customer_id}`），点击客户时更新为当前时间。
- 左栏按最近消息时间降序排列，未读客户显示徽标 + 高亮。
- 备选：新增 `read` 字段到 messages —— 需迁移且采集器写库时维护复杂，弃用；用 settings 记最后查看时间更轻量。

### D3. 右栏手动刷新
- 右栏画像/摘要/AI 建议 Tab 各加"刷新"按钮，复用现有 `refresh-profile`/`summarize`/`analyze` 端点。
- 不自动轮询右栏（避免频繁 LLM 调用）。

## Risks / Trade-offs

- **[轮询频率与负载]** → 5s 间隔对本地 SQLite 极轻（单条 `ts>` 查询）；若客户多可调大间隔。
- **[htmx every 触发可靠性]** → 若 `hx-trigger="every"` 与 `beforeend` 组合不可靠，退化为 JS `setInterval` + `htmx.ajax`（app.js 已有事件委托基础）。
- **[未读无持久化已读]** → 本期只展示未读，不写回已读；点击客户即视为已读（更新 last_seen），刷新后未读清零。

## Migration Plan

- 无 schema 变更（未读用 `settings` 表，已存在）。
- 新增路由 `GET /workspace/customer/{id}/chat/poll`。
- 回滚：移除轮询触发与左栏排序逻辑即可，不影响旧页面。

## Open Questions

- 轮询间隔：默认 5s 是否合适？（本地应用，5s 足够实时且负载低）
- 未读是否需要在点击客户后清零？（本期设计为点击即清零，符合直觉）