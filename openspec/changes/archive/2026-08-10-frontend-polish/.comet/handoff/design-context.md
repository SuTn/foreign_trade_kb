# Comet Design Handoff

- Change: frontend-polish
- Phase: design
- Mode: compact
- Context hash: d0e0927831f83b97c610dc5830dd1dd88e840f0ff748bd0b3bbd769cd0d6de9b

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/frontend-polish/proposal.md

- Source: openspec/changes/frontend-polish/proposal.md
- Lines: 1-36
- SHA256: 26caf56322d5186bd484ff4453f30e94163659794b190aa8ba994d552067a3bb

```md
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
```

## openspec/changes/frontend-polish/design.md

- Source: openspec/changes/frontend-polish/design.md
- Lines: 1-72
- SHA256: 74444426a6b8982cf663918dda1d3b3e6b91e614bb85a1e194d3f0e69d5df30a

```md
# frontend-polish Design

## Context

全站前端为裸 HTML（无样式、无静态资源、htmx 走 unpkg CDN），客户列表/仪表盘/详情/知识库/回复页均为朴素表格。动机见 proposal.md。约束：本地优先（不依赖外部网络）、纯静态无构建工具、双进程架构（采集器子进程 + Web 主进程共享 SQLite/Chroma）。

## Goals / Non-Goals

**Goals**
- 一套本地静态资源（CSS/JS/htmx）为全站提供统一样式与交互，离线可用
- 客户头像自动抓取（WhatsApp → 本地文件 → 卡片/详情展示），首字母占位兜底
- 客户卡片网格 + 前端实时搜索/筛选（含画像字段）
- 首页仪表盘（采集状态 + 客户/知识库统计 + 近期活跃会话）
- 详情页画像卡片化 + 聊天气泡化；知识库/回复页统一样式

**Non-Goals**
- 手动头像上传/替换（保留 `avatar_path` 后续扩展）
- 回复自动发送、多账号 UI、画像字段 schema 化
- 聊天同步/RAG/回复生成的逻辑改动

## Decisions

### D1: 头像抓取 —— 随 scan_all_chats 顺带抓，按 customer 归属
`scan_all_chats` 已逐会话打开（click → settle），打开后从 `conversation-header` 取头像 `img` src，用 `page.evaluate` 在页面上下文 fetch（只读 GET）→ base64 回传 → 写 `data/avatars/<customer_id>.<ext>` → 更新 `avatar_path`。customer_id 经当前 chat 的 `customer_chat_map` 解析；无映射/失败/>2MB 静默跳过。

- **备选**：按需抓（匹配时额外打开会话）—— 增加 WhatsApp 操作频率与复杂度；独立后台批量抓 —— 重复遍历 chat-list。均否决。
- 只读语义：`page.evaluate` fetch 是 GET，与既有 `page.locator().click()` 同一层级，不违背 ReadOnlyCDP 白名单模式。

### D2: 头像存储 —— `customers.avatar_path` 列 + `avatars_dir` 配置 + `/avatars` 静态挂载
- `config.py` 新增 `avatars_dir: Path = Path("data/avatars")`
- `schema.sql` 更新 `customers` CREATE 定义；`_init_schema` 对旧库 try/except `ALTER TABLE customers ADD COLUMN avatar_path TEXT`（SQLite 无 ADD COLUMN IF NOT EXISTS）
- `app.py` 新增 `app.mount("/avatars", StaticFiles(directory=<avatars_dir.resolve()>), name="avatars")`；`avatar_path` 存相对 URL `/avatars/<file>`
- **备选**：独立 `avatars` 表（支持 source/manual 覆盖）—— 本次无手动上传，YAGNI 否决；后续若加手动覆盖再迁移。

### D3: 仪表盘数据 —— 新增 `GET /api/stats`
一次返回聚合：客户统计（总数/有画像/关联会话）、知识库统计（文档/chunk/wiki 页）、采集状态（复用 `read_status`/`is_alive`）、近期活跃会话（`messages GROUP BY chat_id ORDER BY MAX(ts) DESC LIMIT 10` join `chats` 显示名 + `customer_chat_map` 客户）。

- 轮询策略：采集状态卡沿用现有 `/api/collector/status` 5s 轮询；`/api/stats` 一次性渲染（避免每 5s 全量重查）。

### D4: 搜索/筛选 —— 前端实时过滤
`customers()` 路由预聚合：一次查 `profiles` 拼 `{customer_id: [field:value...]}`，为每客户生成 `data-search` 串（名称+电话+公司+国家+画像字段，小写化）。搜索框 `oninput` + 国家/公司下拉 `onchange` → 前端过滤卡片（AND 叠加）。国家/公司选项由前端从已渲染数据提取 distinct。

### D5: 静态资源 —— 手写单文件，htmx 本地化
- `static/css/app.css`：浅色简洁、蓝色系、卡片圆角+轻阴影；头像组件（圆形 img / 首字母占位 span）；聊天气泡；仪表盘卡片；表单/按钮统一
- `static/js/app.js`：搜索/筛选过滤、首字母占位取色（名称 hash → 8 色调色板）
- `static/js/htmx.min.js`：从 unpkg 固定版本（htmx 2.x）下载本地化，`base.html` 统一引用（消除 CDN 依赖，离线可用）

### D6: 模板改造范围
| 模板 | 改造 |
|---|---|
| base.html | 引本地 CSS/JS/htmx，统一导航 |
| customers.html | 卡片网格 + 头像 + 搜索/筛选 |
| chat.html | 大头像 + 画像卡片分组（保留 HTMX 行内编辑） |
| chat_messages.html | 气泡左右分列（保持 partial swap 结构 + 分页 + 生成回复） |
| home.html | 仪表盘四卡 + 近期活跃会话列表 |
| knowledge.html / knowledge_docs.html / knowledge_search.html / reply_result.html | 统一卡片/表格样式 |

## Risks / Trade-offs

- **[头像选择器版本漂移]** WhatsApp 可能将头像改为 `div` background 或 src 懒加载未就绪 → 静默跳过 + 首字母占位兜底；实现时探测多种选择器（`header img`、`img[data-testid]`）。
- **[base64 回传体积]** 头像过大拖慢 CDP 回传 → 超 2MB 丢弃，下次扫描重试。
- **[htmx 本地化引入许可证/版本固定]** 固定具体版本（htmx 2.x）到 `static/js/htmx.min.js`，README 注明来源与版本。
- **[搜索数据随客户增长]** 前端一次性加载在数千客户时变重 → 当前 95 客户规模无忧，增长后换后端查询（设计预留 data-search 属性便于迁移）。

## Migration Plan

- 部署：无破坏性。旧库启动时 `_init_schema` 自动 ALTER 补 `avatar_path`（NULL → 首字母占位）。
- 回滚：代码回退即回退；新增列/文件目录无副作用。

## Open Questions

无 —— 关键决策（归属/存储/时点/兜底）均已在 brainstorming + 审计中与用户确认。
```

