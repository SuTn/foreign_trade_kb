# Comet Design Handoff

- Change: collector-settings-center
- Phase: design
- Mode: compact
- Context hash: 229c7c9555c57b0a7fb7beea1cfeaf5a479b906bd834fef37fc41e1ffa601a9e

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/collector-settings-center/proposal.md

- Source: openspec/changes/collector-settings-center/proposal.md
- Lines: 1-30
- SHA256: 070831987904caf4b883e798815bb55798b8eb0c8d6a5d085630f9c4ceba9a20

```md
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
```

## openspec/changes/collector-settings-center/design.md

- Source: openspec/changes/collector-settings-center/design.md
- Lines: 1-56
- SHA256: 87378b66a312c7af9dcf2fc494da284ab299e84ce4d4db1b251edaf765c92b55

```md
# Design: 采集器设置中心

## Context

采集器（`app/collector/scanner.py` 的 `Scanner.run()`）为独立进程，通过 CDP 只读访问 WhatsApp Web，主循环执行 fast_tick（DOM 增量）/ slow_tick（IDB 校准）/ scan_all_chats（周期全量扫描）。配置全部来自 `app/config.py` 的进程级单例 `settings`（启动时从 `.env` 加载，运行时不刷新）。Web 进程与采集器进程通过 SQLite 共享数据；`backfill_requests` 表已实现「Web 写意图 → 采集器轮询消费」的异步请求模式（`_drain_backfill_requests`，scanner.py:443）。

## Goals / Non-Goals

**Goals:**
- 手动全量扫描复用 backfill 意图表模式，新增 `scan_requests` 表，采集器轮询消费
- 采集器参数持久化到 `settings` 表（key-value），主循环每轮读取 DB 覆盖 `.env`，即时生效
- 设置中心 + 首页状态控制区 + 全站视觉改版（简约清爽）

**Non-Goals:**
- 不改动 fast/slow tick 的采集核心逻辑与只读语义
- 不引入鉴权/多用户/权限体系
- 不做设置项的实时热替换复杂度（如 LLM/嵌入类配置不入此范围，仅采集参数）

## Decisions

### D1: 手动扫描走「意图表 + 轮询消费」而非直接 IPC
与 backfill 完全同构：`POST /api/collector/scan` 写入 `scan_requests(id, requested_at, status, done, ...)`；采集器主循环 `_drain_scan_requests()` 取 pending 请求执行 `scan_all_chats()`。进度写入 `status.json`（与现有 status 文件合一）：`{scan: {running, current, total, ingested, finished_at}}`。
- 备选：跨进程直接调用（signal/socket）——破坏两进程解耦，且采集器可能正忙/需重建，回退复杂。弃用。

### D2: 参数持久化 `settings` 表 + 主循环读 DB
`settings` 表 `(key TEXT PRIMARY KEY, value TEXT, updated_at)`，key 用下划线名（`fast_tick_sec` 等）对应 config 字段。新增 `app/storage` 的 `RuntimeSettings` 读写层：`get(key, default)` 返回 DB 值或 `.env` 默认。采集器主循环每轮调用 `load_runtime_settings()` 刷新自身 `self._rt` 字典，读取 `self._rt.get("fast_tick_sec", settings.fast_tick_sec)` 等。
- 备选：写回 `.env` + 重载 —— 需重启/热重载 pydantic，且易损坏配置。弃用。
- 校验：Web API 层做范围校验（间隔 >0，max_chats 1..1000，settle 0.1..30），非法值 400 + 提示；DB 层只存字符串，解析失败回退默认值，保证采集器健壮。

### D3: 手动扫描与自动扫描互斥
采集器维护 `self._manual_scan_active` 标志：`_drain_scan_requests` 发现请求则执行（串行在 `run()` 主循环中，天然与自动扫描同线程互斥——`scan_all_chats` 是阻塞调用）。执行期间设置 `last_scan` 为当前时间，使自动周期扫描分支的 `time.time() - last_scan >= interval` 为假，自然跳过本轮；扫描完成重置。重复触发：`scan_requests` 表中已有未完成请求时，Web API 直接拒绝并返回 `{busy: true}`。

### D4: 扫描进度数据源
`scan_all_chats()` 当前返回入库数，但无内部进度。改造：传入回调 `on_progress(current, total, ingested)`，每处理一个会话调用一次，Scanner 将进度合并写入 status.json。前端首页 `GET /api/collector/status` 已每 5s 轮询，直接在其响应中加入 `scan` 字段即可展示，无需新增轮询端点。

### D5: 前端视觉改版
基于现有 `app.css` 的 CSS 变量体系演进，不引入框架：重排导航（图标+标签）、统一页面标题区（page-title + page-sub + 操作区）、卡片/按钮/表单/表格/徽章样式收敛、空态与错误提示统一。新增 `settings.html` 与导航项。全部本地静态资源（已满足离线要求）。
- 备选：引入 Tailwind/shadcn —— 需构建链，违背「本地、无依赖」约束。弃用。

## Risks / Trade-offs

- **[DB 每轮读的 I/O 开销]** → SQLite key-value 单行读取极廉价，采集器每轮仅 ~5 行；可再加 10s 内存缓存（TTL）进一步降低，但即时生效优先，默认直接读。
- **[手动扫描长耗时阻塞主循环]** → 与自动扫描同线程串行是刻意的（避免并发开会话）；期间 fast/slow tick 顺延，符合「扫描优先」语义；可通过 status.json 的 scan 状态让前端感知。
- **[settings 值非法导致采集器异常]** → 解析失败回退默认值 + Web 层校验双层防御，采集器永不因单条配置崩溃。
- **[未读消息被标记已读]** → 既有行为（scan_all_chats 本质），前端触发前弹确认提示明示。
- **[scan_requests 与 backfill_requests 并存]** → 两表职责不同（全量 vs 单会话），实现各自独立 drain；不合并避免耦合。

## Migration Plan

- 表结构在 SQLite 迁移（`sqlite_store.py` 的 migrate 逻辑）中 `CREATE TABLE IF NOT EXISTS settings / scan_requests`，无需手工操作。
- 升级期间旧 `status.json` 缺 `scan` 字段：前端容错 `(status.scan || {})`，无字段时不渲染进度区。
- 回滚：删除两表 + 前端退回旧样式即可，采集器核心逻辑无破坏性变更。

## Open Questions

无 —— 关键交互（后台执行+进度轮询、DB 持久化+即时生效、互斥策略）已在需求澄清时与用户确认。
```

