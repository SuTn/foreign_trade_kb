# customer-profile Specification

## Purpose
TBD - created by archiving change whatsapp-customer-kb. Update Purpose after archive.
## Requirements
### Requirement: 客户画像字段维护
系统 SHALL 为每个客户维护画像字段（姓名/公司/国家/产品兴趣/询价历史/沟通偏好/语言/成交阶段等），存储于结构化存储，并支持手动编辑修正。画像抽取所用的聊天摘要 SHALL 对群聊会话按发送者标注消息归属，使 LLM 能区分群内不同成员的发言。

#### Scenario: 自动抽取画像
- **WHEN** 某客户有新增聊天内容并触发画像更新
- **THEN** 系统 SHALL 由 LLM 从该客户近期聊天摘要中抽取/更新画像字段，带时间戳与来源标记

#### Scenario: 群聊摘要按发送者标注
- **WHEN** 画像抽取所依据的聊天摘要来自群聊会话
- **THEN** 摘要 SHALL 按发送者显示名标注每条消息（如 `成员名: 正文`），单聊保持 `我/客户` 标注不变

#### Scenario: 手动编辑优先
- **WHEN** 用户手动编辑某画像字段
- **THEN** 该字段 SHALL 以用户编辑值为准（标记为人工来源），不被后续自动抽取覆盖

### Requirement: 客户实体匹配
系统 SHALL 将 WhatsApp chatId/JID 关联到客户实体，MVP 采用手机号 + 显示名启发式匹配并支持人工确认合并。

#### Scenario: 启发式匹配
- **WHEN** 采集到新聊天
- **THEN** 系统 SHALL 用手机号 + 显示名启发式匹配现有客户实体，匹配不确定时标记为待确认

#### Scenario: 人工合并
- **WHEN** 用户确认两个聊天属于同一客户
- **THEN** 系统 SHALL 合并其消息与画像到同一客户实体

### Requirement: 客户分析
系统 SHALL 基于客户画像 + 聊天摘要给出客户分析，包括兴趣点、活跃度、跟进建议。

#### Scenario: 生成客户分析
- **WHEN** 用户在客户画像页请求分析
- **THEN** 系统 SHALL 基于该客户画像与近期聊天摘要生成分析（兴趣点/活跃度/跟进建议）并展示

### Requirement: 画像可查看
系统 SHALL 在 Web UI 提供客户画像页，展示画像字段、来源标记与最近更新时间。

#### Scenario: 查看画像
- **WHEN** 用户打开某客户画像页
- **THEN** 系统 SHALL 展示该客户全部画像字段、各字段来源（自动/人工）与最近更新时间

### Requirement: 客户头像资产

系统 SHALL 为客户维护头像资产，包含本地头像文件与 `avatar_path` 字段；无头像时由界面展示占位。

#### Scenario: 头像落库

- **WHEN** 采集器抓取到某客户头像
- **THEN** 系统 SHALL 将头像文件保存到本地 `avatars` 目录并更新该客户 `avatar_path`

#### Scenario: 无头像客户

- **WHEN** 客户无头像文件
- **THEN** 系统 SHALL 在界面显示首字母占位头像，不报错

