# Proposal: Web API 重构（api-refactor）

## Why

`app/web/routes.py` 存在三处可优化点（审计 W1/W2/W3）：
- **W2 参数解析重复**：`_cleanup_params` 与 `_reply_params` 各自实现 form-or-JSON 解析，逻辑重复，易漂移。
- **W3 路由重复**：`POST /api/reply` 与 `POST /api/reply/regenerate` 几乎相同，仅 mode/style 不同，可合并。
- **W1 状态端点返回 HTML**：`/api/reply/status` 与 `/api/summary/status` 返回渲染后的 HTML 片段，把 API 与前端渲染耦合。

## What Changes

- **W2**：提取统一 `_parse_body(request)` helper，`_cleanup_params`/`_reply_params` 复用，消除重复。
- **W3**：合并 `POST /api/reply` 与 `POST /api/reply/regenerate` 为单一路由，用 `mode` 参数区分（向后兼容：`/api/reply/regenerate` 保留为别名）。
- **W1**：评估状态端点 JSON 化。**结论：当前 htmx 架构下保持返回 HTML 片段**——htmx 的 `hx-get` + `every 1s` 轮询天然消费 HTML 片段，改 JSON 需引入 JS 渲染层，复杂度上升且无收益。记录为"设计权衡"，不改。

## Capabilities

### Modified Capabilities
- `web-app`: Web 路由层代码整洁——统一参数解析、合并重复路由

## Impact

- **app/web/routes.py**: 新增 `_parse_body` helper；合并 reply 路由；`_cleanup_params`/`_reply_params` 复用 helper
- **测试**: 参数解析、reply/regenerate 合并后行为不变（现有测试回归）
- 无第三方依赖变更；无 schema 变更

## Non-goals

- 不改状态端点返回格式（htmx 架构下 HTML 片段是正确设计）
- 不做三栏布局（独立 change）