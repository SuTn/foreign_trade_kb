# reply-assist Specification

## Purpose
TBD - created by archiving change whatsapp-customer-kb. Update Purpose after archive.
## Requirements
### Requirement: RAG 辅助回复生成
系统 SHALL 基于客户画像 + 相关历史聊天 + 本地产品知识，结合当前收到的消息，通过 RAG 生成建议回复。

#### Scenario: 生成建议回复
- **WHEN** 用户在聊天浏览页对某条消息请求"生成回复"
- **THEN** 系统 SHALL 检索该客户画像、相关历史聊天与产品知识，生成一条建议回复并展示

#### Scenario: 仅生成不自动发送
- **WHEN** 系统生成建议回复
- **THEN** 系统 SHALL 不自动发送该回复到 WhatsApp，仅展示供用户复制/编辑

### Requirement: 回复上下文可追溯
系统 SHALL 展示建议回复所依据的检索来源（画像字段/历史消息片段/产品知识片段）。

#### Scenario: 展示来源
- **WHEN** 系统返回建议回复
- **THEN** 系统 SHALL 同时展示支撑该回复的检索来源片段，供用户判断依据

### Requirement: 多回复候选
系统 SHALL 支持为同一条消息生成多个候选回复供用户选择。

#### Scenario: 生成多候选
- **WHEN** 用户请求生成回复
- **THEN** 系统 SHALL 提供至少一个候选回复，并支持用户请求重新生成获得不同候选

### Requirement: 回复生成失败降级
系统 SHALL 在回复生成（LLM 或检索）失败时返回可读的降级结果，而非 500 错误。

#### Scenario: LLM 生成失败
- **WHEN** 回复生成过程中 LLM 调用失败或不可用
- **THEN** 系统 SHALL 返回可读错误信息（含失败原因提示），不返回 500

#### Scenario: 检索失败仍可提示
- **WHEN** 回复生成的检索环节失败
- **THEN** 系统 SHALL 返回可读错误信息并提示检索不可用，不返回 500

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

