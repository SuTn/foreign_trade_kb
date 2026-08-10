# web-app Specification

## Purpose
TBD - created by archiving change whatsapp-customer-kb. Update Purpose after archive.
## Requirements
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

### Requirement: 本地静态资源与统一样式

系统 SHALL 通过本地静态资源提供统一样式与脚本，页面加载不依赖外部 CDN（含 htmx）。

#### Scenario: 静态资源本地化

- **WHEN** 用户加载任意页面
- **THEN** 页面 SHALL 从本地 `/static` 加载 CSS、JS 与 htmx，并呈现统一样式（浅色简洁、卡片圆角）

#### Scenario: 离线可用

- **WHEN** 本地网络不可用
- **THEN** 页面样式与前端交互逻辑 SHALL 仍完整可用

### Requirement: 客户头像展示

系统 SHALL 在客户列表与客户详情页展示客户头像；无真实头像时 SHALL 显示客户名首字母彩色占位。

#### Scenario: 列表显示头像

- **WHEN** 用户打开客户列表
- **THEN** 每个客户卡片 SHALL 显示其头像（真实头像或首字母占位）

#### Scenario: 详情显示大头像

- **WHEN** 用户打开客户详情页
- **THEN** 系统 SHALL 在客户头部展示大头像，并在无头像时显示首字母占位

### Requirement: 客户搜索与筛选

系统 SHALL 提供客户实时搜索与筛选，支持按名称/电话/公司/国家/画像字段搜索，并按国家与公司叠加筛选。

#### Scenario: 搜索客户

- **WHEN** 用户在客户列表搜索框输入关键字
- **THEN** 客户列表 SHALL 实时过滤出匹配客户（含画像字段匹配）

#### Scenario: 筛选客户

- **WHEN** 用户选择国家或公司筛选条件
- **THEN** 客户列表 SHALL 按所选条件过滤，且与搜索条件叠加生效

### Requirement: 首页仪表盘

系统 SHALL 提供首页仪表盘，展示采集器状态、客户统计、知识库统计与近期活跃会话。

#### Scenario: 查看仪表盘

- **WHEN** 用户打开首页
- **THEN** 系统 SHALL 展示采集器状态卡、客户统计卡、知识库统计卡与近期活跃会话列表

#### Scenario: 获取统计接口

- **WHEN** 客户端请求 `GET /api/stats`
- **THEN** 系统 SHALL 返回客户统计、知识库统计、采集器状态与近期活跃会话的聚合数据

### Requirement: 聊天气泡展示

系统 SHALL 将聊天记录以气泡样式展示，我方与客户方消息左右分列，并保留分页加载与生成回复入口。

#### Scenario: 浏览聊天气泡

- **WHEN** 用户浏览某客户聊天
- **THEN** 系统 SHALL 以气泡样式分列展示消息（我方/客户方），支持加载更早消息并在消息上触发回复生成

