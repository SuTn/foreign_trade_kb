# whatsapp-sync Delta Specification

## MODIFIED Requirements

### Requirement: 消息元数据与明文正文采集
系统 SHALL 通过 CDP 读取 WhatsApp Web IndexedDB `model-storage` 库的 message/chat/contact/group-metadata stores 获取消息元数据，并通过 DOM 快照获取明文正文，按消息 id 合并两者。正文提取 SHALL 排除引用回复中的被引用文本，仅保留消息本身正文；相册/媒体消息 SHALL 以可用说明文字或媒体标记入库。

#### Scenario: 合并元数据与正文
- **WHEN** 一次采集 tick 完成
- **THEN** 每条消息 SHALL 同时具备 IDB 来源的元数据（id/chatId/fromMe/from/timestamp/type）与 DOM 来源的明文正文（若该消息已渲染）

#### Scenario: 引用回复正文净化
- **WHEN** 某消息为引用回复（含被引用文本块）
- **THEN** 系统 SHALL 仅采集该消息本人的正文文本，排除被引用的历史消息内容

#### Scenario: 相册/媒体消息
- **WHEN** 消息行为相册或媒体行（非普通文本行）
- **THEN** 系统 SHALL 不再忽略该行；有说明文字时采集说明文字，无正文时以媒体标记作为 body

#### Scenario: 正文缺失容忍
- **WHEN** 某历史消息未在当前 DOM 渲染
- **THEN** 系统 SHALL 保存其元数据，正文标记为缺失，不阻塞该批采集

## ADDED Requirements

### Requirement: 消息发送方向判断
系统 SHALL 通过 DOM 渲染信号（tail-in/tail-out）与 IDB 消息元数据（发送者 JID 与自身账号比对）联合判断消息方向，任一来源可用时作为依据，两者冲突时以 IDB 元数据为准，降低误判。

#### Scenario: 多信号联合判断
- **WHEN** 某消息同时具备 DOM tail 信号与 IDB 发送者元数据
- **THEN** 系统 SHALL 综合两者判断 `fromMe`，冲突时以 IDB 发送者与自身账号比对结果为准

#### Scenario: 仅 DOM 信号可用
- **WHEN** 某消息仅能从 DOM 判断方向
- **THEN** 系统 SHALL 以 tail-in/tail-out 信号作为 `fromMe` 依据
