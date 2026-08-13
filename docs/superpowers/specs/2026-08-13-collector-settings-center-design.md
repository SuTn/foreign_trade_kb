---
comet_change: collector-settings-center
role: technical-design
canonical_spec: openspec
---

# 采集器设置中心 — 技术设计

## 1. 目标与范围

见 `openspec/changes/collector-settings-center/proposal.md`（Why/What）。本文档给出可落地实现细节，规范见 delta specs：`collector-settings`、`whatsapp-sync/manual-scan`、`web-app`。

范围边界：只涉及采集器参数（fast/slow tick 与自动扫描参数）与全量扫描控制；不涉及 LLM/嵌入/RAG 配置热更新，不引入鉴权。

## 2. 架构概览

沿用既有双进程架构，全部通过 SQLite 共享状态，无新增进程间通信：

```
Web 进程 (FastAPI)                       采集器进程 (Scanner.run 主循环)
┌─────────────────────┐                 ┌──────────────────────────────┐
│ /settings 页面       │   settings 表    │ 每轮 RuntimeSettings.get()    │
│ GET/POST /api/      │ ───────────────► │ 覆盖 .env 默认值，即时生效    │
│ settings            │                 │                              │
│                     │  scan_requests  │ _drain_scan_requests()       │
│ POST /api/collector │ ───────────────► │  消费请求 → scan_all_chats()  │
│ /scan               │                 │  进度回调 → status.json       │
│                     │                 │                              │
│ GET /api/collector/ │ ◄────────────── │ status.json {scan:{...}}     │
│ status (已轮询)      │                 │                              │
└─────────────────────┘                 └──────────────────────────────┘
```

## 3. 数据模型

### 3.1 `settings` 表（新增）

```sql
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at INTEGER);
```

- `key` 使用 config 字段名下划线形式（`fast_tick_sec`、`slow_tick_sec`、`auto_scan_interval_sec`、`auto_scan_max_chats`、`auto_scan_settle_sec`、`auto_scan_chats`）
- 仅存储**用户显式配置过**的项；未配置项 DB 无行，回退 `.env` 默认值
- 加入 `app/storage/schema.sql`（新库直接建表）+ `SqliteStore._init_schema()` 幂等迁移（旧库）

### 3.2 `scan_requests` 表（新增）

```sql
CREATE TABLE IF NOT EXISTS scan_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  requested_at INTEGER,
  status TEXT DEFAULT 'pending',   -- pending | running | done | failed
  attempts INTEGER DEFAULT 0,
  done INTEGER DEFAULT 0);
```

- 一次一条请求；`status` 由采集器更新
- 不存 chat_id（全量扫描无参数）；与 `backfill_requests`（单会话）职责分离

## 4. 存储层：RuntimeSettings（新增模块）

`app/storage/runtime_settings.py`：

```python
class RuntimeSettings:
    DEFAULTS = {
        "fast_tick_sec": settings.fast_tick_sec,        # 2.0
        "slow_tick_sec": settings.slow_tick_sec,        # 30.0
        "auto_scan_interval_sec": settings.auto_scan_interval_sec,  # 600.0
        "auto_scan_max_chats": settings.auto_scan_max_chats,       # 100
        "auto_scan_settle_sec": settings.auto_scan_settle_sec,     # 1.5
        "auto_scan_chats": settings.auto_scan_chats,              # True
    }

    def get(self, key, default=None) -> str | None   # DB 值；无行返回 default（None 时用 DEFAULTS）
    def set(self, key, value) -> None                # UPSERT；value 存字符串
    def reset(self, key) -> None                     # DELETE 行，恢复 .env 默认
    def all(self) -> dict[str, str]                  # DB 全部键值（不含默认，供合并展示）
    def get_typed(self, key, default):               # 按 DEFAULTS 类型转换；解析失败回退 default
```

- 使用独立短生命周期连接还是复用？—— **复用 SqliteStore 连接**（`store.conn`）。采集器已有 store；Web 层经 `_store(request)` 访问。并发安全同现有模式（WAL + busy_timeout）。
- **解析失败回退默认值**：`get_typed` 内 `float()`/`int()`/`bool()` 转换，异常即返回 default —— 保证单条脏数据不搞崩采集器。

