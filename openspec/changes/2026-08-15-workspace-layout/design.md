# Design: 三栏工作台布局

## Context

当前客户管理分离为 `customers.html`（列表）、`chat.html`（详情，纵向堆叠画像/分析/摘要/分层/会话链接）、`chat_messages.html`（独立聊天页）。业务员需来回跳转。目标：三栏工作台（左客户列表 | 中聊天 | 右画像+AI建议），一屏整合。

## Goals / Non-Goals

**Goals:**
- 三栏工作台：左栏客户列表（紧凑行）、中栏聊天窗口（WhatsApp 风格气泡）、右栏画像 + AI 建议
- 渐进加载：点客户 → htmx 加载中栏聊天 + 右栏画像/AI 建议，不整页刷新
- 复用现有组件：聊天气泡、画像、摘要、分析
- 保留旧页面（`/customers`、`/customers/{id}`）

**Non-Goals:**
- 不删除旧页面
- 不做消息实时推送
- 不做多会话切换 UI

## Decisions

### D1: 三栏布局用 CSS Grid，全高
`.workspace` 用 `display:grid; grid-template-columns: 280px 1fr 320px; height: calc(100vh - nav高度)`，三栏各自独立滚动（`overflow-y:auto`）。中栏聊天区固定高度，气泡复用现有 `.chat-row`/`.chat-bubble` 样式。
- 备选：flexbox —— grid 更简洁，弃用。

### D2: 渐进加载，htmx 驱动
- `GET /workspace`：渲染左栏客户列表 + 空的中栏/右栏（占位提示"选择客户"）。
- 左栏客户行 `hx-get="/workspace/customer/{id}/chat"` 加载中栏，`hx-get="/workspace/customer/{id}/side"` 加载右栏。
- 中栏/右栏各自独立 `hx-target`，互不干扰。
- 备选：整页加载所有客户+聊天 —— 数据量大时慢，弃用。

### D3: 中栏聊天复用现有消息渲染
中栏聊天窗口复用 `chat_messages.html` 的消息气泡逻辑（`.chat-row`/`.chat-bubble`），但去掉独立页面的 `<html>/<head>/<nav>` 外壳，只渲染 `#messages` 部分。回复生成沿用 `POST /api/reply` + 轮询。
- 备选：新建独立聊天模板 —— 重复，弃用。

### D4: 右栏画像 + AI 建议
右栏分区块：客户画像（复用 `profile_list.html`）、对话摘要（复用 `summary.html`）、客户分析（复用 `analysis.html` + 生成按钮）。AI 建议即"客户分析"（兴趣点/活跃度/跟进建议）。
- 备选：新建独立 AI 建议组件 —— 现有 `analyze_customer_full` 已产出跟进建议，复用，弃用。

### D5: 导航"客户"指向工作台
`base.html` 导航"客户"链接从 `/customers` 改为 `/workspace`。旧 `/customers` 保留（可通过 URL 访问）。

## Risks / Trade-offs

- **[三栏在小屏拥挤]** → `@media (max-width: 900px)` 折叠为单栏（左栏隐藏，中栏+右栏堆叠）。
- **[渐进加载延迟]** → 中栏/右栏各自独立加载，互不阻塞；加载中显示占位。
- **[复用组件耦合]** → 中栏/右栏模板只复用渲染片段，不引入旧页面外壳。

## Migration Plan

- 纯前端新增，无 schema 变更，无数据迁移。
- 回滚：导航"客户"指回 `/customers`，删除工作台路由/模板即可。

## Open Questions

无 —— 三栏结构、渐进加载、组件复用、导航指向四项均已在 proposal 阶段确认。