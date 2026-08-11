# whatsapp-sync Delta Specification

## MODIFIED Requirements

### Requirement: 幂等 upsert
系统 SHALL 按 (account_id, chat_id, message_id) 幂等 upsert 消息到结构化存储，按消息 id 独立幂等 upsert 到向量库，保证可重试且不重复；同一会话同日不同消息的向量 SHALL 各自独立保存，互不覆盖。

#### Scenario: 重复采集去重
- **WHEN** 同一消息被多次采集
- **THEN** 系统 SHALL 仅保留一条记录，不产生重复

#### Scenario: 同日多消息独立入库
- **WHEN** 同一会话同一天采集多条不同消息
- **THEN** 系统 SHALL 为每条消息独立保存向量，后到的消息不覆盖先到的消息

#### Scenario: 向量语义保留
- **WHEN** 历史聊天被向量召回
- **THEN** 系统 SHALL 能召回该会话每条消息（而非仅每会话每天最后一条）
