# reply-workflow-optimization 任务清单

## 1. CloudLLM 复用客户端

- [x] 1.1 `CloudLLM` 增加 `_client` 懒加载缓存：首次 `generate()` 按 provider 创建 anthropic/openai client 并缓存
- [x] 1.2 用 `threading.Lock` 保证并发首次调用只建一次 client
- [x] 1.3 单测：多次 generate 复用同一 client 实例；并发首次调用仅创建一个 client

## 2. 回复异步化

- [ ] 2.1 `schema.sql` 新增 `reply_tasks` 表（id, customer_id, chat_id, message, style, status, result, error, created_at, updated_at）
- [ ] 2.2 `SqliteStore` 增加任务创建/更新/查询方法
- [ ] 2.3 `POST /api/reply` 改为插入任务返回 `task_id`；后台线程执行 RAG + LLM，独立 SQLite 连接
- [ ] 2.4 新增 `GET /api/reply/status/{task_id}` 返回任务状态（pending/running/done/failed + result/error）
- [ ] 2.5 `reply_result.html` 提交任务后 HTMX 轮询状态直至 done，失败展示错误
- [ ] 2.6 单测：异步任务状态流转（pending→running→done/failed）+ 状态查询接口

## 3. 多轮对话

- [ ] 3.1 `schema.sql` 新增 `reply_sessions`、`reply_session_messages` 表
- [ ] 3.2 `SqliteStore` 增加会话创建/消息追加/历史读取方法
- [ ] 3.3 `POST /api/reply` 支持 `session_id` 参数：无则新建会话，有则读取历史
- [ ] 3.4 `generate_reply` 将会话历史作为上下文传给 LLM（增强 REPLY_SYSTEM）
- [ ] 3.5 前端 `chat_messages.html` 维护 `session_id` 并随回复请求透传；`regenerate` 沿用同一会话
- [ ] 3.6 单测：会话历史持久化 + 上下文传递（历史出现在 prompt）

## 4. 一键复制

- [ ] 4.1 `reply_result.html` 加「复制」按钮
- [ ] 4.2 `app.js` 实现 `navigator.clipboard.writeText()` 复制逻辑（含 `execCommand` 回退）
- [ ] 4.3 前端测试/走读确认按钮在 done 结果上可用

## 5. 回归验证

- [ ] 5.1 全量 `pytest -q` 通过（新增 + 既有）
- [ ] 5.2 `compileall -q app` 通过
- [ ] 5.3 代码走读确认：异步任务线程安全、会话上下文正确、无每请求新建 client 残留
