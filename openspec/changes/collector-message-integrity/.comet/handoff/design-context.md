# Comet Design Handoff

- Change: collector-message-integrity
- Phase: design
- Mode: compact
- Context hash: a9f16dbe094f31f9cadfbbe36bb27aa4b9dc3a8035c9c1a0d3ba69ead1b3645c

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/collector-message-integrity/proposal.md

- Source: openspec/changes/collector-message-integrity/proposal.md
- Lines: 1-36
- SHA256: def0df3a4d247b227a455e50879e3255f0805487e98e5fdc40b0332f3273012e

```md
# collector-message-integrity 提案

## Why

当前采集器对 WhatsApp 会话的处理存在两条消息完整性缺口：**群聊完全被跳过**（`idb_walk` 不读 `group-metadata`、chat `kind` 硬编码 `single`，群聊消息永远无法入库，客户被拉进群后该群沟通完全不进知识），以及**复合行正文失真**（引用回复会把被引用文本混入 body、相册/图片消息被整行忽略、`fromMe` 仅靠 tail-in/tail-out 单点判断偶有误判）。这两条缺口导致入库聊天数据不完整、归属不准确，进而影响画像抽取与 RAG 召回的准确性。

## What Changes

- **群聊会话归属**：读取 IDB `group-metadata` store（群名/成员），识别 `@g.us` 会话并落库为 `kind=group`；群聊入站消息解析成员显示名（LID→名字），入库 `sender_jid` 同时记录显示名
- **画像摘要区分发送者**：`build_chat_summary` 在群聊会话按发送者标注（`成员名: 正文`），单聊保持 `我/客户` 不变，使 LLM 画像抽取能区分群内不同成员
- **Web 聊天页显示发送者**：群聊消息气泡展示发送者名，单聊保持现状
- **引用回复净化**：DOM 解析排除引用块（`message-quote`/引用文本），body 只保留本人正文
- **相册/媒体消息**：`image-album` 行不再被忽略，按可用的说明文字/媒体标记入库
- **fromMe 判断增强**：多信号联合判断（tail-in/out + 发送人 JID 与自身账号比对），降低误判

## Capabilities

### New Capabilities

- `whatsapp-sync/group-chat`: 群聊会话识别、`kind=group` 标记、群成员显示名解析与消息发送者归属

### Modified Capabilities

- `whatsapp-sync`: 复合行（引用回复/相册）正文提取与 `fromMe` 判断行为变化；群聊不再被排除，纳入同步范围
- `customer-profile`: 画像摘要构造对群聊会话按发送者标注，抽取输入内容语义变化

## Impact

- `app/collector/idb_walk.py`: 读取 `group-metadata` store，扩展 walk 返回结构（群名/成员映射）
- `app/collector/dom_snapshot.py`: 引用块排除、相册行识别、fromMe 多信号
- `app/collector/scanner.py`: 群聊识别与 `kind` 写入、发送者显示名入库
- `app/collector/merger.py`: 合并逻辑携带发送者显示名
- `app/storage/schema.sql` / `sqlite_store.py` / `interfaces.py`: messages 增加发送者显示名、chats 记录群名；迁移幂等
- `app/profile/service.py`: `build_chat_summary` 群聊发送者标注
- `app/web/templates/chat_messages.html`: 群聊发送者展示
- `tests/`: 新增群聊/引用净化/相册/fromMe 测试，既有测试保持通过
```

## openspec/changes/collector-message-integrity/design.md

- Source: openspec/changes/collector-message-integrity/design.md
- Lines: 1-69
- SHA256: 8678ba13c206a93595a389942bdc1c25fca34b6f9e9cd3e058fa46f29bc93332

```md
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
```

## openspec/changes/collector-message-integrity/tasks.md

- Source: openspec/changes/collector-message-integrity/tasks.md
- Lines: 1-41
- SHA256: cd8ac046f3fe28de6a5acb4c77db115a097de587c9770434c297bd546d365bae

