## MODIFIED Requirements

### Requirement: 回复生成异步任务

系统 SHALL 将回复生成改为异步任务：提交后立即返回任务标识，由后台线程执行 RAG + LLM，前端轮询任务状态直至完成。任务可携带语种（language）、场景（scenario）、语气（formality）参数，缺省使用默认值。

#### Scenario: 提交回复任务

- **WHEN** 用户对某条消息请求"生成回复"
- **THEN** 系统 SHALL 创建回复任务并立即返回 `task_id`，不阻塞请求直至 LLM 完成

#### Scenario: 查询任务状态

- **WHEN** 客户端请求 `GET /api/reply/status/{task_id}`
- **THEN** 系统 SHALL 返回任务当前状态（pending/running/done/failed）；done 时包含回复内容与检索来源，failed 时包含可读错误

#### Scenario: 异步失败降级

- **WHEN** 回复任务执行中 LLM 或检索失败
- **THEN** 系统 SHALL 将任务置为 failed 并记录可读错误信息，不抛出 500

#### Scenario: 提交带生成参数的任务

- **WHEN** 用户请求生成回复且携带语种/场景/语气参数
- **THEN** 系统 SHALL 将参数持久化到任务并用于生成；未携带时使用默认值（中文/通用/口语）
