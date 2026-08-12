# whatsapp-sync Delta Spec

> Delta 变更，叠加于 `openspec/specs/whatsapp-sync/spec.md`。

## ADDED Requirements

### Requirement: 手动清理聊天消息

系统 SHALL 支持手动清理聊天消息数据，删除消息记录及其向量，不影响知识库与画像。

#### Scenario: 清理会话消息

- **WHEN** 用户请求清理某会话
- **THEN** 系统 SHALL 删除该会话的 messages 记录与对应 message_vectors，不删除知识库文档与画像

#### Scenario: 清理过期消息

- **WHEN** 用户请求清理 N 天前的消息
- **THEN** 系统 SHALL 删除 N 天前的 messages 记录与对应 message_vectors
