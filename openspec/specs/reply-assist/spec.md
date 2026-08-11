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

