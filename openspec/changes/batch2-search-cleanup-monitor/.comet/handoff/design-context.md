# Comet Design Handoff

- Change: batch2-search-cleanup-monitor
- Phase: design
- Mode: compact
- Context hash: fa59c2642c9ff593d91bc415d6b571473187fd2ade99c099225bd8810af3d02e

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/batch2-search-cleanup-monitor/proposal.md

- Source: openspec/changes/batch2-search-cleanup-monitor/proposal.md
- Lines: 1-34
- SHA256: f932070fc507dbc4d5172a2da5304ffd89c80cbf1c3874ce7fc5e9bdffdd4c60

```md
# batch2-search-cleanup-monitor 提案

## Why

第二批优化聚焦客户与知识管理（优化计划书第 6/7/8 项）：Web 端无全局搜索能力，客户/消息/知识/画像分散在各页无法跨源检索；聊天数据无手动清理入口，历史消息只能累积无法按需清除；采集器异常（CDP 断线/进程停止）仅在首页状态卡可见，其它页面无提示，用户可能误以为数据已同步。

## What Changes

- **全局搜索页（P2）**：新增 `/search` 页 + `GET /api/search?q=`；客户（customers 表名称/电话/公司/国家 LIKE）、消息（messages_fts 全文检索）、知识库（doc_chunks_fts）、画像（profiles 字段匹配）四源检索，结果分组展示
- **手动数据清理（P2）**：新增 `/api/cleanup` 接口 + 管理页按钮；支持按会话（chat_id）或按天数（N 天前）清理；删除 messages + 对应 message_vectors（ChromaDB 按 chat_id 删）；只清理聊天消息，不动知识库文档，保留画像（profiles 不动）
- **采集器异常提示（P2）**：`base.html` 加全局横幅区域；前端定时轮询 `/api/collector/status`（app.js setInterval）；`is_alive=false` 时显示红色横幅「采集器异常」

## Capabilities

### New Capabilities

（无新增 capability，行为契约并入既有 capabilities）

### Modified Capabilities

- `web-app`: 新增全局搜索页与接口；手动数据清理管理入口；采集器异常全局横幅
- `knowledge-base`: 全局搜索覆盖知识库文档片段（doc_chunks_fts）
- `whatsapp-sync`: 手动清理聊天消息（按会话/按天数删除 messages + 向量）

## Impact

- `app/web/routes.py`: `/search` 页、`GET /api/search`、`/api/cleanup`、管理页
- `app/web/templates/search.html`（新）、`cleanup.html` 或管理区片段、`base.html` 横幅
- `app/web/static/js/app.js`: 采集器状态轮询 + 搜索交互
- `app/storage/sqlite_store.py`: 按会话/天数删除 messages（含 FTS 同步）、客户 LIKE 检索、画像字段检索
- `app/storage/chroma_store.py`: 新增 `delete_message_vectors(chat_id)`
- `app/storage/interfaces.py`: `VectorStore` 接口扩展 `delete_message_vectors`
- `app/storage/schema.sql`: 无新增表（清理/搜索复用既有表）
- `tests/`: 搜索各源检索、清理删除逻辑与画像保留断言、横幅轮询
```

## openspec/changes/batch2-search-cleanup-monitor/design.md

- Source: openspec/changes/batch2-search-cleanup-monitor/design.md
- Lines: 1-48
- SHA256: cc1b7b69b52c433e3a284eed07ceca0b2edcf62edfc751217f46f7860b8e580f

