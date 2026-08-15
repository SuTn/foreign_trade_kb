# Proposal: 三栏工作台布局（workspace-layout）

## Why

当前客户管理是**分离的两页**：
- `customers.html`：客户列表（卡片网格）
- `chat.html`：客户详情（画像/分析/摘要/分层/会话链接，纵向堆叠）
- `chat_messages.html`：独立聊天页（无画像/AI 建议）

业务员要**来回跳转**才能看客户画像 + 聊天 + AI 建议，效率低。主流 CRM / WhatsApp Web 采用**三栏工作台**：左栏客户列表、中栏聊天窗口、右栏画像+AI 建议，一屏整合，符合业务员工作流。

## What Changes

- **三栏工作台**：新路由 `GET /workspace`，左栏客户列表（紧凑行），中栏聊天窗口（WhatsApp 风格气泡），右栏画像 + AI 建议。
- **渐进加载**：左栏初始渲染客户列表；点击客户 → htmx 加载中栏聊天 + 右栏画像/AI 建议，不整页刷新。
- **复用现有组件**：聊天气泡复用 `chat_messages.html` 样式；画像/摘要/分析复用 `profile_list.html`/`summary.html`/`analysis.html`。
- **保留旧页面**：`/customers` 列表页与 `/customers/{id}` 详情页保留，工作台为新增入口（导航"客户"指向工作台）。

## Capabilities

### New Capabilities
- `workspace`: 三栏工作台——客户列表 + 聊天窗口 + 画像/AI 建议一站式

### Modified Capabilities
- `web-app`: 新增工作台路由与模板

## Impact

- **app/web/routes.py**: 新增 `GET /workspace`、`GET /workspace/customer/{id}/chat`、`GET /workspace/customer/{id}/side`
- **app/web/templates/workspace.html**: 三栏骨架
- **app/web/templates/workspace_customers.html**: 左栏客户列表（紧凑行）
- **app/web/templates/workspace_chat.html**: 中栏聊天窗口
- **app/web/templates/workspace_side.html**: 右栏画像 + AI 建议
- **app/web/static/css/app.css**: 三栏布局样式
- **app/web/templates/base.html**: 导航"客户"指向 `/workspace`
- **测试**: 工作台路由、渐进加载、组件复用

## Non-goals

- 不删除旧页面（`/customers`、`/customers/{id}` 保留）
- 不做消息实时推送（沿用现有手动刷新/轮询）
- 不做多会话切换 UI（本期聚焦单客户工作台）