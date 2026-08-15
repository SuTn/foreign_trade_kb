# Design: Web API 重构

## Context

`app/web/routes.py` 的 Web 层存在三处可优化点（审计 W1/W2/W3）：
- **W2**：`_cleanup_params`（routes.py:263）与 `_reply_params`（routes.py:563）各自实现 form-or-JSON 解析，逻辑重复。
- **W3**：`POST /api/reply`（routes.py:605）与 `POST /api/reply/regenerate`（routes.py:619）几乎相同，仅 mode/style 不同。
- **W1**：`/api/reply/status`（routes.py:635）与 `/api/summary/status`（routes.py:405）返回渲染后的 HTML 片段。

## Goals / Non-Goals

**Goals:**
- 统一 form-or-JSON 参数解析，消除 `_cleanup_params`/`_reply_params` 重复
- 合并 reply/regenerate 路由，消除重复
- 保持所有现有行为与测试不变（纯重构）

**Non-Goals:**
- 不改状态端点返回格式（htmx 架构下 HTML 片段是正确设计，见 D1）
- 不做三栏布局（独立 change）

## Decisions

### D1: W1 状态端点保持返回 HTML 片段（不改）
`/api/reply/status` 与 `/api/summary/status` 当前返回渲染后的 HTML 片段，前端用 htmx `hx-get` + `every 1s` 轮询消费。htmx 的设计哲学就是服务端渲染 HTML 片段、客户端无 JS 渲染逻辑。改 JSON 需引入 JS fetch + 手动渲染层，复杂度上升且无收益。**结论：保持现状，记录为设计权衡。**
- 备选：改 JSON + JS 渲染 —— 破坏 htmx 简洁性，弃用。

### D2: 提取 `_parse_body(request)` helper
新增 `async def _parse_body(request) -> dict`：按 content-type 解析 JSON body 或表单，统一返回 dict（JSON 解析失败/非 dict 时回退空 dict）。`_cleanup_params` 与 `_reply_params` 复用，各自只保留字段提取逻辑。
- 备选：保留两处独立实现 —— 重复，弃用。

### D3: 合并 reply/regenerate 路由
`POST /api/reply` 接受可选 `mode`（默认 `generate`），`mode=regenerate` 时用 `NEXT_STYLE` 轮换 style。`POST /api/reply/regenerate` 保留为别名（转发到同一 handler，mode=regenerate），向后兼容现有前端调用。
- 备选：保留两个独立路由 —— 重复，弃用。

## Risks / Trade-offs

- **[合并路由破坏现有调用]** → `/api/reply/regenerate` 保留为别名，现有前端与测试不受影响。
- **[htmx 轮询依赖 HTML 片段]** → W1 不改，避免破坏轮询。

## Migration Plan

- 纯后端重构，无 schema 变更，无数据迁移。
- 回滚：恢复独立路由与参数解析即可。

## Open Questions

无 —— W1 权衡、W2 统一、W3 合并三项均已在 proposal 阶段确认。