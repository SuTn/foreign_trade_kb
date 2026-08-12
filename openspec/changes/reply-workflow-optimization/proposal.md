# reply-workflow-optimization 提案

## Why

回复链路存在四个体验/性能问题：`POST /api/reply` 同步阻塞（`app/web/routes.py:307`），LLM 生成期间用户长时间等待；`CloudLLM.generate()`（`app/llm/cloud_llm.py:30`）每次调用新建 anthropic/openai client，浪费连接资源；每次回复请求独立无上下文，无法围绕同一客户消息连续调整；回复结果仅 textarea 需手动选中复制，操作繁琐。这些问题共同拉低回复链路的使用体验。

## What Changes

- **回复异步化（P0）**：新增 `reply_tasks` 表；`POST /api/reply` 改为插入任务立即返回 `task_id`，后台线程执行 RAG + LLM；新增 `GET /api/reply/status/{task_id}` 返回任务状态；前端 `reply_result.html` 用 HTMX 轮询直到 done
- **CloudLLM 复用客户端（P0）**：anthropic/openai client 懒加载缓存为实例属性，`threading.Lock` 保证线程安全，消除每次 generate 重复建连
- **多轮对话（P1）**：新增 `reply_sessions` 与 `reply_session_messages` 表；`POST /api/reply` 支持 `session_id` 参数（无则新建）；生成回复时把会话历史作为上下文传给 LLM；前端聊天页维护当前 `session_id` 连续调整回复
- **一键复制（P1）**：`reply_result.html` 加「复制」按钮，用 `navigator.clipboard.writeText()` 复制建议回复，用户自行粘贴到 WhatsApp

## Capabilities

### New Capabilities

（无新增 capability，行为契约并入既有 capabilities）

### Modified Capabilities

- `reply-assist`: 回复生成改为异步任务（提交后返回 task_id、后台执行、状态查询）；多轮会话（session_id + 会话历史作为上下文）
- `web-app`: 回复结果前端轮询任务状态直至完成；建议回复一键复制

## Impact

- `app/storage/schema.sql`: `reply_tasks`、`reply_sessions`、`reply_session_messages` 表
- `app/storage/sqlite_store.py`: 新表操作（创建任务、查询状态、会话读写）
- `app/web/routes.py`: `/api/reply` 异步化、`/api/reply/status/{task_id}`、session_id 支持、后台线程执行
- `app/web/templates/reply_result.html`: 轮询状态展示 + 复制按钮
- `app/web/templates/chat_messages.html`: 携带 session_id 触发回复
- `app/web/static/js/app.js`: 复制按钮逻辑（clipboard）
- `app/llm/cloud_llm.py`: client 懒加载复用 + 线程锁
- `app/reply/generator.py`: 多轮上下文拼接（增强 REPLY_SYSTEM）
- `tests/`: 新增异步任务、状态查询、会话历史、复制按钮相关测试，既有测试保持通过