```md
# batch2-search-cleanup-monitor 技术设计

> 需求与范围见 proposal.md，行为契约见 specs/。本文记录高层架构决策；深度技术设计见 comet-design 阶段的 Design Doc。

## Context

Web 端无全局搜索（客户/消息/知识/画像分散）；聊天数据无清理入口；采集器异常仅首页可见。批二三项优化均落在 Web 层（routes + 模板 + store），无新外部依赖。

## Goals / Non-Goals

**Goals:**
- 全局搜索页：跨客户/消息/知识库/画像四源检索分组展示
- 手动清理：按会话或按天数删除聊天消息 + 向量，保留知识库与画像
- 采集器异常全局横幅：前端定时轮询，离线红色提示

**Non-Goals:**
- 不引入全文检索引擎（复用既有 FTS5）
- 不做清理操作的可恢复（删除即不可恢复，需前端确认）
- 不做自动清理（仅手动触发）
- 不改采集器进程行为（异常检测复用既有 status 文件）

## Decisions

### D1: 全局搜索复用既有检索原语
客户用 customers 表 LIKE（名称/电话/公司/国家）；消息用 `messages_fts`（复用 `search_fts`，FTS 行 rowid join 回 messages 取 chat_id/body）；知识库用 `doc_chunks_fts`；画像用 profiles 表字段 LIKE。四源各自查询、统一返回分组 JSON，前端分组渲染。搜索为空返回空结果不报错。

### D2: 清理 = messages + message_vectors，不动知识库/画像
按 chat_id 或 ts<now-N 天删除 messages 行；FTS 外部内容表删行后 rebuild（复用 delete_document 的 FTS 处理模式）。向量按 metadata.chat_id 删除：Chroma `where={"chat_id": ...}` 支持，新增 `VectorStore.delete_message_vectors(chat_id)`。按天数清理时按时间范围收集 chat_id 集合分别删。**profiles 与 documents 完全不动**。

### D3: 采集器异常横幅复用既有 status 端点
`/api/collector/status` 已存在（返回 `{status, alive}`）。`base.html` 加全局横幅容器，`app.js` 用 `setInterval` 定时轮询（如 15s），`alive=false` 时显示红色横幅，恢复后隐藏。不新增端点。

### D4: 清理接口需前端确认
`/api/cleanup` 为 POST + 参数（mode: chat|days, chat_id?, days?），删除前由前端弹确认（普通 `<script>confirm()` 即可）。返回删除行数与受影响会话数。

## Risks / Trade-offs

- **[清理不可恢复]** → 删除前前端确认 + 只清聊天不动知识库/画像；本地工具可接受。
- **[FTS rebuild 开销]** → 清理为低频手动操作，rebuild 成本可接受；复用既有 delete_document 模式。
- **[Chroma 双进程写锁]** → 清理走 Web 进程，与采集器低频写入冲突面小；失败时返回可读错误。
- **[搜索性能]** → LIKE 查询无索引，本地数据量小（单用户）可接受；FTS 走索引。

## Migration Plan

1. 无 schema 变更（复用既有表与 FTS）
2. 新功能走 feature 分支，build 阶段逐任务提交
3. 回归：`pytest -q` 全量；`compileall -q app`
4. 回滚：均为增量改动；清理删除不可回滚（操作层面），功能代码可回退
```

## openspec/changes/batch2-search-cleanup-monitor/tasks.md

- Source: openspec/changes/batch2-search-cleanup-monitor/tasks.md
- Lines: 1-29
- SHA256: 712538cb4fcb5d1eccbe9d07f1e59f2580bee0811fe4e58c6ae053474dec41a5

```md
# batch2-search-cleanup-monitor 任务清单

## 1. 全局搜索

- [ ] 1.1 `SqliteStore` 增加客户检索（名称/电话/公司/国家 LIKE）与画像字段检索方法
- [ ] 1.2 `SqliteStore.search_fts` 结果映射：消息 FTS 行 join 回 messages 取 chat_id/body/ts；知识库 FTS join 回 doc_chunks 取 doc_id
- [ ] 1.3 新增 `GET /api/search?q=` 聚合四源返回分组结果
- [ ] 1.4 新增 `/search` 页模板（分组展示，空查询友好提示）
- [ ] 1.5 单测：四源各自命中与空查询行为

## 2. 手动数据清理

- [ ] 2.1 `VectorStore.delete_message_vectors(chat_id)`（Chroma metadata 过滤）+ 接口声明
- [ ] 2.2 `SqliteStore` 删除方法：按 chat_id 或按 ts 范围删 messages + FTS rebuild
- [ ] 2.3 新增 `POST /api/cleanup`（mode: chat|days，前端确认后调用）
- [ ] 2.4 管理入口：模板页/按钮触发清理
- [ ] 2.5 单测：按会话/按天数删除、画像与知识库保留断言

## 3. 采集器异常横幅

- [ ] 3.1 `base.html` 加全局横幅容器
- [ ] 3.2 `app.js` 定时轮询 `/api/collector/status`，alive=false 显示红色横幅
- [ ] 3.3 前端测试/走读确认横幅逻辑

## 4. 回归验证

- [ ] 4.1 全量 `pytest -q` 通过（新增 + 既有）
- [ ] 4.2 `compileall -q app` 通过
- [ ] 4.3 代码走读：清理保留画像/知识库、搜索各源正确、横幅轮询无泄漏
```

