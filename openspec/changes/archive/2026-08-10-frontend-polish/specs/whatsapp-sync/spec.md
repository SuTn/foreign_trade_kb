# whatsapp-sync Delta Specification

## ADDED Requirements

### Requirement: 会话头像抓取

系统 SHALL 在自动扫描会话时顺带抓取 WhatsApp 会话头像并落盘，按 customer 归属，失败时静默跳过。

#### Scenario: 扫描时抓取头像

- **WHEN** 采集器自动扫描会话并打开某会话
- **THEN** 系统 SHALL 尝试抓取该会话头像，成功后写入本地头像文件并更新对应客户头像记录

#### Scenario: 头像抓取失败

- **WHEN** 头像不可用或抓取失败
- **THEN** 系统 SHALL 静默跳过，不中断扫描，后续扫描可重试
