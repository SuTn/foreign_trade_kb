---
role: technical-design
canonical_spec: none
status: final
---

# Design: WhatsApp 双向文字收发 + 实时会话列表

## Context

现状：采集器 (`app/collector`) 通过 Playwright + `ReadOnlyCDP` 只读抓取 WhatsApp Web 的 DOM 快照与 IndexedDB，写入 SQLite；Web (`app/web`) 是 FastAPI + Jinja2 + HTMX 的三栏工作台，`workspace_chat.html` 展示聊天、`/api/reply` 生成建议回复（仅生成不发送），前端 5 秒轮询增量拉取 (`workspace_chat_poll`)。

目标：个人外贸业务员自用，把工作台升级为**双向文字聊天客户端** —— 网页上实时看到新消息（含左栏会话列表的未读红点 + 最后一句），并能直接在网页输入框发送纯文字消息。扫码登录保持现状（采集器弹出的 Chrome 窗口扫码，`user-data-dir` 持久登录）。

## Goals / Non-Goals

**Goals:**
- 发送纯文字消息到指定会话（`send_requests` 意图表 + 采集器执行），全程经过「发送开关 + 发送前确认」双重把关
- 网页点开某客户 → 采集器切到该会话 → 新消息 2~3 秒内可见（跟随会话）
- 左栏会话列表实时监控：未读红点 + 未读数 + 最后一句预览，任何客户来消息都能秒级察觉，点进去读全文
- 发送开关 `send_enabled` 持久化，默认 `false`（只读），Web 层隐藏/拒绝 + 采集器层二次把关
- 现有只读能力完全保留，关闭开关时行为与当前完全一致

**Non-Goals:**
- 不发图片/语音/文档（媒体发送需操纵文件选择框，易碎，v2 再说）
- 不做网页内嵌扫码二维码（保持 Chrome 窗口扫码）
- 不做多账号
- 不做 SSE/WebSocket 推送（2~3 秒轮询对聊天够用，YAGNI）
- 不替换现有「生成建议回复」能力，仅在建议旁新增「直接发送」入口

## Decisions

### D1: Web ↔ 采集器继续用 SQLite 意图表（不新增 HTTP 端口）
新增 `send_requests` 表 + 采集器轮询消费，与 `backfill_requests` / `scan_requests` 同构。
- 备选：采集器开本地 HTTP 端口接受发送指令 —— 多一个端口 + 鉴权面 + server 代码，复杂度和故障面都变大。弃用。
- 取舍：意图表有约 2 秒轮询延迟（发送/跟随均如此），聊天场景可接受。

### D2: 发送链路
`schema.sql` 新增 `send_requests` 表：`id INTEGER PK AUTOINCREMENT, chat_id TEXT, text TEXT, status TEXT('pending'|'running'|'done'|'failed'), attempts INTEGER DEFAULT 0, error TEXT, requested_at INTEGER, updated_at INTEGER`；旧库经 `MIGRATIONS` 加 version 8 迁移。
`SqliteStore` 新增 `create_send_request / next_pending_send_request / mark_send_request_running / mark_send_request_done / bump_send_request_attempts`（照抄 scan_requests 语义，attempts<3 才可重试）。
采集器 `Scanner` 新增 `_drain_send_requests()`，每轮 `run()` 调用：取一条 pending → 校验 `send_enabled` → `open_chat(chat_id)` 切会话 → `sender.send_text(page, text)` → 标 done；失败 attempts+1，满 3 次标 failed + error。
发送执行独立成 `app/collector/sender.py`：`async def send_text(page, text)` 用 WhatsApp Web 输入框/发送按钮选择器写入并点击；选择器集中为一个常量/模块，便于 WhatsApp 改版时单点修补。
Web 新增 `POST /api/send`（body: `{chat_id, text}`，创建任务返回 task_id）+ `GET /api/send/status/{task_id}`（pending/running → 处理中；done → 成功；failed → 错误），复用 reply/summary 的轮询 UI 模式。

### D3: 发送开关 `send_enabled`（持久化，默认 false）
加入 `RuntimeSettings.DEFAULTS`（默认 `False`）+ `SETTING_VALIDATORS`（bool）。设置页遍历 DEFAULTS 渲染，自动出现「发送功能」复选框。
双重把关：Web 层 `send_enabled=false` 时前端隐藏发送 UI、`/api/send` 返回 403；采集器层 `_drain_send_requests` 消费前再次读取 `send_enabled`，false 则跳过并直接标 failed（防绕过）。
持久化：用户开了就一直开着，不随重启重置（用户已确认）。

