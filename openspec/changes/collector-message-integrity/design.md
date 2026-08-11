# collector-message-integrity 技术设计

> 需求与范围见 proposal.md，行为契约见 specs/。本文只讲 HOW。

## Context

采集器双进程架构：采集器子进程经 `ReadOnlyCDP` 只读同步 WhatsApp（DOM 快照 + IDB walk），写入共享 SQLite（WAL+FTS5）+ Chroma。当前 `idb_walk.py` 跳过 `group-metadata` store，`dom_snapshot.py` 仅识别 `conv-msg-*` 文本行，chat `kind` 在 `scanner._upsert_one` 硬编码 `single`。`Message` 模型有 `sender_jid` 但无显示名；`build_chat_summary`（画像抽取输入）只按 `from_me` 标 `我/客户`。

## Goals / Non-Goals

**Goals:**
- 群聊消息入库（`kind=group` + 群名），发送者显示名随消息持久化
- 画像摘要按发送者标注群聊消息，单聊不变
- 引用回复正文净化、相册/媒体行入库、fromMe 多信号联合判断
- 全部为增量改造，既有单聊行为与测试不回归

**Non-Goals:**
- 不为群成员单独建客户/画像
- 不改 RAG/知识库/Wiki/回复链路
- 不做前端改版（仅聊天页发送者展示的局部改动）
- 不实现群聊消息的"按需回溯/全量扫描"新入口（复用既有 scan/backfill 机制）

## Decisions

### D1: group-metadata 读取与群聊识别
`idb_walk.walk_idb` 放开 `group-metadata` store，页面 JS 提取精简字段：群 JID（`_serialized`，`@g.us`）、群名、参与者列表（jid + 名字，尽力提取）。walk 结果新增 `groups: {g_jid: {name, members: {jid: name}}}`。chat 归属逻辑中 `chat` 以 `@g.us` 结尾 → `kind=group`，群名作为 chat display_name。

- **备选**：不做 group-metadata，仅靠 chats store 的 `@g.us` 名字 → 成员名解析只能依赖 contacts，群名缺失时无法兜底。放弃。

### D2: 消息发送者显示名持久化
`messages` 表新增 `sender_name TEXT`（幂等迁移：try/except OperationalError）。`scanner._merge_idb_dom` 解析入站消息发送者名字，优先级：contacts（按 LID/手机号）→ 群成员表 → DOM 发送人显示名 → 原始 JID 回退。`Message` dataclass 与 `sqlite_store.upsert_message` 同步扩展。

- **备选**：不落库，消费时实时 join contacts → 需在每处消费点重复解析，且 contacts 可能后来变化。落库快照更稳。

### D3: 画像摘要发送者标注
`build_chat_summary` 查 `chats.kind`：`group` 时以 `{sender_name}: {body}` 标注入站消息（我 仍为 `我:`），`single` 保持 `我/客户`。`list_messages` 返回的 Message 已含 `sender_name`，无需额外查询。

### D4: 引用回复净化
`dom_snapshot._parse_row` 遍历子树时跳过引用容器（`data-testid` 含 `message-quote`/`quoted-` 的元素），`selectable-text` 收集仅限非引用子树。引用块检测用 testid 前缀匹配，版本漂移时静默回退到当前行为（收集全部文本），保证不倒退。

### D5: 相册/媒体行入库
`parse_dom_snapshot` 除 `conv-msg-*` 外识别媒体行 testid（`image-album-*`、`image-*`、`video-*`、`ptt-*`、`document-*`、`audio-*`、`location-*` 等），解析其说明文字（行内 `selectable-text`）作 body；无正文则以媒体标记（如 `[图片]`/`[相册]`/`[文档]`）作为 body 占位，`type` 记为媒体类型。fromMe/时间仍由 tail + pre-plain-text 信号复用。

- **备选**：仅记录 `type` 无 body → 画像摘要与 RAG 完全看不到媒体存在。放弃。

### D6: fromMe 多信号判断
`_merge_idb_dom` 已用 `rec.from == our_jid` 推导 fromMe；保留该逻辑并提升为权威（IDB 元数据可用时覆盖 DOM tail 信号）。DOM-only 路径（fast_tick）仍用 tail-in/out。`dom_snapshot` 保持输出 DOM 信号，冲突仲裁集中在 merger/scanner 合并层。

### D7: 迁移幂等与向后兼容
所有 schema 变更走 `try/except OperationalError` 幂等迁移；`sender_name` 缺省 `NULL`，旧库无该列时 upsert SQL 用动态检测列存在与否（或直接迁移后统一）。旧测试数据路径（FakeStore）补 `sender_name` 属性默认 `None`。

## Risks / Trade-offs

- **[group-metadata 字段结构未实测]** → 页面 JS 提取用防御式读取（`_serialized`/`user`/字符串兜底），单测用合成结构覆盖映射逻辑；实测在 build 阶段用真实 WhatsApp 校验，失败仅群成员名缺失（回退 JID），不阻塞入库
- **[引用块 testid 版本漂移]** → 前缀匹配 + 静默降级到原行为，不影响既有消息采集
- **[媒体行识别误伤]** → 仅追加媒体 testid 白名单，未知 testid 维持忽略；白名单可在 config 调整
- **[画像摘要变更影响 LLM 输出]** → 单聊路径完全不变，群聊为新场景；加单测断言标注格式
- **[迁移列新增]** → 幂等 + 全量测试回归（当前 102 passed）

## Migration Plan

1. schema.sql 增加 `sender_name`；`sqlite_store` 迁移块幂等补列
2. 新功能走 feature 分支，build 阶段逐任务提交
3. 回归：`pytest -q` 全量；`compileall -q app`
4. 回滚：`kind`/`sender_name` 为增量字段，无破坏性变更，可安全回退

## Open Questions

- group-metadata store 的真实记录结构（群成员字段名）——build 阶段实测确认，不影响 spec 与任务拆分
