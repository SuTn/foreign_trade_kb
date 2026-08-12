# reply-workflow-optimization 技术设计

> 需求与范围见 proposal.md，行为契约见 specs/。本文记录高层架构决策；深度技术设计见 comet-design 阶段的 Design Doc。

## Context

当前回复链路为同步阻塞：`POST /api/reply`（`app/web/routes.py:307`）直接构造 `RagPipeline` + `CloudLLM()` 同步执行 RAG + LLM 生成，期间 HTTP 请求挂起等待。`CloudLLM.generate()`（`app/llm/cloud_llm.py:30`）每次调用 import 并新建 provider client。回复请求无会话概念，多轮独立。回复结果仅 textarea。

## Goals / Non-Goals

**Goals:**
- `/api/reply` 提交后立即返回，后台线程执行，前端轮询
- CloudLLM client 懒加载复用（线程安全）
- 多轮会话：session_id + 会话历史作为 LLM 上下文
- 建议回复一键复制

**Non-Goals:**
- 不实现自动发送回复到 WhatsApp
- 不引入新外部依赖（无 Celery/Redis，任务队列用 SQLite + 后台线程）
- 不重构 RAG 管线本身
- 不做任务持久化跨进程恢复（重启后遗留任务清理即可）

## Decisions

### D1: 任务队列用 SQLite 表 + 常驻串行 worker，不引入消息队列
新增 `reply_tasks` 表承载任务状态，Web 进程内常驻串行 worker 线程（lifespan 启动，daemon）按序消费执行。理由：单用户本地应用，无跨进程/水平扩展需求；SQLite 已存在，无新依赖；串行执行消除并发 LLM 风暴与 DB 写竞争。任务在 Web 进程内执行，采集器进程不参与。regenerate 同样走任务队列（异步一致）。

### D2: 后台线程独立 SQLite 连接
后台线程执行 RAG 需读写 SQLite，不能复用请求线程的连接。参照 `_extract_profile_sync`（`app/collector/scanner.py:433`）模式，为任务执行线程建立独立连接（`check_same_thread=False` + WAL），任务完成后关闭。

### D3: CloudLLM client 懒加载 + 线程锁
`self._client` 首次 `generate()` 时按 provider 创建并缓存；`threading.Lock` 包裹避免竞态重复创建。配置（api_base/key/model）实例化后固定，不处理动态变更。Web 进程以 `app.state.llm` 单例共享实例，worker 与路由复用，client 缓存跨任务生效。

### D4: 多轮会话两张表 + 服务器自动解析
`reply_sessions`（会话主记录）与 `reply_session_messages`（轮次消息）。`POST /api/reply` 按 (customer_id, chat_id) find-or-create 会话（可选传 session_id 沿用既有）；有历史则作为额外 system 上下文拼接进 `REPLY_SYSTEM`，历史上限最近 10 轮。仅主 generate 追加 user+assistant 一轮；regenerate 只读历史不追加。前端 `chat_messages.html` 从响应中获取 session_id 随后续请求透传。

### D5: 前端轮询 + 一键复制
`reply_result.html` 提交任务后用 HTMX `hx-trigger="every 1s"` 轮询 `GET /api/reply/status/{task_id}`，done 时渲染完整结果（停止轮询）；复制按钮用 `navigator.clipboard.writeText()`，本地 HTTP 上下文下可用，`app.js` 用事件委托绑定（htmx 动态插入 DOM），失败回退 `execCommand('copy')`。

## Risks / Trade-offs

- **[后台线程与请求并发写 SQLite]** → WAL + 独立连接 + busy_timeout 缓解；任务执行频率低（用户触发）。
- **[任务执行期间 Web 进程重启丢任务]** → 遗留 pending/running 任务在下次启动清理为 failed；本地工具可接受。
- **[clipboard API 需安全上下文]** → 本地 127.0.0.1 视为 secure context，`navigator.clipboard` 可用；失败时回退 `execCommand('copy')`。

## Migration Plan

1. schema.sql 新增 `reply_tasks`/`reply_sessions`/`reply_session_messages` 表（CREATE IF NOT EXISTS 幂等）
2. 新功能走 feature 分支，build 阶段逐任务提交
3. 回归：`pytest -q` 全量；`compileall -q app`
4. 回滚：均为增量改动，无破坏性 schema 变更；移除新表不影响既有功能