### D4: 跟随会话（网页点开 → 采集器切换）
Web 在 `workspace_chat` 路由里把 `follow_chat`（=chat_id）写入 settings 表（key 如 `collector_follow_chat`）；切换/离开时更新或清空。
采集器每轮读 `follow_chat`：与 `_current_chat_id` 不同则调 `open_chat(chat_id)` —— 通过聊天列表**搜索框**（按显示名/手机号搜索，点第一个匹配结果）切过去，成功后更新 `_current_chat_id`。跟随失败仅记日志，不崩溃，下轮重试。
切换后现有 `fast_tick`（抓当前会话 DOM）自然入库新消息；跟随模式下 `fast_tick` 间隔压到约 1 秒。

### D5: 会话列表监控（未读红点 + 最后一句，不打开会话）
`fast_tick` 的 DOM 快照本就含整页（左列表 + 右会话）。新增解析：从左栏会话列表提取「会话 → 未读计数 + 最后一条消息预览 + 时间戳」，写入新表 `chat_previews(chat_id TEXT PK, unread_count INTEGER, preview TEXT, preview_ts INTEGER, updated_at INTEGER)`。该表是「实时未读/预览」的权威来源，与现有基于已入库消息的 `get_customer_recent_activity` unread 口径互补（前者秒级反映 WhatsApp 自身红点，后者反映已入库未读）。
Web 左栏客户列表据此显示红点 + 未读数 + 最后一句，前端缩短轮询到约 1 秒。用户点红点 → 走 D4 跟随 → 读到全文。
`auto_scan` 保留并缩短周期到 3~5 分钟，兜底回填未点开会话的历史正文（供搜索/画像/摘要），不依赖手动点开。

### D6: 前端
`workspace_chat.html` 底部加：输入框 + 「发送」按钮 + 发送前确认弹窗（自定义 modal：确认发送给 X 的文本）。
`reply_result.html` 建议卡片旁，在「复制」之外加「直接发送」按钮（同样过确认 + 开关）。
`send_enabled=false` 时：不渲染输入框/发送按钮，仅保留「生成/复制」。
发送流：`POST /api/send` → 轮询 `/api/send/status/{id}` → 成功提示已发送、失败展示错误 + 可重试。
聊天增量轮询间隔从 5 秒降到约 1 秒（仅对当前打开会话）。

### D7: 群聊与错误处理
群聊文字发送与单聊同链路（`open_chat` 切到群会话 → `send_text`），不额外区分。
未登录 / 搜索无结果 / 选择器失效 → 发送失败并回传错误，3 次重试后 failed，可手动重发。
采集器串行消费发送任务，天然避免并发乱序。

## Risks / Trade-offs

- **[封号风险上升]**：主动发送比只读更易被判自动化。`send_enabled` 默认关 + 发送前确认降低误触，但根本风险无法消除；`docs/RISK.md` 补一句发送风险提示，建议小号试跑。
- **[WhatsApp DOM 选择器易变]**：输入框/发送按钮/搜索框选择器集中在 `sender.py` 单点，随改版修补。
- **[轮询而非推送]**：新消息稳态 2~3 秒延迟，聊天体感可接受；非当前会话靠左栏列表秒级提示，正文靠 auto_scan 兜底。
- **[跟随会占用 Chrome 焦点]**：Chrome 窗口会随网页切换会话，用户不能再同时手动操作该窗口（接受：Chrome 当后台引擎，只操作网页）。

## Migration Plan

- `send_requests` 表：`schema.sql` 加建表 + `MIGRATIONS` 加 version 8（旧库补表）。
- `chat_previews` 表：`schema.sql` 加建表 + `MIGRATIONS` 加 version 9（旧库补表）。
- 回滚：关闭 `send_enabled` 即回到只读；删除发送相关路由/表/`sender.py` 不影像既有功能。

## Open Questions

无 —— 发送范围（纯文字）、扫码方式（Chrome 窗口）、实时方案（跟随 + 会话列表监控）、安全（开关持久化 + 发送前确认）、别的客户消息呈现（左栏红点 + 最后一句）均已在探索阶段与用户确认。
