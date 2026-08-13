# Brainstorm Summary

- Change: collector-settings-center
- Date: 2026-08-13

## 确认的技术方案

### 1. 手动全量扫描（复用 backfill 意图表模式）
- 新增 `scan_requests` 表：`(id, requested_at, status, done, attempts)`
- Web `POST /api/collector/scan` 写入请求 → 采集器主循环 `_drain_scan_requests()` 消费
- 进度写入 status.json `scan` 字段（running/current/total/ingested/finished_at）
- `scan_all_chats()` 改造：加进度回调 `on_progress(current, total, ingested)`
- **采集器离线时请求仍写入表排队，恢复后自动消费**（用户已确认）；前端提示"请求已排队"

### 2. 参数持久化 settings 表 + 即时生效
- `settings` 表：`(key TEXT PK, value TEXT, updated_at)`
- `RuntimeSettings` 读写层：`get(key, default)` / `set` / `reset` / `all`
- 采集器主循环每轮读 DB 覆盖 `.env` 默认值，即时生效，不做 TTL 缓存
- 可配参数：fast_tick_sec / slow_tick_sec / auto_scan_interval_sec / auto_scan_max_chats / auto_scan_settle_sec / auto_scan_chats
- Web 层做范围校验（间隔>0、max_chats 1..1000、settle 0.1..30、auto_scan 布尔），非法值 400 + 可读错误

### 3. 手动/自动扫描互斥
- 采集器主循环内 `scan_all_chats` 为阻塞调用，天然同线程互斥
- 手动扫描执行时设置 `last_scan=now`，自动周期分支自然跳过
- Web 层重复触发拦截：有未完成请求返回 `{busy: true}`

### 4. 前端改版
- **独立设置页 `/settings`**（用户已确认）：导航新增「设置」入口，参数表单 + 保存/重置 + 即时反馈
- 首页采集器状态区：连接状态、最近同步、「立即全量扫描」按钮（确认提示：未读会标记为已读）+ 扫描进度
- 进度数据源：扩展 `GET /api/collector/status` 加 `scan` 字段（首页已每 5s 轮询，零新增请求）
- 简约清爽风格，CSS 变量体系演进，不引入框架；全站 7 个模板统一版式

## 关键取舍与风险

- [主循环每轮读 DB 的 I/O] → SQLite 单行读取廉价，仅 ~6 行；即时生效优先，不做缓存
- [手动扫描长耗时阻塞主循环] → 与自动扫描同线程串行是刻意的（避免并发开会话）；期间 fast/slow tick 顺延
- [settings 值非法致采集器异常] → Web 校验 + 采集器解析失败回退默认值双层防御
- [未读消息被标记已读] → 既有行为，前端触发前弹确认提示明示
- [scan 请求离线排队] → 意图表模式天然支持；前端提示排队不报错

## 测试策略

- 单元：RuntimeSettings 读写/重置、scan_requests 存储、settings API 校验、scan 互斥逻辑
- 接口：GET/POST /api/settings、POST /api/collector/scan、status 扩展字段
- 手动验证：运行中触发扫描→进度→完成；改频次→即时生效→重启保留；非法值被拒
- 回归：compileall + pytest 全绿

## Spec Patch

无 —— 需求澄清与规范已充分覆盖上述决策。
