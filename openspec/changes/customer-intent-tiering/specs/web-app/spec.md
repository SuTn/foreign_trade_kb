## MODIFIED Requirements

### Requirement: 客户列表与画像页
系统 SHALL 提供客户列表页与客户画像页，画像页支持编辑。

#### Scenario: 浏览客户列表
- **WHEN** 用户打开客户列表
- **THEN** 系统 SHALL 展示所有客户及其关键画像摘要

#### Scenario: 编辑画像
- **WHEN** 用户在画像页编辑某字段并保存
- **THEN** 系统 SHALL 持久化该编辑值并标记为人工来源

#### Scenario: 展示意向等级徽章与标签
- **WHEN** 用户打开客户列表
- **THEN** 每个客户卡片 SHALL 展示其意向等级徽章（A/B/C/D）与业务标签

#### Scenario: 按意向等级筛选
- **WHEN** 用户选择意向等级筛选条件
- **THEN** 客户列表 SHALL 按所选等级过滤，且与搜索及其他筛选条件叠加生效

#### Scenario: 编辑意向等级与标签
- **WHEN** 用户在画像页编辑意向等级或标签并保存
- **THEN** 系统 SHALL 持久化该编辑值并标记为人工来源