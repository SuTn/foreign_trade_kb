# Proposal: 采集器设置中心（collector-settings-center）

## Why

采集器当前只有自动同步，外贸员无法主动触发全量扫描（例如首次接入后想立即构建完整知识库），也看不到扫描过程；同步频次只能改 `.env` 且需重启生效，非技术用户无法调优。前端页面整体为工具感布局，缺少统一视觉语言，与「本地专业工具」定位不符。

## What Changes

- **手动全量扫描**：在首页采集器状态区新增「立即全量扫描」按钮，复用 backfill 意图表 + 采集器轮询消费机制；后台执行、前端轮询展示进度（已扫会话数 / 新入库消息数 / 状态），扫描期间自动扫描自动跳过本轮，避免冲突。
- **采集器频次设置中心**：新增 `/settings` 页面，可配置 fast_tick、slow_tick、auto_scan_interval、auto_scan_max_chats、auto_scan_settle_sec 与 auto_scan 开关；配置持久化到 DB（`settings` 表），采集器主循环读 DB 即时生效，`.env` 作为默认值；重启后保留。
- **前端全量视觉改版**：简约清爽风格，全站（首页/客户/聊天/知识库/搜索/清理/设置）统一设计语言（导航、卡片、按钮、表单、表格、状态提示），不依赖外部 CDN。

## Capabilities

### New Capabilities
- `collector-settings`: 采集器运行参数（同步频次与扫描参数）的 DB 持久化、即时生效与前端配置接口能力
- `whatsapp-sync/manual-scan`: 手动触发全量扫描全部会话并展示进度的能力

### Modified Capabilities
- `web-app`: 新增采集器设置中心页面与首页状态控制区；升级全站统一样式为简约清爽视觉语言

## Impact

- **app/config.py**: settings 默认值来源改为「DB 覆盖，`.env` 兜底」，新增运行时读取入口
- **app/storage/sqlite_store.py**: 新增 `settings` 表（key-value）读写；`scan_requests` 意图表
- **app/collector/scanner.py**: 主循环改读 DB 配置；新增 scan 请求消费逻辑（含扫描进度写入 status）；手动扫描与自动扫描互斥
- **app/web/routes.py**: 新增设置读写 API、手动扫描触发 API、扫描进度 API、采集器状态扩展
- **app/web/templates/\***: 全站模板改版 + 新增 settings.html
- **app/web/static/css/app.css, js/app.js**: 视觉语言重构与设置交互逻辑
- 无第三方依赖变更；不影响 LLM/RAG/知识库核心与 WhatsApp 采集只读语义