## openspec/changes/batch2-search-cleanup-monitor/specs/knowledge-base/spec.md

- Source: openspec/changes/batch2-search-cleanup-monitor/specs/knowledge-base/spec.md
- Lines: 1-14
- SHA256: 0345a8fc7b1f29ef0738438c222ee92b272fec6c6698a967f4310601ec7282f0

```md
# knowledge-base Delta Spec

> Delta 变更，叠加于 `openspec/specs/knowledge-base/spec.md`。

## ADDED Requirements

### Requirement: 全局搜索覆盖知识库

系统 SHALL 在全局搜索中检索知识库文档片段并返回匹配结果。

#### Scenario: 知识库片段命中

- **WHEN** 用户发起全局搜索且关键字命中知识库文档片段
- **THEN** 系统 SHALL 返回命中的文档片段及所属文档
```

## openspec/changes/batch2-search-cleanup-monitor/specs/web-app/spec.md

- Source: openspec/changes/batch2-search-cleanup-monitor/specs/web-app/spec.md
- Lines: 1-62
- SHA256: 54f5ba9c86df134ff088718d93755227e69ce8cbc97770928e00febbbc8356f6

```md
# web-app Delta Spec

> Delta 变更，叠加于 `openspec/specs/web-app/spec.md`。

## ADDED Requirements

### Requirement: 全局搜索

系统 SHALL 提供全局搜索页与接口，跨客户、消息、知识库、画像四源检索并分组展示。

#### Scenario: 搜索客户

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回名称/电话/公司/国家匹配的客户

#### Scenario: 搜索消息

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回全文匹配的聊天消息

#### Scenario: 搜索知识库

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回匹配的知识库文档片段

#### Scenario: 搜索画像

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回字段值匹配的客户画像

### Requirement: 手动数据清理

系统 SHALL 提供手动清理聊天数据的管理入口，支持按会话或按天数删除，且不影响知识库文档与客户画像。

#### Scenario: 按会话清理

- **WHEN** 用户指定某会话（chat_id）请求清理
- **THEN** 系统 SHALL 删除该会话的全部聊天消息及其向量

#### Scenario: 按天数清理

- **WHEN** 用户指定天数 N 请求清理
- **THEN** 系统 SHALL 删除 N 天前的全部聊天消息及其向量

#### Scenario: 保留知识库与画像

- **WHEN** 清理聊天数据
- **THEN** 系统 SHALL 不删除知识库文档，不删除客户画像字段

### Requirement: 采集器异常全局提示

系统 SHALL 在 Web UI 全局区域展示采集器状态，采集器不可达时显示异常横幅。

#### Scenario: 展示采集器异常

- **WHEN** 采集器不在线（is_alive=false）
- **THEN** 系统 SHALL 在页面全局横幅显示「采集器异常」提示

#### Scenario: 定时检查采集状态

- **WHEN** 用户停留在任意页面
- **THEN** 前端 SHALL 定时轮询采集器状态并在异常时更新横幅
```

## openspec/changes/batch2-search-cleanup-monitor/specs/whatsapp-sync/spec.md

- Source: openspec/changes/batch2-search-cleanup-monitor/specs/whatsapp-sync/spec.md
- Lines: 1-19
- SHA256: faf0aa584e51d3950b98c1c1f0481bacf6025ebf8aaba04bb76967452486d636

```md
# whatsapp-sync Delta Spec

> Delta 变更，叠加于 `openspec/specs/whatsapp-sync/spec.md`。

## ADDED Requirements

### Requirement: 手动清理聊天消息

系统 SHALL 支持手动清理聊天消息数据，删除消息记录及其向量，不影响知识库与画像。

#### Scenario: 清理会话消息

- **WHEN** 用户请求清理某会话
- **THEN** 系统 SHALL 删除该会话的 messages 记录与对应 message_vectors，不删除知识库文档与画像

#### Scenario: 清理过期消息

- **WHEN** 用户请求清理 N 天前的消息
- **THEN** 系统 SHALL 删除 N 天前的 messages 记录与对应 message_vectors
```

