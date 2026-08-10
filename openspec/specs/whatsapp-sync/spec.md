# whatsapp-sync Specification

## Purpose
TBD - created by archiving change whatsapp-customer-kb. Update Purpose after archive.
## Requirements
### Requirement: WhatsApp Web 连接与登录态持久化
系统 SHALL 通过 CDP 驱动独立 Chrome 打开 WhatsApp Web，并持久化登录态（user-data-dir），使重启后无需重新扫码。

#### Scenario: 首次扫码登录
- **WHEN** 首次启动采集器且 WhatsApp Web 未登录
- **THEN** 系统 SHALL 在 Web UI 展示登录二维码/状态，登录成功后开始采集

#### Scenario: 重启复用登录态
- **WHEN** 采集器重启且 user-data-dir 中存在有效登录态
- **THEN** 系统 SHALL 跳过扫码直接进入采集，无需人工干预

### Requirement: 消息元数据与明文正文采集
系统 SHALL 通过 CDP 读取 WhatsApp Web IndexedDB `model-storage` 库的 message/chat/contact/group-metadata stores 获取消息元数据，并通过 DOM 快照获取明文正文，按消息 id 合并两者。

#### Scenario: 合并元数据与正文
- **WHEN** 一次采集 tick 完成
- **THEN** 每条消息 SHALL 同时具备 IDB 来源的元数据（id/chatId/fromMe/from/timestamp/type）与 DOM 来源的明文正文（若该消息已渲染）

#### Scenario: 正文缺失容忍
- **WHEN** 某历史消息未在当前 DOM 渲染
- **THEN** 系统 SHALL 保存其元数据，正文标记为缺失，不阻塞该批采集

### Requirement: 实时同步新消息
系统 SHALL 以快 tick（约 2s）DOM 增量抓取新消息，仅当可见行 hash 变化时才产出增量，避免空闲刷屏。

#### Scenario: 新消息增量同步
- **WHEN** 聊天窗口出现新消息且可见行 hash 变化
- **THEN** 系统 SHALL 在下一个快 tick 内采集并入库该新消息

#### Scenario: 空闲不刷屏
- **WHEN** 聊天窗口无新消息、可见行 hash 未变
- **THEN** 系统 SHALL 不产出增量事件

### Requirement: 全量校准
系统 SHALL 以慢 tick（约 30s）走 IDB 全量校准，补齐快 tick 遗漏的元数据。

#### Scenario: 慢 tick 校准
- **WHEN** 慢 tick 触发
- **THEN** 系统 SHALL 走 IDB walk 并与已入库消息 upsert 合并，补齐缺失元数据

### Requirement: 按需历史回溯
系统 SHALL 支持对指定聊天手动触发滚动加载历史 DOM 行并采集，不自动执行首次全量回溯。

#### Scenario: 手动触发回溯
- **WHEN** 用户在 Web UI 对某聊天点击"回溯历史"
- **THEN** 系统 SHALL 程序化滚动加载该聊天历史 DOM 行并采集入库，直到达到上限或无更多历史

#### Scenario: 不自动全量回溯
- **WHEN** 采集器首次启动
- **THEN** 系统 SHALL 仅同步当前可见与新消息，不自动回溯全部历史

### Requirement: 幂等 upsert
系统 SHALL 按 (account_id, chat_id, message_id) 幂等 upsert 消息到结构化存储，按 (chatId, day) 分组幂等 upsert 到向量库，保证可重试且不重复。

#### Scenario: 重复采集去重
- **WHEN** 同一消息被多次采集
- **THEN** 系统 SHALL 仅保留一条记录，不产生重复

### Requirement: 降低封号风险的采集行为
系统 SHALL 采取单设备、拟人化轮询间隔加随机抖动、不自动发送消息、不高频全量扫描的策略降低封号风险。

#### Scenario: 拟人化轮询
- **WHEN** 采集器运行中
- **THEN** 轮询间隔 SHALL 带随机抖动，不呈现机械固定频率

#### Scenario: 不自动发送
- **WHEN** 系统运行任意功能
- **THEN** 系统 SHALL 不向 WhatsApp 发送任何消息，仅采集与只读操作

### Requirement: 会话头像抓取

系统 SHALL 在自动扫描会话时顺带抓取 WhatsApp 会话头像并落盘，按 customer 归属，失败时静默跳过。

#### Scenario: 扫描时抓取头像

- **WHEN** 采集器自动扫描会话并打开某会话
- **THEN** 系统 SHALL 尝试抓取该会话头像，成功后写入本地头像文件并更新对应客户头像记录

#### Scenario: 头像抓取失败

- **WHEN** 头像不可用或抓取失败
- **THEN** 系统 SHALL 静默跳过，不中断扫描，后续扫描可重试

