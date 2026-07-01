## ADDED Requirements

### Requirement: 本地 Web 应用访问
系统 SHALL 提供 FastAPI 后端 + 前端页面的本地 Web 应用，通过本地浏览器访问（127.0.0.1）。

#### Scenario: 本地访问
- **WHEN** 用户启动应用并在浏览器打开本地地址
- **THEN** 系统 SHALL 展示 Web UI 主界面

### Requirement: 客户列表与画像页
系统 SHALL 提供客户列表页与客户画像页，画像页支持编辑。

#### Scenario: 浏览客户列表
- **WHEN** 用户打开客户列表
- **THEN** 系统 SHALL 展示所有客户及其关键画像摘要

#### Scenario: 编辑画像
- **WHEN** 用户在画像页编辑某字段并保存
- **THEN** 系统 SHALL 持久化该编辑值并标记为人工来源

### Requirement: 聊天浏览
系统 SHALL 提供聊天浏览页，按客户/聊天展示历史消息，并支持在消息上触发回复生成。

#### Scenario: 浏览聊天
- **WHEN** 用户打开某客户聊天
- **THEN** 系统 SHALL 分页展示该聊天的历史消息（含元数据与正文）

#### Scenario: 触发回复
- **WHEN** 用户在某条消息上请求生成回复
- **THEN** 系统 SHALL 在该消息上下文触发辅助回复生成并展示结果

### Requirement: 本地知识管理页
系统 SHALL 提供本地知识管理页，支持上传/列表/删除文档与检索测试。

#### Scenario: 管理知识
- **WHEN** 用户进入知识管理页
- **THEN** 系统 SHALL 展示已导入文档列表，支持上传新文档、删除文档、检索测试

### Requirement: 采集器状态可见
系统 SHALL 在 Web UI 暴露 WhatsApp 采集器的运行状态（连接/登录/最近同步时间）。

#### Scenario: 查看采集状态
- **WHEN** 用户查看应用状态
- **THEN** 系统 SHALL 展示采集器连接状态、登录状态与最近同步时间
