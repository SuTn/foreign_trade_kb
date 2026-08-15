# Tasks: 三栏工作台布局

## 1. 路由

- [x] 1.1 `GET /workspace`：渲染三栏骨架 + 左栏客户列表 + 空中栏/右栏
- [x] 1.2 `GET /workspace/customer/{id}/chat`：渲染中栏聊天窗口（复用消息气泡）
- [x] 1.3 `GET /workspace/customer/{id}/side`：渲染右栏画像 + 摘要 + 分析

## 2. 模板

- [x] 2.1 `workspace.html`：三栏骨架（左/中/右）
- [x] 2.2 `workspace_customers.html`：左栏客户列表（紧凑行，含头像/名称/等级）
- [x] 2.3 `workspace_chat.html`：中栏聊天窗口（复用消息气泡 + 回复生成）
- [x] 2.4 `workspace_side.html`：右栏画像 + 摘要 + 分析

## 3. 样式

- [x] 3.1 `app.css` 新增 `.workspace` grid 布局、左栏紧凑行、中栏聊天区、右栏区块样式
- [x] 3.2 小屏折叠（`@media max-width: 900px`）

## 4. 导航

- [x] 4.1 `base.html` 导航"客户"指向 `/workspace`

## 5. 测试与验证

- [x] 5.1 工作台路由测试（`/workspace`、`/workspace/customer/{id}/chat`、`/workspace/customer/{id}/side`）
- [x] 5.2 全量回归：`compileall` + `pytest` 通过
- [x] 5.3 手动验证：点客户 → 中栏聊天 + 右栏画像/AI 建议加载；回复生成正常