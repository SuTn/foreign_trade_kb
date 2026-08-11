# whatsapp-sync/group-chat Specification

## Purpose
支持 WhatsApp 群聊会话的识别与消息归属：将 `@g.us` 群聊标记为 `kind=group`、解析群成员显示名，使群聊消息能正确入库并在画像摘要与 Web 界面中展示发送者。
## Requirements
### Requirement: 群聊会话识别
系统 SHALL 读取 IDB `group-metadata` store，识别 `@g.us` 群聊会话，将其以 `kind=group` 落库到结构化存储，并记录群名。

#### Scenario: 群聊入库
- **WHEN** 采集器同步到 `@g.us` 会话的消息
- **THEN** 系统 SHALL 将该会话标记为 `kind=group` 并记录群名，消息按群聊归属入库

#### Scenario: 单聊不受影响
- **WHEN** 采集器同步到 `@c.us` 单聊会话
- **THEN** 系统 SHALL 保持 `kind=single`，行为与既有同步一致

### Requirement: 群成员显示名解析
系统 SHALL 解析群聊入站消息的发送者身份，将成员 LID/手机号 JID 映射为可读显示名；无法解析时回退原始标识。

#### Scenario: 成员名解析
- **WHEN** 群聊入站消息发送者为已知联系人（含 LID）
- **THEN** 系统 SHALL 在消息中记录该成员的显示名，供摘要与界面使用

#### Scenario: 成员名缺失回退
- **WHEN** 群聊入站消息发送者无法匹配任何联系人
- **THEN** 系统 SHALL 以原始 JID 作为回退标识，不中断该批采集

### Requirement: 群聊发送者归属入库
系统 SHALL 在消息记录中同时保存发送者 JID 与显示名，使画像摘要能区分群内不同成员。

#### Scenario: 入库携带发送者名
- **WHEN** 群聊消息入库
- **THEN** 系统 SHALL 持久化发送者 JID 与解析后的显示名，供后续消费

#### Scenario: 群聊不拆成员客户
- **WHEN** 群聊会话关联客户实体
- **THEN** 系统 SHALL 将整个群聊关联到单个客户实体，不为群成员单独创建客户