```md
# collector-message-integrity 任务清单

## 1. 数据层：sender_name 列 + Message 模型

- [ ] 1.1 `schema.sql` 为 `messages` 表增加 `sender_name TEXT`；`sqlite_store.py` 幂等迁移块（try/except OperationalError）确保旧库补列
- [ ] 1.2 `interfaces.py` `Message` dataclass 增加 `sender_name: str | None`；`sqlite_store._row_to_msg`/`upsert_message` 同步读写新列
- [ ] 1.3 补测试：旧库无 sender_name 时迁移不报错；upsert/list 携带 sender_name 往返一致

## 2. IDB 群聊元数据读取

- [ ] 2.1 `idb_walk.py` 放开 `group-metadata` store，页面 JS 提取群 JID/群名/参与者（jid+name，防御式读取）；walk 结果新增 `groups: {g_jid: {name, members}}`
- [ ] 2.2 补测试：合成 group-metadata 结构 → walk 输出 groups 映射正确；store 缺失/异常时静默降级

## 3. 采集器群聊识别与发送者解析

- [ ] 3.1 `scanner._merge_idb_dom`：chat 以 `@g.us` 结尾 → 标记 group；解析入站发送者显示名（contacts → 群成员表 → DOM 显示名 → JID 回退），写入 `sender_name`
- [ ] 3.2 `scanner._upsert_one`：`kind` 按群聊/单聊写入 `chats`（群聊用群名作 display_name）；`Message` 构造携带 `sender_name`
- [ ] 3.3 补测试：群聊消息入库 kind=group、sender_name 正确；成员名缺失回退 JID；单聊行为不变（既有测试通过）

## 4. 复合行净化（DOM 解析）

- [ ] 4.1 `dom_snapshot._parse_row` 跳过引用容器（testid 含 `message-quote`/`quoted-`），body 只含本人正文；testid 漂移时静默回退原行为
- [ ] 4.2 `parse_dom_snapshot` 识别媒体行（image-album/image/video/ptt/document/audio/location），说明文字作 body，无正文用媒体标记占位，type 记录媒体类型
- [ ] 4.3 fromMe：`_merge_idb_dom` 以 IDB 发送者==自身账号为权威（DOM tail 信号冲突时覆盖）；DOM-only 路径保持 tail-in/out
- [ ] 4.4 补测试：引用回复 body 排除引用文本；相册/图片行入库带说明或媒体标记；fromMe 冲突时 IDB 优先

## 5. 画像摘要发送者标注

- [ ] 5.1 `profile/service.py build_chat_summary`：按 `chats.kind` 分支——group 用 `{sender_name}: {body}`（我方仍 `我:`），single 保持 `我/客户`
- [ ] 5.2 补测试：群聊会话摘要按发送者标注；单聊摘要格式不变

## 6. Web 聊天页发送者展示

- [ ] 6.1 `chat_messages.html`：群聊会话消息气泡展示发送者名（单聊保持现状）
- [ ] 6.2 补测试：群聊聊天页渲染发送者名

## 7. 收尾验证

- [ ] 7.1 全量测试：`pytest -q` 全部通过（原 102 + 新增，预计 110+）
- [ ] 7.2 构建检查：`compileall -q app tests` 通过
- [ ] 7.3 勾选 tasks.md 全部任务并提交
```

## openspec/changes/collector-message-integrity/specs/customer-profile/spec.md

- Source: openspec/changes/collector-message-integrity/specs/customer-profile/spec.md
- Lines: 1-18
- SHA256: eb99ab20ddc4ff99be035fcd96edce079c10cf8a63f432f99b9a6e0122de5f54

```md
# customer-profile Delta Specification

## MODIFIED Requirements

### Requirement: 客户画像字段维护
系统 SHALL 为每个客户维护画像字段（姓名/公司/国家/产品兴趣/询价历史/沟通偏好/语言/成交阶段等），存储于结构化存储，并支持手动编辑修正。画像抽取所用的聊天摘要 SHALL 对群聊会话按发送者标注消息归属，使 LLM 能区分群内不同成员的发言。

#### Scenario: 自动抽取画像
- **WHEN** 某客户有新增聊天内容并触发画像更新
- **THEN** 系统 SHALL 由 LLM 从该客户近期聊天摘要中抽取/更新画像字段，带时间戳与来源标记

#### Scenario: 群聊摘要按发送者标注
- **WHEN** 画像抽取所依据的聊天摘要来自群聊会话
- **THEN** 摘要 SHALL 按发送者显示名标注每条消息（如 `成员名: 正文`），单聊保持 `我/客户` 标注不变

#### Scenario: 手动编辑优先
- **WHEN** 用户手动编辑某画像字段
- **THEN** 该字段 SHALL 以用户编辑值为准（标记为人工来源），不被后续自动抽取覆盖
```

## openspec/changes/collector-message-integrity/specs/whatsapp-sync/group-chat/spec.md

- Source: openspec/changes/collector-message-integrity/specs/whatsapp-sync/group-chat/spec.md
- Lines: 1-40
- SHA256: bfcdc5dcf03e9d46e096d1fca8c4eedf67820a65df614fdcbfcef064d4e08193

