## Purpose

为采集器提供运行参数（同步频次与扫描参数）的持久化存储、即时生效与前端配置接口，使非技术用户无需编辑 .env 即可调优采集行为。

## ADDED Requirements

### Requirement: 采集器参数持久化与即时生效
系统 SHALL 将采集器运行参数持久化到本地数据库 settings 表，采集器主循环每次读取时以 DB 值覆盖 `.env` 默认值，保存后无需重启即生效。

#### Scenario: 参数保存后即时生效
- **WHEN** 用户通过 Web UI 修改采集器参数并保存
- **THEN** 系统 SHALL 立即持久化到 settings 表，采集器下一个轮询周期即采用新值

#### Scenario: 重启后保留配置
- **WHEN** 采集器进程重启
- **THEN** 系统 SHALL 从 settings 表恢复用户已配置的参数，未配置项回退 `.env` 默认值

#### Scenario: 未配置项使用默认值
- **WHEN** settings 表中某参数从未被用户配置
- **THEN** 系统 SHALL 使用 `.env` 中的对应默认值

### Requirement: 可配置参数范围与校验
系统 SHALL 支持配置的采集器参数包括：fast_tick_sec、slow_tick_sec、auto_scan_interval_sec、auto_scan_max_chats、auto_scan_settle_sec 与 auto_scan_chats 开关，并对数值参数进行范围校验。

#### Scenario: 接受合法配置
- **WHEN** 用户提交所有参数均在合法范围内
- **THEN** 系统 SHALL 接受并保存全部参数

#### Scenario: 拒绝非法值
- **WHEN** 用户提交的任一参数超出合法范围或为非数值
- **THEN** 系统 SHALL 拒绝保存并返回可读的校验错误，同时保持原配置不变

### Requirement: 前端配置接口
系统 SHALL 提供读写采集器参数的 HTTP 接口，供设置中心页面调用。

#### Scenario: 读取当前配置
- **WHEN** 设置中心页面加载
- **THEN** 系统 SHALL 返回各参数的当前生效值（DB 值或默认值）

#### Scenario: 更新配置
- **WHEN** 用户提交新参数值
- **THEN** 系统 SHALL 校验并保存，返回更新后的生效值

#### Scenario: 恢复默认值
- **WHEN** 用户请求恢复某参数为默认值
- **THEN** 系统 SHALL 删除该参数在 settings 表中的记录，使其回退到 `.env` 默认值