## openspec/changes/collector-settings-center/tasks.md

- Source: openspec/changes/collector-settings-center/tasks.md
- Lines: 1-38
- SHA256: dafbeb875dfdb57215cb4b55fbb828c6223e1017cd362b8a91119bc295fd0d66

```md
# Tasks: 采集器设置中心

## 1. 存储层：settings 表与 scan_requests 表

- [ ] 1.1 在 `app/storage/sqlite_store.py` 迁移逻辑中新增 `settings` 表（key TEXT PRIMARY KEY, value TEXT, updated_at）与 `scan_requests` 表（id, requested_at, status, done, attempts）
- [ ] 1.2 新增 `RuntimeSettings` 读写层：`get(key, default)`（DB 有值取 DB，否则返回传入的 .env 默认）、`set(key, value)`、`reset(key)`、`all(defaults)`，并配套单元测试
- [ ] 1.3 `scan_requests` 的插入/查询 pending/标记 done 的存储方法，并配套单元测试

## 2. 采集器：手动扫描消费与运行时配置

- [ ] 2.1 改造 `scan_all_chats` 支持进度回调 `on_progress(current, total, ingested)`，每处理一个会话回调一次
- [ ] 2.2 `Scanner` 新增 `_drain_scan_requests()`：取 pending 请求 → 执行 scan_all_chats → 进度/结果写入 status.json；失败 attempts+1 不标 done
- [ ] 2.3 主循环 `run()` 接入 `_drain_scan_requests()`；执行期间设置 `last_scan=now` 跳过自动周期扫描，扫描完成重置；重复请求拒绝（已有 pending 时 Web 层拦截）
- [ ] 2.4 `Scanner` 每轮通过 `RuntimeSettings.get` 读取 fast_tick_sec / slow_tick_sec / auto_scan_interval_sec / auto_scan_max_chats / auto_scan_settle_sec / auto_scan_chats，替换直接读 settings 常量；解析失败回退默认值

## 3. Web API：设置读写与手动扫描触发

- [ ] 3.1 新增 `GET /api/settings`（返回各参数当前生效值 + 默认值）与 `POST /api/settings`（校验 + 保存 + 返回新值）与 `POST /api/settings/reset`（重置某参数为默认），含范围校验（间隔>0、max_chats 1..1000、settle 0.1..30、auto_scan 布尔），非法值返回可读错误
- [ ] 3.2 新增 `POST /api/collector/scan`（写 scan_requests；已有 pending 返回 busy）与扩展 `GET /api/collector/status` 返回 scan 进度字段（容错缺失）
- [ ] 3.3 Web API 层路由与现有 `/api/stats`、采集器状态接口对接，配套接口测试

## 4. 前端：设置中心页面与首页控制区

- [ ] 4.1 新增 `settings.html`：参数表单（间隔/扫描数/开关），保存/重置操作与即时反馈（成功/校验错误），纳入统一样式与导航
- [ ] 4.2 首页采集器状态区重构：连接状态、最近同步时间、「立即全量扫描」按钮（点击弹确认提示：会将未读标记为已读）与扫描进度条（已扫/总会话数、新入库消息数）
- [ ] 4.3 `app.js` 增加设置提交、手动扫描触发、进度轮询逻辑

## 5. 前端：全站视觉改版

- [ ] 5.1 重构 `app.css`：统一设计变量（色板/圆角/阴影/间距）、导航、卡片、按钮、表单、表格、徽章、空态、错误提示样式；移动端适配
- [ ] 5.2 全站模板（home/customers/chat/knowledge/search/cleanup + 新增 settings）套用统一版式：页面标题区、卡片布局、操作区对齐
- [ ] 5.3 `base.html` 导航升级（图标 + 标签，含「设置」入口），验证全部页面离线可用（本地静态资源）

## 6. 测试与验证

- [ ] 6.1 新增/更新单元测试：RuntimeSettings、scan_requests、settings API 校验、scan 互斥逻辑
- [ ] 6.2 手动验证：采集器运行中触发全量扫描 → 前端显示进度 → 完成后停止；改频次 → 采集器即时采用新值且重启保留；非法值被拒
- [ ] 6.3 全量回归：`compileall` + `pytest` 通过
```