```md
# whatsapp-sync/group-chat Delta Specification

## Purpose

支持 WhatsApp 群聊会话的识别与消息归属：将 `@g.us` 群聊标记为 `kind=group`、解析群成员显示名，使群聊消息能正确入库并在画像摘要与 Web 界面中展示发送者。

## ADDED Requirements

### Requirement: 群聊会话识别
系统 SHALL 读取 IDB `group-metadata` store，识别 `@g.us` 群聊会话，将其以 `kind=group` 落库到结构化存储，并记录群名。

#### Scenario: 群聊入库
- **WHEN** 采集器同步到 `@g.us` 会话的消息
- **THEN** 系统 SHALL 将该会话标记为 `kind=group` 并记录群名，消息按群聊归属入库

#### Scenario: 单聊不受影响
- **WHEN** 采集器同步到 `@c.us` 单聊会话
- **THEN** 系统 SHALL 保持 `kind=single`，行为与既有同步一致

### Requirement: 群成员显示名解析
系统 SHALL 解析群聊入站消息的发送者身份，将成员 LID/手机号 JID 映射为可读显示名；无法解析时回退原始标识。

#### Scenario: 成员名解析
- **WHEN** 群聊入站消息发送者为已知联系人（含 LID）
- **THEN** 系统 SHALL 在消息中记录该成员的显示名，供摘要与界面使用

#### Scenario: 成员名缺失回退
- **WHEN** 群聊入站消息发送者无法匹配任何联系人
- **THEN** 系统 SHALL 以原始 JID 作为回退标识，不中断该批采集

### Requirement: 群聊发送者归属入库
系统 SHALL 在消息记录中同时保存发送者 JID 与显示名，使画像摘要能区分群内不同成员。

#### Scenario: 入库携带发送者名
- **WHEN** 群聊消息入库
- **THEN** 系统 SHALL 持久化发送者 JID 与解析后的显示名，供后续消费

#### Scenario: 群聊不拆成员客户
- **WHEN** 群聊会话关联客户实体
- **THEN** 系统 SHALL 将整个群聊关联到单个客户实体，不为群成员单独创建客户
```

## openspec/changes/collector-message-integrity/specs/whatsapp-sync/spec.md

- Source: openspec/changes/collector-message-integrity/specs/whatsapp-sync/spec.md
- Lines: 1-33
- SHA256: 362733b1ce76969fd12ec72d1538126edeb2429d3993adf37c8a7aebc3e107a4

```md
# whatsapp-sync Delta Specification

## MODIFIED Requirements

### Requirement: 消息元数据与明文正文采集
系统 SHALL 通过 CDP 读取 WhatsApp Web IndexedDB `model-storage` 库的 message/chat/contact/group-metadata stores 获取消息元数据，并通过 DOM 快照获取明文正文，按消息 id 合并两者。正文提取 SHALL 排除引用回复中的被引用文本，仅保留消息本身正文；相册/媒体消息 SHALL 以可用说明文字或媒体标记入库。

#### Scenario: 合并元数据与正文
- **WHEN** 一次采集 tick 完成
- **THEN** 每条消息 SHALL 同时具备 IDB 来源的元数据（id/chatId/fromMe/from/timestamp/type）与 DOM 来源的明文正文（若该消息已渲染）

#### Scenario: 引用回复正文净化
- **WHEN** 某消息为引用回复（含被引用文本块）
- **THEN** 系统 SHALL 仅采集该消息本人的正文文本，排除被引用的历史消息内容

#### Scenario: 相册/媒体消息
- **WHEN** 消息行为相册或媒体行（非普通文本行）
- **THEN** 系统 SHALL 不再忽略该行；有说明文字时采集说明文字，无正文时以媒体标记作为 body

#### Scenario: 正文缺失容忍
- **WHEN** 某历史消息未在当前 DOM 渲染
- **THEN** 系统 SHALL 保存其元数据，正文标记为缺失，不阻塞该批采集

### Requirement: 消息发送方向判断
系统 SHALL 通过 DOM 渲染信号（tail-in/tail-out）与 IDB 消息元数据（发送者 JID 与自身账号比对）联合判断消息方向，任一来源可用时作为依据，两者冲突时以 IDB 元数据为准，降低误判。

#### Scenario: 多信号联合判断
- **WHEN** 某消息同时具备 DOM tail 信号与 IDB 发送者元数据
- **THEN** 系统 SHALL 综合两者判断 `fromMe`，冲突时以 IDB 发送者与自身账号比对结果为准

#### Scenario: 仅 DOM 信号可用
- **WHEN** 某消息仅能从 DOM 判断方向
- **THEN** 系统 SHALL 以 tail-in/tail-out 信号作为 `fromMe` 依据
```