## 5. 采集器改造（scanner.py）

### 5.1 运行时配置读取

`Scanner.__init__` 新增 `self._rt = RuntimeSettings(store)`。主循环 `run()` 每轮：

```python
# 每轮刷新（即时生效）
self._rt.refresh()   # 内部缓存 dict，执行一次 SELECT key,value FROM settings
fast_tick = self._rt.get_typed("fast_tick_sec", settings.fast_tick_sec)
```

对应替换点：
- `run()` 尾部 `await asyncio.sleep(...)` → `settings.fast_tick_sec` 改为运行时值
- `slow_tick` 触发阈值 → 运行时 `slow_tick_sec`
- 自动扫描分支条件 `settings.auto_scan_chats` → 运行时值；`scan_all_chats()` 内部 `max_chats`/`settle` → 运行时值；周期判断 `last_scan` 阈值 → 运行时 `auto_scan_interval_sec`

### 5.2 手动扫描消费

新增 `_drain_scan_requests()`（与 `_drain_backfill_requests` 并列，主循环每轮调用）：

```python
async def _drain_scan_requests(self):
    rows = store 查 scan_requests WHERE done=0 AND attempts<3 ORDER BY id LIMIT 1
    if not rows: return
    self._manual_scan_active = True
    self.last_scan = time.time()          # 覆盖周期判断，跳过本轮自动扫描
    store 标记该行 status=running
    try:
        total = 从 chat-list 取会话总数
        def on_progress(current, ingested):
            write_status(status_path, {"state":"running",
                "scan":{"running":True,"current":current,"total":total,"ingested":ingested}})
        ingested = await scan_all_chats(max_chats=运行时 max, settle=运行时 settle, on_progress=on_progress)
        write_status(..., {"state":"running","last_sync":now,"scan":{"running":False,"done":True,"ingested":ingested,"finished_at":now}})
        store 标记 done=1, status=done
    except Exception as e:
        attempts+1; status=failed（attempts<3 时下一轮重试）
    finally:
        self._manual_scan_active = False
```

### 5.3 `scan_all_chats` 进度回调改造

签名 `scan_all_chats(self, max_chats=None, settle=None, on_progress=None)`。循环内每处理一个会话：

```python
if on_progress: on_progress(i + 1, min(total, max_chats), ingested)
```

`total` 在入口已获取（`eval_on_selector_all` 计数），扫描前写入 status.json 一次（total 已知）。

### 5.4 互斥细节

- 主循环串行：`_drain_scan_requests` 阻塞执行 `scan_all_chats`，期间不会进入自动扫描分支
- `self._manual_scan_active` 标志：Web 侧 busy 判断不依赖该标志（Web 是另一进程），busy 由 scan_requests 表 pending 行判断；标志仅用于采集器内部（可选防御）
- 自动扫描分支额外判断：`if not self._manual_scan_active and ...`（双保险）

## 6. Web API（routes.py）

### 6.1 设置读写

```python
@router.get("/api/settings")
# 返回 {"values": {key: 当前生效值}, "defaults": {key: .env 默认}}
# 当前生效值 = DB 值 or DEFAULTS；类型由 DEFAULTS 类型决定

@router.post("/api/settings")
# body: {"values": {key: value}}；逐项校验；全通过才写库（原子）
# 校验: 数值参数 float>0；max_chats int 1..1000；settle 0.1..30；auto_scan_chats 布尔
# 非法 → 400 {"error": "字段名: 提示", "field": key}
# 成功 → 200 {"values": 新生效值}

@router.post("/api/settings/reset")
# body: {"key": "fast_tick_sec"}；删除该行；返回默认值
```

### 6.2 手动扫描

```python
@router.post("/api/collector/scan")
# 检查 scan_requests 是否已有 pending/running 未完成行 → 有则 409 {"busy": true, "error": "已有扫描进行中"}
# 否则插入新行，返回 {"accepted": true}
# 采集器离线不拦截（意图表排队语义）
```