## openspec/changes/collector-settings-center/specs/collector-settings/spec.md

- Source: openspec/changes/collector-settings-center/specs/collector-settings/spec.md
- Lines: 1-46
- SHA256: 9b6fae63161da62d87163164774902918fdc76281ff5858d598605d5b95f920f

```md
## Purpose

为采集器提供运行参数（同步频次与扫描参数）的持久化存储、即时生效与前端配置接口，使非技术用户无需编辑 .env 即可调优采集行为。

## ADDED Requirements

### Requirement: 采集器参数持久化与即时生效
系统 SHALL 将采集器运行参数持久化到本地数据库 settings 表，采集器主循环每次读取时以 DB 值覆盖 `.env` 默认值，保存后无需重启即生效。

#### Scenario: 参数保存后即时生效
- **WHEN** 用户通过 Web UI 修改采集器参数并保存
- **THEN** 系统 SHALL 立即持久化到 settings 表，采集器下一个轮询周期即采用新值

#### Scenario: 重启后保留配置
- **WHEN** 采集器进程重启
- **THEN** 系统 SHALL 从 settings 表恢复用户已配置的参数，未配置项回退 `.env` 默认值

#### Scenario: 未配置项使用默认值
- **WHEN** settings 表中某参数从未被用户配置
- **THEN** 系统 SHALL 使用 `.env` 中的对应默认值

### Requirement: 可配置参数范围与校验
系统 SHALL 支持配置的采集器参数包括：fast_tick_sec、slow_tick_sec、auto_scan_interval_sec、auto_scan_max_chats、auto_scan_settle_sec 与 auto_scan_chats 开关，并对数值参数进行范围校验。

#### Scenario: 接受合法配置
- **WHEN** 用户提交所有参数均在合法范围内
- **THEN** 系统 SHALL 接受并保存全部参数

#### Scenario: 拒绝非法值
- **WHEN** 用户提交的任一参数超出合法范围或为非数值
- **THEN** 系统 SHALL 拒绝保存并返回可读的校验错误，同时保持原配置不变

### Requirement: 前端配置接口
系统 SHALL 提供读写采集器参数的 HTTP 接口，供设置中心页面调用。

#### Scenario: 读取当前配置
- **WHEN** 设置中心页面加载
- **THEN** 系统 SHALL 返回各参数的当前生效值（DB 值或默认值）

#### Scenario: 更新配置
- **WHEN** 用户提交新参数值
- **THEN** 系统 SHALL 校验并保存，返回更新后的生效值

#### Scenario: 恢复默认值
- **WHEN** 用户请求恢复某参数为默认值
- **THEN** 系统 SHALL 删除该参数在 settings 表中的记录，使其回退到 `.env` 默认值
```

