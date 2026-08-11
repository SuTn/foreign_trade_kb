---
comet_change: collector-message-integrity
role: technical-design
canonical_spec: openspec
---

# collector-message-integrity 技术设计

> 需求与范围见 `openspec/changes/collector-message-integrity/proposal.md`，行为契约见其 `specs/*/spec.md`。本文只讲 HOW。

## 1. 背景与约束

采集器双进程架构：采集器子进程经 `ReadOnlyCDP` 只读同步 WhatsApp（DOM 快照 + IDB walk），写入共享 SQLite（WAL+FTS5）+ Chroma。当前缺口：`idb_walk` 跳过 `group-metadata` store；`dom_snapshot` 仅识别 `conv-msg-*` 文本行；chat `kind` 在 `_upsert_one` 硬编码 `single`；`Message` 有 `sender_jid` 无显示名；`build_chat_summary` 只按 `from_me` 标 `我/客户`。硬约束：采集器只读、本地优先、既有单聊行为不回归。

## 2. 模块与数据流

```
IDB walk (idb_walk.py)
  └─ 放开 group-metadata → groups{j_gid: {name, members{jid:name}}}
DOM 解析 (dom_snapshot.py)
  ├─ 引用容器排除 (testid message-quote/quoted-)
  ├─ 媒体行白名单 → 说明文字 / 媒体标记
  └─ tail-in/out → fromMe (DOM 信号)
merger/scanner (merger.py / scanner.py)
  ├─ chat @g.us → kind=group; chat_id=群JID
  ├─ 发送者解析: contacts(LID/phone) → 群成员表 → DOM → JID 回退 → sender_name
  └─ fromMe: IDB 权威覆盖 DOM
SQLite
  ├─ messages.sender_name (新增列, 幂等迁移)
  └─ chats.kind=group / display_name=群名
消费
  ├─ profile/service.build_chat_summary → 群聊按「成员名: 正文」标注
  └─ web chat_messages.html → 群聊气泡显示发送者
```

## 3. 详细设计

### 3.1 群聊识别与元数据（`idb_walk.py`）

放开 `group-metadata` store，页面 JS 防御式提取精简字段（群 JID `_serialized`/`user`/字符串兜底、群名、参与者 jid+name），walk 结果新增 `groups: {g_jid: {name, members}}`。单测用合成结构覆盖映射；store 缺失/异常静默降级为 `{}`。

### 3.2 发送者显示名解析与入库（`scanner.py` / `merger.py` / `sqlite_store.py`）

`messages` 表新增 `sender_name TEXT`，迁移走 `try/except OperationalError` 幂等补列。`_merge_idb_dom` 解析入站发送者名字，优先级：`contacts`（按 LID/手机号 JID）→ 群成员表 → DOM 发送人显示名 → 原始 JID 回退。`_upsert_one` 按 `@g.us` 判定 kind 写入 `chats`（群名作 display_name），`Message` 构造携带 `sender_name`。

### 3.3 画像摘要发送者标注（`profile/service.py`）

`build_chat_summary` 查 `chats.kind`：`group` 时入站消息以 `{sender_name}: {body}` 标注（我方仍 `我:`），`single` 保持 `我/客户`。`Message` 已含 `sender_name`，无需额外 join。

### 3.4 引用回复净化（`dom_snapshot.py`）

`_parse_row` 遍历子树时跳过引用容器（`data-testid` 含 `message-quote`/`quoted-` 前缀），`selectable-text` 收集仅限非引用子树。testid 漂移时静默回退到收集全部文本，保证不倒退。

### 3.5 媒体消息入库（`dom_snapshot.py`）

媒体行 testid 白名单（`image-album-*`、`image-*`、`video-*`、`ptt-*`、`document-*`、`audio-*`、`location-*`）识别并入库：行内 `selectable-text` 作 body；无正文以媒体标记（`[图片]`/`[相册]`/`[文档]` 等）占位，`type` 记媒体类型。fromMe/时间复用 tail + pre-plain-text 信号。白名单可配置。

### 3.6 fromMe 多信号仲裁（`scanner.py` / `merger.py`）

IDB 元数据可用时（`rec.from == our_jid`）为权威，覆盖 DOM tail 信号；DOM-only 路径（fast_tick）保持 tail-in/out。仲裁集中在 merger/scanner 合并层，`dom_snapshot` 保持输出 DOM 信号不变。

### 3.7 Web 发送者展示（`chat_messages.html`）

群聊会话（`chats.kind=group`）消息气泡在 meta 区显示发送者名（`成员名 · 时间`），单聊保持 `我/客户 · 时间`。

## 4. 测试策略

- 数据层：迁移幂等（旧库补列不报错）、`sender_name` 往返一致
- IDB walk：合成 group-metadata → `groups` 映射；store 缺失静默降级
- 群聊入库：kind=group、sender_name 正确、成员缺失回退 JID、单聊不回归
- DOM 净化：引用 body 排除引用文本、媒体行说明文字/标记、fromMe IDB 优先
- 摘要：群聊按发送者标注、单聊格式不变
- Web：群聊页渲染发送者名
- 回归：全量 `pytest -q`（102→预计 110+）+ `compileall -q app tests`

## 5. 取舍与风险

| 风险 | 缓解 |
|------|------|
| group-metadata 字段结构未实测 | 防御式读取 + 合成单测；实测失败仅成员名缺失（回退 JID），不阻塞入库 |
| 引用块 testid 版本漂移 | 前缀匹配 + 静默降级到收集全部文本 |
| 媒体行识别误伤 | 白名单 + 可配置；未知 testid 保持忽略 |
| 画像摘要变更影响 LLM 输出 | 单聊路径完全不变；群聊为新场景，单测断言标注格式 |
| 迁移列新增 | 幂等 + 全量回归 |
