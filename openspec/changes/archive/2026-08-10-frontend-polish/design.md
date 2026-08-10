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
