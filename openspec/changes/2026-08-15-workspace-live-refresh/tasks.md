# Tasks: 工作台实时消息刷新

## 1. 后端路由

- [x] 1.1 `GET /workspace/customer/{id}/chat/poll?after_ts=`：增量拉取 `ts > after_ts` 的新消息，返回气泡片段（复用气泡渲染）
- [x] 1.2 `GET /workspace`：左栏客户列表补充最近消息时间 + 未读数，按最近活跃降序
- [x] 1.3 `GET /workspace/customer/{id}/chat`：点击客户时更新 `settings.ws_last_seen:{customer_id}`（视为已读）

## 2. 存储层

- [x] 2.1 新增 `get_customer_recent_activity(customer_id)`：返回最近消息时间 + 未读数（`from_me=0 AND ts > last_seen`）
- [x] 2.2 新增 `set_last_seen(customer_id, ts)` / `get_last_seen(customer_id)`（读写 `settings` 表）

## 3. 模板

- [x] 3.1 `workspace_chat.html`：气泡容器加轮询触发（`hx-trigger="every 5s"` + `hx-swap="beforeend"`），`after_ts` 由 JS 动态注入
- [x] 3.2 `workspace_customers.html`：客户行显示最近消息时间 + 未读徽标，未读高亮
- [x] 3.3 右栏各 Tab 加"刷新"按钮（复用现有 refresh-profile/summarize/analyze 端点）

## 4. 前端脚本与样式

- [x] 4.1 `app.js`：轮询逻辑（若 htmx every 不可靠则 JS setInterval + htmx.ajax）、滚动到底部、未读高亮
- [x] 4.2 `app.css`：未读徽标、活跃高亮、新消息动画

## 5. 测试与验证

- [x] 5.1 增量拉取路由测试（`/chat/poll?after_ts=` 返回新消息）
- [x] 5.2 左栏活跃排序 + 未读计数测试
- [x] 5.3 全量回归：`compileall` + `pytest` 通过
- [ ] 5.4 手动验证：采集器写入新消息 → 工作台 5s 内自动出现新气泡；左栏未读徽标更新