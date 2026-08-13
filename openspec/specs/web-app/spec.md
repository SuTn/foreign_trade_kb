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

### Requirement: 接口错误反馈
系统 SHALL 在回复生成、知识检索或文档上传等接口返回降级错误时，在 Web UI 向用户呈现可读的错误信息，而非通用 500 页面。

#### Scenario: 显示可读错误
- **WHEN** 用户触发回复生成/检索/上传且后端返回降级错误
- **THEN** 系统 SHALL 在页面向用户展示该错误信息与原因提示，页面不崩溃

#### Scenario: 错误后页面可用
- **WHEN** 接口返回降级错误后
- **THEN** 系统 SHALL 保持页面其他功能可用，用户可重试或返回

### Requirement: 回复结果异步轮询

系统 SHALL 在提交回复任务后，前端轮询任务状态直至完成并展示结果，而非阻塞等待。

#### Scenario: 轮询任务状态

- **WHEN** 用户触发回复生成
- **THEN** 前端 SHALL 提交任务后周期轮询 `GET /api/reply/status/{task_id}`，完成后展示建议回复与来源，失败时展示错误

### Requirement: 建议回复一键复制

系统 SHALL 为建议回复提供一键复制按钮，用户复制后自行粘贴到 WhatsApp。

#### Scenario: 复制建议回复

- **WHEN** 建议回复已生成展示
- **THEN** 页面 SHALL 提供「复制」按钮，点击后使用剪贴板 API 复制回复内容

### Requirement: 全局搜索

系统 SHALL 提供全局搜索页与接口，跨客户、消息、知识库、画像四源检索并分组展示。

#### Scenario: 搜索客户

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回名称/电话/公司/国家匹配的客户

#### Scenario: 搜索消息

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回全文匹配的聊天消息

#### Scenario: 搜索知识库

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回匹配的知识库文档片段

#### Scenario: 搜索画像

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回字段值匹配的客户画像

### Requirement: 手动数据清理

系统 SHALL 提供手动清理聊天数据的管理入口，支持按会话或按天数删除，且不影响知识库文档与客户画像。

#### Scenario: 按会话清理

- **WHEN** 用户指定某会话（chat_id）请求清理
- **THEN** 系统 SHALL 删除该会话的全部聊天消息及其向量

#### Scenario: 按天数清理

- **WHEN** 用户指定天数 N 请求清理
- **THEN** 系统 SHALL 删除 N 天前的全部聊天消息及其向量

#### Scenario: 保留知识库与画像

- **WHEN** 清理聊天数据
- **THEN** 系统 SHALL 不删除知识库文档，不删除客户画像字段

### Requirement: 采集器异常全局提示

系统 SHALL 在 Web UI 全局区域展示采集器状态，采集器不可达时显示异常横幅。

#### Scenario: 展示采集器异常

- **WHEN** 采集器不在线（is_alive=false）
- **THEN** 系统 SHALL 在页面全局横幅显示「采集器异常」提示

#### Scenario: 定时检查采集状态

- **WHEN** 用户停留在任意页面
- **THEN** 前端 SHALL 定时轮询采集器状态并在异常时更新横幅

### Requirement: 采集器设置中心页面
系统 SHALL 提供采集器设置中心页面，允许用户查看与修改采集器运行参数（同步频次与扫描参数），并在保存后即时生效。

#### Scenario: 访问设置中心
- **WHEN** 用户打开设置中心页面
- **THEN** 系统 SHALL 展示所有可配置的采集器参数及其当前生效值，并支持导航访问

#### Scenario: 修改并保存参数
- **WHEN** 用户在设置中心修改参数并提交
- **THEN** 系统 SHALL 校验并保存，保存成功后展示确认提示并显示新的生效值

#### Scenario: 恢复默认值
- **WHEN** 用户在设置中心请求恢复某参数默认值
- **THEN** 系统 SHALL 将该参数重置为 `.env` 默认值并即时生效

### Requirement: 首页采集器状态控制区
系统 SHALL 在首页提供采集器状态与控制区，展示实时状态（连接/最近同步）并提供「立即全量扫描」入口与扫描进度反馈。

#### Scenario: 查看状态与扫描入口
- **WHEN** 用户打开首页
- **THEN** 系统 SHALL 展示采集器连接状态、最近同步时间与「立即全量扫描」按钮

#### Scenario: 展示扫描进度
- **WHEN** 手动扫描进行中
- **THEN** 首页控制区 SHALL 展示当前扫描进度（已扫/总会话数与新入库消息数）直至完成

#### Scenario: 展示扫描确认
- **WHEN** 用户点击「立即全量扫描」
- **THEN** 系统 SHALL 先展示确认提示（说明会将未读标记为已读），确认后才提交请求

### Requirement: 全站统一样式
系统 SHALL 以简约清爽的视觉语言统一全部页面（首页/客户/聊天/知识库/搜索/清理/设置）的布局、导航、卡片、按钮、表单、表格与状态提示，样式本地化加载不依赖外部 CDN。

#### Scenario: 全站一致风格
- **WHEN** 用户浏览任意页面
- **THEN** 页面 SHALL 呈现统一的简约清爽视觉语言（浅色背景、卡片化、统一的导航与组件样式）

#### Scenario: 设置页纳入统一样式
- **WHEN** 用户打开设置中心页面
- **THEN** 设置页 SHALL 与其余页面使用相同的设计语言与导航

