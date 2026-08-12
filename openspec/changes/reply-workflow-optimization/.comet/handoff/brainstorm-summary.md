# Brainstorm Summary

- Change: reply-workflow-optimization
- Date: 2026-08-12

## 确认的技术方案

### 1. 回复异步化（P0）
- **执行模型**：常驻串行 worker 线程（lifespan 启动，daemon），从 `reply_tasks` 表按序取 pending 任务顺序执行；消除并发 LLM 风暴与 DB 写竞争；后提交任务排队等待
- **API**：`POST /api/reply` 插入任务 → 返回 HTML 片段（含 task_id + 轮询区域）；`GET /api/reply/status/{task_id}` 返回任务状态（pending/running/done/failed）
- **响应格式**：HTML 片段 + HTMX 轮询（`hx-trigger="every 1s"`），done 后渲染完整结果；纯 HTMX，无需新 JS 轮询
- **worker 独立 SQLite 连接**（`check_same_thread=False` + WAL），按任务复用；**regenerate 同样走任务队列**（异步一致）
- **共享 CloudLLM 实例**：`app.state.llm` 单例，worker 复用（client 缓存跨任务生效）
- **测试迁移**：改写 4 个既有 reply 测试为「提交→轮询→断言结果」模式

### 2. CloudLLM 复用客户端（P0）
- `self._client` 懒加载缓存（首次 generate 按 provider 创建），`threading.Lock` 防竞态
- 配置实例化后固定，不处理动态变更

### 3. 多轮对话（P1）
- **会话粒度**：每 chat 一个会话（首次生成时创建 session_id，同一 chat 的生成/重生成共用）
- 表：`reply_sessions` + `reply_session_messages`（role: user/assistant）
- **历史上限**：最近 10 轮（user+assistant 各算一条）作为 LLM 上下文，超出截断
- 历史作为额外上下文拼接进 REPLY_SYSTEM；`regenerate` 沿用同一 session_id
- **会话写入时机**：仅主 generate 追加 user+assistant 一轮；regenerate 只读历史做上下文，不追加（替代候选不入历史，避免污染上下文）

### 4. 一键复制（P1）
- `reply_result.html` 加「复制」按钮，`navigator.clipboard.writeText()` + `execCommand` 回退
- 用户自行粘贴到 WhatsApp

## 关键取舍与风险

- **[既有 reply 测试破坏]** → 4 个测试改为轮询模式；async 任务在测试中需等待 worker 处理
- **[串行排队延迟]** → 后提交任务等待前序完成；本地单用户低频，排队窗口短
- **[worker 与请求并发写 SQLite]** → WAL + 独立连接 + busy_timeout；worker 串行写，竞争面收敛
- **[Web 进程重启丢任务]** → 遗留 pending/running 任务启动时清理为 failed
- **[clipboard 安全上下文]** → 127.0.0.1 视为 secure context；失败回退 execCommand
- **[regenerate 不追加会话]** → 上下文与最终选择可能脱节；权衡后接受（替代候选不污染历史）

## 测试策略

- 异步：任务状态流转（pending→running→done/failed）+ 状态查询 + 后台线程独立连接
- 会话：历史持久化 + 上下文传递（历史出现在 prompt）+ regenerate 复用会话
- CloudLLM：多次 generate 复用 client；并发首建仅一次
- 复制：前端走读确认按钮可用
- 回归：全量 `pytest -q` + `compileall -q app`

## Spec Patch

无（proposal/design/delta spec 已覆盖确认后的方案细节）