## openspec/changes/frontend-polish/tasks.md

- Source: openspec/changes/frontend-polish/tasks.md
- Lines: 1-40
- SHA256: 4709a1873c2396c69ce3a02a98c1fe9030a6f1a886a43ece8ba1e70ed1439216

```md
# frontend-polish Tasks

## 1. 数据层与配置

- [ ] 1.1 `config.py` 新增 `avatars_dir` 配置（默认 `data/avatars`）
- [ ] 1.2 `schema.sql` 更新 `customers` 定义含 `avatar_path`；`sqlite_store._init_schema` 兼容旧库 try/except `ALTER TABLE`
- [ ] 1.3 `tests/conftest.py` `tmp_data` 补 `avatars_dir` monkeypatch
- [ ] 1.4 测试：旧 schema 库打开后自动迁移出 `avatar_path` 列（幂等）

## 2. 采集器头像抓取

- [ ] 2.1 `scanner.py` 新增头像抓取 helper（读 conversation-header img src → page.evaluate fetch → base64）
- [ ] 2.2 `scan_all_chats` 打开会话后调用 helper：解析 customer_id、写 `avatars_dir/<customer_id>.<ext>`、更新 `avatar_path`；失败/无映射/超 2MB 静默跳过
- [ ] 2.3 测试：mock `page.evaluate` 返回 base64 + content-type → 断言文件落盘 + `avatar_path` 更新；无客户映射跳过；失败静默

## 3. 仪表盘 API

- [ ] 3.1 `routes.py` 新增 `GET /api/stats`（客户/知识库统计 + 采集状态 + 近期活跃会话，join chats/customer_chat_map）
- [ ] 3.2 测试：`/api/stats` 聚合正确性（临时库造数据）

## 4. Web 静态挂载

- [ ] 4.1 `app.py` 挂载 `/avatars` 静态目录（指向 `avatars_dir` 绝对路径）
- [ ] 4.2 `base.html` 引入本地 CSS/JS；下载固定版本 htmx 2.x 的 `htmx.min.js` 到 `static/js/` 并本地引用

## 5. 前端改版

- [ ] 5.1 `static/css/app.css`：基础样式（浅色简洁、蓝色系、卡片圆角+轻阴影）+ 头像/气泡/仪表盘组件
- [ ] 5.2 `static/js/app.js`：搜索/筛选过滤 + 首字母占位取色（名称 hash → 8 色调色板）
- [ ] 5.3 `customers.html`：卡片网格（头像 + 名称/电话/公司/国家）+ 搜索框 + 国家/公司筛选；`customers()` 路由预聚合画像字段拼 `data-search`
- [ ] 5.4 `chat.html`：大头像 + 画像卡片分组（保留 HTMX 行内编辑）
- [ ] 5.5 `chat_messages.html`：聊天气泡左右分列（保持 partial swap + 分页 + 生成回复）
- [ ] 5.6 `home.html`：仪表盘四卡 + 近期活跃会话列表（`/api/stats`）
- [ ] 5.7 `knowledge.html`/`knowledge_docs.html`/`knowledge_search.html`/`reply_result.html`：统一卡片/表格样式
- [ ] 5.8 测试：Web 路由渲染（卡片/仪表盘/详情带 avatar_path 与占位、`/api/stats` 页面）

## 6. 文档与收尾

- [ ] 6.1 README 使用流程补头像/仪表盘说明
- [ ] 6.2 全量 pytest + compileall 通过
```

