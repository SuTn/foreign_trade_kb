# whatsapp-sync/resilience Specification

## Purpose

确保 WhatsApp 采集器在 Chrome 崩溃、CDP 会话失效或瞬时异常时能自动自愈并继续采集，同时限制 IndexedDB 全量读取的资源消耗，并可靠地处理按需回溯请求。

## ADDED Requirements

### Requirement: 采集器断线自愈
系统 SHALL 在采集主循环的任何异常下不退出进程：瞬时错误按指数退避重试，CDP 会话失效时自动重新建立浏览器连接，保证核心采集链路持续可用。

#### Scenario: 瞬时异常自动重试
- **WHEN** 采集 tick 中发生可重试的瞬时异常（如网络抖动、单次 CDP 调用失败）
- **THEN** 系统 SHALL 记录该失败并按其退避策略等待后重试，主循环不退出、不中断后续 tick

#### Scenario: CDP 会话失效自愈
- **WHEN** 检测到 CDP 会话失效或浏览器连接断开
- **THEN** 系统 SHALL 自动重新启动浏览器并恢复采集，无需人工干预

#### Scenario: 子进程守护
- **WHEN** 采集器子进程意外退出
- **THEN** 系统 SHALL 由守护进程自动重新拉起采集器，Web 服务不因此终止

### Requirement: IndexedDB 分页读取
系统 SHALL 以分页或上限方式读取 IndexedDB store，应用 `max_records_per_store` 上限，避免单次全量读取造成内存与带宽峰值。

#### Scenario: 分页读取生效
- **WHEN** 慢 tick 读取 IDB store
- **THEN** 系统 SHALL 按 `max_records_per_store` 限制单 store 读取数量，不一次拉取全量数据

### Requirement: 按需回溯请求可靠处理
系统 SHALL 可靠处理按需回溯请求队列：请求表在 schema 中定义、轮询不因表缺失而报错、失败任务可重试。

#### Scenario: 请求队列可靠轮询
- **WHEN** 回溯请求表不存在或为空
- **THEN** 系统 SHALL 不报错、不刷错误日志，静默等待新请求

#### Scenario: 失败回溯可重试
- **WHEN** 某回溯请求执行失败
- **THEN** 系统 SHALL 保留该请求以供重试，不将其错误地标记为完成
