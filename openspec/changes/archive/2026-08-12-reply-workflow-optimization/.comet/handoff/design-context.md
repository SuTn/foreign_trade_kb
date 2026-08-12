# Comet Design Handoff

- Change: reply-workflow-optimization
- Phase: design
- Mode: compact
- Context hash: 70a7874c62f1a987259c0c2080eab4a668055c7561a7d65d3fc80ba8db4e8f49

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/reply-workflow-optimization/proposal.md

- Source: openspec/changes/reply-workflow-optimization/proposal.md
- Lines: 1-35
- SHA256: 3dd3849020324e3dfec10cee2861d71fa11e019a7f9bf88f4f204ddd5fddb1e4

```md
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
```

## openspec/changes/reply-workflow-optimization/design.md

- Source: openspec/changes/reply-workflow-optimization/design.md
- Lines: 1-51
- SHA256: 2ec1cc6707ccfbea337fa309320c8dadea92b758f71798da8021ec5fedd5821d

```md
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
```

## openspec/changes/reply-workflow-optimization/tasks.md

- Source: openspec/changes/reply-workflow-optimization/tasks.md
- Lines: 1-37
- SHA256: ba46494b1693d1a20fe6d9383f2a0336757c8e3a45d753f27d2a7e7b4a9f87bf

```md
# reply-workflow-optimization 任务清单

## 1. CloudLLM 复用客户端

- [ ] 1.1 `CloudLLM` 增加 `_client` 懒加载缓存：首次 `generate()` 按 provider 创建 anthropic/openai client 并缓存
- [ ] 1.2 用 `threading.Lock` 保证并发首次调用只建一次 client
- [ ] 1.3 单测：多次 generate 复用同一 client 实例；并发首次调用仅创建一个 client

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
```

## openspec/changes/reply-workflow-optimization/specs/reply-assist/spec.md

- Source: openspec/changes/reply-workflow-optimization/specs/reply-assist/spec.md
- Lines: 1-43
- SHA256: 9c27a15d44cb6c681737dc696bb3ee52b736388898158542506bca8d14ac6a84

```md
# reply-assist Delta Spec

> Delta 变更，叠加于 `openspec/specs/reply-assist/spec.md`。

## ADDED Requirements

### Requirement: 回复生成异步任务

系统 SHALL 将回复生成改为异步任务：提交后立即返回任务标识，由后台线程执行 RAG + LLM，前端轮询任务状态直至完成。

#### Scenario: 提交回复任务

- **WHEN** 用户对某条消息请求"生成回复"
- **THEN** 系统 SHALL 创建回复任务并立即返回 `task_id`，不阻塞请求直至 LLM 完成

#### Scenario: 查询任务状态

- **WHEN** 客户端请求 `GET /api/reply/status/{task_id}`
- **THEN** 系统 SHALL 返回任务当前状态（pending/running/done/failed）；done 时包含回复内容与检索来源，failed 时包含可读错误

#### Scenario: 异步失败降级

- **WHEN** 回复任务执行中 LLM 或检索失败
- **THEN** 系统 SHALL 将任务置为 failed 并记录可读错误信息，不抛出 500

### Requirement: 多轮会话上下文

系统 SHALL 支持回复会话：同一会话内连续生成回复时，将先前对话历史作为上下文传给 LLM。

#### Scenario: 创建会话

- **WHEN** 用户请求生成回复且未携带 `session_id`
- **THEN** 系统 SHALL 自动创建新会话并记录该轮用户消息与生成回复

#### Scenario: 延续会话

- **WHEN** 用户携带既有 `session_id` 请求生成回复
- **THEN** 系统 SHALL 将该会话的历史消息作为上下文参与生成，并追加本轮内容

#### Scenario: 会话持久化

- **WHEN** 回复会话写入用户消息与助手回复
- **THEN** 系统 SHALL 将 `session_id`、角色、内容持久化存储，跨请求可恢复
```

## openspec/changes/reply-workflow-optimization/specs/web-app/spec.md

- Source: openspec/changes/reply-workflow-optimization/specs/web-app/spec.md
- Lines: 1-23
- SHA256: e3a39960169079643aa6501f6cb8a40fac07f13e6b4a0fa5b9244e4659fcd13f

```md
# web-app Delta Spec

> Delta 变更，叠加于 `openspec/specs/web-app/spec.md`。

## ADDED Requirements

### Requirement: 回复结果异步轮询

系统 SHALL 在提交回复任务后，前端轮询任务状态直至完成并展示结果，而非阻塞等待。

#### Scenario: 轮询任务状态

- **WHEN** 用户触发回复生成
- **THEN** 前端 SHALL 提交任务后周期轮询 `GET /api/reply/status/{task_id}`，完成后展示建议回复与来源，失败时展示错误

### Requirement: 建议回复一键复制

系统 SHALL 为建议回复提供一键复制按钮，用户复制后自行粘贴到 WhatsApp。

#### Scenario: 复制建议回复

- **WHEN** 建议回复已生成展示
- **THEN** 页面 SHALL 提供「复制」按钮，点击后使用剪贴板 API 复制回复内容
```

