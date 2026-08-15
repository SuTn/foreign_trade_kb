# Proposal: 工作台实时消息刷新（workspace-live-refresh）

## Why

三栏工作台（`/workspace`）已上线，但中栏聊天窗口**只在打开时加载一次**。采集器（`app.collector`）持续同步 WhatsApp 新消息到 SQLite，业务员停留在工作台时**看不到新消息**，必须手动刷新页面或重新点客户才能看到。这是工作台工作流最直接的痛点。

同时左栏客户列表是静态的：不显示未读消息数、不按最近活跃排序，业务员无法快速判断"哪个客户有新消息"。

## What Changes

- **中栏聊天增量刷新**：聊天窗口定时轮询，仅拉取 `ts > 当前最新消息` 的新消息并追加到气泡列表，不整页重载。
- **左栏客户列表活跃化**：显示每个客户最近消息时间与未读消息数，按最近活跃排序，新消息到达时高亮。
- **右栏画像/摘要联动刷新**：新消息到达后，右栏画像/摘要/AI 建议可一键刷新（不自动重算，避免频繁 LLM 调用）。

## Capabilities

### New Capabilities
- `workspace-live`: 工作台实时消息刷新——中栏增量拉取 + 左栏活跃排序/未读

### Modified Capabilities
- `web-app`: 工作台聊天/客户列表支持实时刷新

## Impact

- **app/web/routes.py**: 新增 `GET /workspace/customer/{id}/chat/poll?after_ts=`（增量拉取新消息片段）；`GET /workspace` 与 `GET /workspace/customer/{id}/chat` 补充最近消息时间/未读数。
- **app/web/templates/workspace_chat.html**: 中栏气泡区加轮询触发（htmx `hx-trigger="every 5s"` 或 JS setInterval），增量追加新气泡。
- **app/web/templates/workspace_customers.html**: 左栏客户行显示最近消息时间 + 未读徽标，按活跃排序。
- **app/web/static/js/app.js**: 轮询逻辑（增量拉取 + 追加 + 滚动到底部 + 未读高亮）。
- **app/web/static/css/app.css**: 未读徽标、活跃高亮、新消息动画样式。
- **测试**: 增量拉取路由、未读计数、轮询片段渲染。

## Non-goals

- 不做 WebSocket/SSE 推送（沿用轮询，架构简单、与现有 htmx 一致）。
- 不自动重算右栏摘要/AI 建议（避免频繁 LLM 调用；提供手动刷新按钮）。
- 不改采集器（采集器已实时写库，Web 端只需轮询读取）。