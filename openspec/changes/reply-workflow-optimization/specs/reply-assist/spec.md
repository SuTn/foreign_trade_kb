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
