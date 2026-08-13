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