## openspec/changes/frontend-polish/specs/customer-profile/spec.md

- Source: openspec/changes/frontend-polish/specs/customer-profile/spec.md
- Lines: 1-17
- SHA256: bd26de5c6e913852e78117f75e9abfbaf29d8a49639046219cadb58559892370

```md
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
```

## openspec/changes/frontend-polish/specs/web-app/spec.md

- Source: openspec/changes/frontend-polish/specs/web-app/spec.md
- Lines: 1-68
- SHA256: cedc511f3ecf4dd1df88660bbd2bdf4239ebed3f9e4c9d01282da6f07e49a5e3

```md
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
```

## openspec/changes/frontend-polish/specs/whatsapp-sync/spec.md

- Source: openspec/changes/frontend-polish/specs/whatsapp-sync/spec.md
- Lines: 1-17
- SHA256: 8aaf2d125cc461475bbfc8b422583716a3d6ef1ed4a4fd7da1e5e141964736bf

```md
# whatsapp-sync Delta Specification

## ADDED Requirements

### Requirement: 会话头像抓取

系统 SHALL 在自动扫描会话时顺带抓取 WhatsApp 会话头像并落盘，按 customer 归属，失败时静默跳过。

#### Scenario: 扫描时抓取头像

- **WHEN** 采集器自动扫描会话并打开某会话
- **THEN** 系统 SHALL 尝试抓取该会话头像，成功后写入本地头像文件并更新对应客户头像记录

#### Scenario: 头像抓取失败

- **WHEN** 头像不可用或抓取失败
- **THEN** 系统 SHALL 静默跳过，不中断扫描，后续扫描可重试
```

