# web-app Delta Specification

## ADDED Requirements

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
