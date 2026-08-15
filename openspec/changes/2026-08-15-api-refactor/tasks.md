# Tasks: Web API 重构

## 1. 统一参数解析（W2）

- [x] 1.1 新增 `_parse_body(request)` helper（form-or-JSON 统一解析，JSON 失败回退空 dict）
- [x] 1.2 `_cleanup_params` 复用 `_parse_body`，只保留字段提取
- [x] 1.3 `_reply_params` 复用 `_parse_body`，只保留字段提取

## 2. 合并 reply/regenerate 路由（W3）

- [x] 2.1 提取 `_create_reply_task(request, mode)` 共享逻辑，`POST /api/reply` 用 mode=generate
- [x] 2.2 `POST /api/reply/regenerate` 保留为别名（强制 mode=regenerate，向后兼容）

## 3. W1 权衡记录

- [x] 3.1 状态端点保持返回 HTML 片段（htmx 架构下正确设计），在 design.md 记录权衡

## 4. 测试与验证

- [x] 4.1 全量回归：`compileall` + `pytest` 通过（现有 reply/cleanup 测试验证行为不变）
- [x] 4.2 手动验证：`/api/reply` 与 `/api/reply/regenerate` 行为一致；cleanup 参数解析正常