## openspec/changes/collector-settings-center/specs/web-app/spec.md

- Source: openspec/changes/collector-settings-center/specs/web-app/spec.md
- Lines: 1-44
- SHA256: e6c29401450e6306a0291de7f742799b1ecda7c1317a4010cfeb2d81dd1b0810

```md
# web-app Delta

## ADDED Requirements

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
```

## openspec/changes/collector-settings-center/specs/whatsapp-sync/manual-scan/spec.md

- Source: openspec/changes/collector-settings-center/specs/whatsapp-sync/manual-scan/spec.md
- Lines: 1-49
- SHA256: e545d21fc2743c6baa5e665379c59223abe15db13d5a8d936f622817ff86f0dc

```md
## Purpose

提供手动触发采集器全量扫描全部 WhatsApp 会话的能力与进度反馈，使业务员可主动发起首次或校准性质的完整知识构建，弥补仅靠自动周期扫描的被动性。

## ADDED Requirements

### Requirement: 手动触发全量扫描
系统 SHALL 支持用户在 Web UI 主动触发一次全量扫描，扫描所有会话（受单次会话数上限约束），逐个打开会话读取可见正文入库，并同步抓取会话头像。

#### Scenario: 从 Web UI 发起扫描
- **WHEN** 用户点击「立即全量扫描」按钮
- **THEN** 系统 SHALL 记录扫描请求，采集器在下一个轮询周期开始扫描全部会话，并返回请求已接受

#### Scenario: 扫描期间会话逐个处理
- **WHEN** 全量扫描进行中
- **THEN** 系统 SHALL 逐个打开会话采集可见正文并入库，同时尝试抓取各会话头像，失败会话静默跳过

#### Scenario: 扫描上限
- **WHEN** 会话总数超过单次扫描上限（auto_scan_max_chats）
- **THEN** 系统 SHALL 本次仅扫描前上限个会话，并如实反映进度

### Requirement: 扫描进度可见
系统 SHALL 提供扫描进度查询，前端轮询展示当前状态、已处理会话数与新入库消息数。

#### Scenario: 查询扫描进度
- **WHEN** 扫描进行中，用户停留在首页
- **THEN** 前端 SHALL 周期轮询进度接口，展示已扫/总会话数与新入库消息数

#### Scenario: 扫描完成
- **WHEN** 扫描结束
- **THEN** 系统 SHALL 展示完成状态，前端停止轮询并提示结果

### Requirement: 手动与自动扫描互斥
系统 SHALL 保证手动全量扫描执行期间不触发自动周期扫描，避免两个扫描同时打开会话造成冲突。

#### Scenario: 手动扫描期间跳过自动扫描
- **WHEN** 手动全量扫描进行中且到达自动扫描周期
- **THEN** 系统 SHALL 跳过本轮自动扫描，手动扫描结束后恢复正常周期

#### Scenario: 重复触发提示
- **WHEN** 已有扫描在进行时用户再次点击「立即全量扫描」
- **THEN** 系统 SHALL 拒绝重复请求并提示当前已有扫描进行中

### Requirement: 扫描前置提示
系统 SHALL 在触发全量扫描前提示用户该操作会逐个打开会话并将未读消息标记为已读。

#### Scenario: 展示风险提示
- **WHEN** 用户点击「立即全量扫描」
- **THEN** 系统 SHALL 先展示确认提示（含「会将未读标记为已读」说明），用户确认后才提交扫描请求
```