### 6.3 status 扩展

`GET /api/collector/status` 响应加 `scan` 字段（直接透传 status.json 的 scan 对象，缺失时 `scan: null`）：

```json
{"status": {...}, "alive": true, "scan": {"running": true, "current": 5, "total": 40, "ingested": 120}}
```

首页 hx-get 已每 5s 轮询此端点，前端 JS 直接渲染进度，无需新增轮询。

## 7. 前端

### 7.1 导航（base.html）

```
首页  客户  知识库  搜索  清理  设置
```

### 7.2 首页状态控制区（home.html）

- 状态卡：连接状态、最近同步时间、当前状态
- 「立即全量扫描」按钮 → `confirm()` 提示"将逐个打开会话并把未读标记为已读，确定继续？" → `POST /api/collector/scan`
  - `busy` 响应 → 提示"已有扫描进行中"
  - `accepted` → 显示进度区（依赖已有 status 轮询展示）
- 进度区：`已扫 current/total 会话 · 新入库 ingested 条`；running 时显示，done 后保留 + 完成时间，前端重置扫描态

### 7.3 设置页（settings.html）

- 分组：同步频次（fast_tick / slow_tick）、自动扫描（interval / max_chats / settle / 开关）
- 每项展示当前生效值 + 「恢复默认」按钮（`POST /api/settings/reset`）
- 「保存」按钮 → `POST /api/settings`；成功提示 + 刷新显示生效值；400 展示字段级错误
- 加载时 `GET /api/settings` 填充表单

### 7.4 视觉改版

- 扩展 `app.css` CSS 变量：色板/圆角/阴影/间距 token；组件样式收敛（nav、card、btn、form、table、tag、empty、status-pill）
- 全站模板（home/customers/chat/knowledge/search/cleanup/settings）统一页面标题区、卡片布局、操作区
- 移动端：现有 768px 断点扩展
- 不引入新依赖，本地静态资源（htmx 已本地化）

## 8. 错误处理

| 场景 | 处理 |
|------|------|
| settings 值脏/解析失败 | `get_typed` 回退默认值，采集器不崩 |
| Web 提交非法值 | 400 + 字段级可读错误，不写库 |
| 重复触发扫描 | 409 busy + 提示 |
| 扫描中途异常 | attempts+1（<3 下轮重试），status=failed |
| 采集器离线时扫描 | 请求排队（表行），恢复后执行；前端提示排队 |
| status.json 无 scan 字段（旧版本） | 前端 `(scan || {})` 容错 |

## 9. 测试策略

**单元**（`tests/`）：
- `RuntimeSettings`: set/get/reset/all、类型转换、脏数据回退默认、未配置项返回默认
- scan_requests 存储：插入/pending 查询/标记 done/attempts 递增
- 校验函数：边界值（0、负、超上限、非数值、非布尔）

**接口**（TestClient）：
- GET/POST /api/settings、POST /api/settings/reset：成功/非法值/未知 key
- POST /api/collector/scan：首请求 accepted、重复 409
- GET /api/collector/status：含 scan 字段，缺失容错

**采集器逻辑**（mock CDP）：
- `_drain_scan_requests`：消费请求→回调进度→done；异常→attempts
- 扫描期间 `last_scan` 被设置（跳过自动扫描）

**手动验证**：
- 运行中触发扫描 → 前端进度推进 → 完成
- 改频次 → 采集器即时采用（观察日志/行为）→ 重启保留
- 非法值被拒，提示可见

## 10. 风险

| 风险 | 缓解 |
|------|------|
| 主循环每轮 SELECT settings 开销 | 单行 key-value，~6 行，SQLite 本地读，代价可忽略 |
| 手动扫描长时间阻塞 fast/slow tick | 刻意的扫描优先语义；status.json 展示状态 |
| scan 请求无限重试 | attempts<3 上限，超过标 failed |
| 前端改版破坏既有交互 | 保留 htmx + 类名兼容策略，逐页改版 |
| 双表（backfill/scan）语义混淆 | 职责分离：backfill=单会话回溯，scan=全量；各自 drain |
