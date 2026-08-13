# Tasks: 采集器设置中心

## 1. 存储层：settings 表与 scan_requests 表

- [x] 1.1 在 `app/storage/sqlite_store.py` 迁移逻辑中新增 `settings` 表（key TEXT PRIMARY KEY, value TEXT, updated_at）与 `scan_requests` 表（id, requested_at, status, done, attempts）
- [x] 1.2 新增 `RuntimeSettings` 读写层：`get(key, default)`（DB 有值取 DB，否则返回传入的 .env 默认）、`set(key, value)`、`reset(key)`、`all(defaults)`，并配套单元测试
- [x] 1.3 `scan_requests` 的插入/查询 pending/标记 done 的存储方法，并配套单元测试

## 2. 采集器：手动扫描消费与运行时配置

- [x] 2.1 改造 `scan_all_chats` 支持进度回调 `on_progress(current, total, ingested)`，每处理一个会话回调一次
- [x] 2.2 `Scanner` 新增 `_drain_scan_requests()`：取 pending 请求 → 执行 scan_all_chats → 进度/结果写入 status.json；失败 attempts+1 不标 done
- [x] 2.3 主循环 `run()` 接入 `_drain_scan_requests()`；执行期间设置 `last_scan=now` 跳过自动周期扫描，扫描完成重置；重复请求拒绝（已有 pending 时 Web 层拦截）
- [x] 2.4 `Scanner` 每轮通过 `RuntimeSettings.get` 读取 fast_tick_sec / slow_tick_sec / auto_scan_interval_sec / auto_scan_max_chats / auto_scan_settle_sec / auto_scan_chats，替换直接读 settings 常量；解析失败回退默认值

## 3. Web API：设置读写与手动扫描触发

- [x] 3.1 新增 `GET /api/settings`（返回各参数当前生效值 + 默认值）与 `POST /api/settings`（校验 + 保存 + 返回新值）与 `POST /api/settings/reset`（重置某参数为默认），含范围校验（间隔>0、max_chats 1..1000、settle 0.1..30、auto_scan 布尔），非法值返回可读错误
- [x] 3.2 新增 `POST /api/collector/scan`（写 scan_requests；已有 pending 返回 busy）与扩展 `GET /api/collector/status` 返回 scan 进度字段（容错缺失）
- [x] 3.3 Web API 层路由与现有 `/api/stats`、采集器状态接口对接，配套接口测试

## 4. 前端：设置中心页面与首页控制区

- [x] 4.1 新增 `settings.html`：参数表单（间隔/扫描数/开关），保存/重置操作与即时反馈（成功/校验错误），纳入统一样式与导航
- [x] 4.2 首页采集器状态区重构：连接状态、最近同步时间、「立即全量扫描」按钮（点击弹确认提示：会将未读标记为已读）与扫描进度条（已扫/总会话数、新入库消息数）
- [x] 4.3 `app.js` 增加设置提交、手动扫描触发、进度轮询逻辑

## 5. 前端：全站视觉改版

- [x] 5.1 重构 `app.css`：统一设计变量（色板/圆角/阴影/间距）、导航、卡片、按钮、表单、表格、徽章、空态、错误提示样式；移动端适配
- [x] 5.2 全站模板（home/customers/chat/knowledge/search/cleanup + 新增 settings）套用统一版式：页面标题区、卡片布局、操作区对齐
- [x] 5.3 `base.html` 导航升级（图标 + 标签，含「设置」入口），验证全部页面离线可用（本地静态资源）

## 6. 测试与验证

- [x] 6.1 新增/更新单元测试：RuntimeSettings、scan_requests、settings API 校验、scan 互斥逻辑
- [x] 6.2 手动验证：采集器运行中触发全量扫描 → 前端显示进度 → 完成后停止；改频次 → 采集器即时采用新值且重启保留；非法值被拒
- [x] 6.3 全量回归：`compileall` + `pytest` 通过
