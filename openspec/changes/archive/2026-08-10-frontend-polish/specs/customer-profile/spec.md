# customer-profile Delta Specification

## ADDED Requirements

### Requirement: 客户头像资产

系统 SHALL 为客户维护头像资产，包含本地头像文件与 `avatar_path` 字段；无头像时由界面展示占位。

#### Scenario: 头像落库

- **WHEN** 采集器抓取到某客户头像
- **THEN** 系统 SHALL 将头像文件保存到本地 `avatars` 目录并更新该客户 `avatar_path`

#### Scenario: 无头像客户

- **WHEN** 客户无头像文件
- **THEN** 系统 SHALL 在界面显示首字母占位头像，不报错
