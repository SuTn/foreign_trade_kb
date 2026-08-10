# frontend-polish Proposal

## Why

全站前端目前是裸 HTML + 表格（无样式、无静态资源、htmx 走外部 CDN），客户列表只有 ID/名称/电话等文本，业务员辨识客户困难，缺少搜索/筛选与概览能力。这与项目"本地优先、离线可用"的定位不一致（htmx 依赖 unpkg CDN）。本次通过一次统一的前端改版提升可用性与辨识度，并顺带把头像等客户资产落地本地。

## What Changes

- **整站基础样式**：新增本地静态资源 `static/css/app.css` + `static/js/app.js`（手写、不引 CDN）；htmx 本地化到 `static/js/htmx.min.js`；统一导航与卡片风格（浅色简洁、蓝色系、圆角+轻阴影）。
- **客户头像（自动抓取）**：采集器在 `scan_all_chats` 逐会话打开时顺带抓取 WhatsApp 会话头像，存 `data/avatars/<customer_id>.<ext>`；无头像时用客户名首字母彩色占位。
- **客户卡片网格**：客户列表改为卡片网格，每卡含头像 + 名称 + 电话 + 公司 + 国家。
- **客户搜索/筛选**：前端实时过滤（按名称/电话/公司/国家/画像字段），叠加国家与公司下拉筛选。
- **首页仪表盘**：采集器状态 + 客户统计 + 知识库统计 + 近期活跃会话，数据来自新增 `GET /api/stats`。
- **客户详情与聊天美化**：详情页大头像 + 画像字段卡片分组；聊天记录改为气泡样式（左右分列）。
- **知识库/回复页美化**：文档列表、检索结果、回复结果的统一卡片/表格样式。
- **数据层**：`customers` 表新增 `avatar_path` 列（旧库 ALTER 兼容）；新增 `avatars_dir` 配置；Web 挂载 `/avatars` 静态目录。

## Capabilities

### New Capabilities

（无 — 均为对现有能力的修改）

### Modified Capabilities

- `web-app`: 新增前端改版需求 —— 本地静态资源与统一样式、客户卡片网格、搜索/筛选、首页仪表盘（`/api/stats`）、聊天气泡、头像展示（含首字母占位）。
- `whatsapp-sync`: 新增头像抓取需求 —— `scan_all_chats` 逐会话抓取 WhatsApp 头像并落盘，按 customer 归属，失败静默。
- `customer-profile`: 新增客户头像资产需求 —— `avatar_path` 字段与本地头像文件，由采集器抓取维护（无手动上传）。

## Impact

- 代码：`app/web/routes.py`、`app/web/app.py`、`app/web/templates/*`、新增 `app/web/static/`（css/js/htmx）
- 采集器：`app/collector/scanner.py`（头像抓取 helper）
- 存储：`app/storage/schema.sql` + `sqlite_store.py`（`avatar_path` 列与旧库迁移）
- 配置：`app/config.py`（`avatars_dir`）
- 测试：采集器头像抓取（mock evaluate）、`/api/stats` 聚合、ALTER 迁移幂等、Web 渲染
