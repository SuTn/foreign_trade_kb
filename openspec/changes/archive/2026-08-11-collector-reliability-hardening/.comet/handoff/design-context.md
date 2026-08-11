# Comet Design Handoff

- Change: collector-reliability-hardening
- Phase: design
- Mode: compact
- Context hash: 83bc286be4d4c8486fe5f90556147df1723830223136ecaa2e46bd98f7f387d2

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/collector-reliability-hardening/proposal.md

- Source: openspec/changes/collector-reliability-hardening/proposal.md
- Lines: 1-42
- SHA256: 406b9b2971e87e42d069db2cd5a2366cacd7fd968c558bfb91315f5d86d5daf9

```md
# collector-reliability-hardening 提案

## Why

采集器与 Web 服务存在 9 项稳定性/正确性缺陷：采集器对 CDP 断线与瞬时异常毫无自愈能力（一次 Chrome 崩溃或会话失效即永久停摆）；消息向量按 `(chatId, day)` 键覆盖写导致历史聊天向量召回实际只剩每会话每天最后一条；Web 端每请求新建 SqliteStore/ChromaStore 放大双进程共享 SQLite 的锁竞争；reply/search/upload 接口在 LLM/嵌入/重排失败时直接 500；上传文档状态永卡 `processing`；空/坏/不支持格式文档上传崩溃且残留脏行；模型加载同步阻塞事件循环且 CPU-only 环境 `use_fp16=True` 直接失败；backfill 存在死代码与每 2s 空轮询；IDB `getAll()` 全量拉取无分页且 `max_records_per_store` 配置从未生效。这些问题共同导致核心采集链路脆弱、检索数据失真、接口体验不可靠。

## What Changes

- **采集器自愈（B1）**：`run()` 主循环整体异常防护 + 指数退避；CDP 会话失效检测并自动重新 `launch_browser`；`__main__.py` 改为 supervisor 守护采集器子进程，进程死亡自动拉起
- **消息向量语义修正（B2）**：向量键从 `(chatId, day)` 覆盖写改为按消息 id 独立入库，消除同日消息互相覆盖与嵌入计算浪费；清理旧键模式数据
- **Web 存储单例（H1）**：FastAPI lifespan 持有进程级 SqliteStore/ChromaStore 单例，Web 侧复用，消除每请求重连与 schema 初始化；采集器进程维持自身单例
- **接口错误降级（H2）**：`/api/reply`、检索、上传统一 try/except，LLM/嵌入/重排失败返回可读降级信息而非 500
- **上传状态机（H3/H4）**：`documents.status` 完整流转 `processing → done/failed`；解析/向量化包 try/except，空/坏/不支持格式上传返回友好错误且不残留脏行
- **模型加载健壮性（H5）**：`use_fp16` 按 CUDA 可用性决定（CPU-only 自动回退 fp32）；模型加载移入 executor/启动预热，不阻塞事件循环
- **backfill 清理（H6）**：删除死代码（未定义变量引用）；backfill_requests 表纳入 schema；表存在性只查一次；失败不标记完成并可重试
- **IDB 分页（H7）**：`idb_walk` 按 IDBKeyRange 分页/上限读取，让 `max_records_per_store` 生效，避免每 30s 全量序列化

## Capabilities

### New Capabilities

- `whatsapp-sync/resilience`: 采集器断线自愈与子进程守护、IDB 分页读取、按需回溯请求队列的可靠处理

### Modified Capabilities

- `whatsapp-sync`: 消息向量按消息 id 独立入库（原按 (chatId,day) 分组覆盖写）；IDB 读取上限生效
- `knowledge-base`: 文档上传状态完整流转（processing→done/failed）与失败处理；检索/回复错误降级
- `web-app`: 存储访问改为进程级单例；reply/search/upload 错误降级行为

## Impact

- `app/collector/scanner.py`: `run()` 自愈主循环、向量键、backfill 清理、IDB 分页调用
- `app/collector/__main__.py` / `app/__main__.py`: supervisor 守护与自动拉起
- `app/collector/idb_walk.py`: 分页读取与 `max_records_per_store` 应用
- `app/collector/browser.py`: 会话失效检测/重连辅助
- `app/web/routes.py`: lifespan 单例、错误降级、上传状态机
- `app/web/app.py`: FastAPI lifespan 持有 store 单例
- `app/storage/sqlite_store.py` / `chroma_store.py`: 单例复用支持、backfill 表 schema、状态更新辅助
- `app/storage/schema.sql`: backfill_requests 表
- `app/knowledge/rag_index.py` / `parser.py`: 上传失败处理与状态写入
- `app/llm/bge_embedding.py` / `app/rag/reranker.py`: `use_fp16` 按 CUDA 决定、加载非阻塞
- `tests/`: 新增自愈/向量/状态机/降级/分页测试，既有测试保持通过
```

## openspec/changes/collector-reliability-hardening/design.md

- Source: openspec/changes/collector-reliability-hardening/design.md
- Lines: 1-91
- SHA256: c3051ddf013c5993dae36ac7d227c1650923d46ac02e974262fd5f96bc400d54

[TRUNCATED]

```md
# collector-reliability-hardening 技术设计

> 需求与范围见 proposal.md，行为契约见 specs/。本文只讲 HOW。

## Context

双进程架构：采集器子进程经 `ReadOnlyCDP` 只读同步 WhatsApp（DOM 快照 + IDB walk），Web 主进程经 FastAPI 提供界面。两者共享同一 SQLite（WAL+FTS5）与 Chroma。当前 `Scanner.run()` 主循环只对 slow_tick/scan_all_chats 有 try/except（scanner.py:334-343），最高频的 `fast_tick`（scanner.py:34-46）无防护，任何 CDP 异常会冲出循环杀死采集器进程且无守护；消息向量以 `f"{chat_id}:{day}"` 为键 upsert（scanner.py:244-245），同日多消息互相覆盖；`_drain_backfill_requests`（scanner.py:372-398）含未定义变量 `data` 的死代码、每 2s 轮询表缺失、失败也标 done；Web 端 `_store()` 每请求新建 SqliteStore、每个接口新建 ChromaStore（routes.py:25-26,198,253,265,288）；`use_fp16=True` 硬编码（bge_embedding.py:21, reranker.py:33）；上传无状态流转（routes.py:276-279 只写 processing）。

## Goals / Non-Goals

**Goals:**
- 采集主循环 + 子进程守护自愈，CDP 失效自动重连浏览器
- 消息向量按消息 id 独立入库，消除同日覆盖
- Web 端进程级 store 单例；reply/search/upload 错误降级
- 上传状态完整流转 processing→done/failed；坏文件友好失败
- `use_fp16` 按 CUDA 可用性决定
- backfill 死代码清理 + 失败重试
- IDB 分页读取，`max_records_per_store` 生效

**Non-Goals:**
- 不做 Medium/Low 级问题（CSRF、Wiki 编辑、chat id 归一、客户匹配并发、父子块展开、检索性能重构）
- 不实现发送消息/任何写 WhatsApp 能力
- 不引入新外部依赖

## Decisions

### D1: 采集器自愈 — 主循环防护 + CDP 会话失效重连
`Scanner.run()` 主循环整体包 try/except：捕获异常记录状态后按指数退避（1s→2s→4s…上限 30s）重试下一轮。CDP 失效判定采用**失败计数阈值触发**：fast_tick 捕获异常时先区分可重试（网络抖动/瞬时）与致命（session 失效/连接断开），连续 3 次致命失败才重建浏览器（`launch_browser()` 重建 pw/context/page/cdp 并重置 `_current_chat_id`/`_last_dom_hash`），瞬时错误走退避重试不触发重建。重连失败继续退避重试，不退出。

- **备选**：仅靠进程级看门狗重启——丢状态且需反复 launch，更慢。放弃。

### D2: 子进程守护 — supervisor 自动拉起
`app/__main__.py` 将 `Popen` 改为循环：`while True: p=Popen(...); rc=p.wait(); if 用户退出: break; sleep 退避; 重启`。用 `--supervise` 或默认行为区分手动运行。Web 主进程内维护标志位区分正常退出（Ctrl+C）与异常退出（重启）。采集器 `__main__.py` 自身异常时 exit code 非 0，supervisor 据此决定重启。

- **备选**：外部 systemd/supervisord——本机场景不引入系统依赖。放弃。

### D3: 消息向量 per-message 键
向量键从 `f"{chat_id}:{day}"` 改为 `f"{chat_id}:{msg_id}"`（消息 id 全局唯一即可，无则回退原 day 键）。Chroma metadata 保留 `chat_id`/`day` 供按会话过滤与清理。**旧数据处理：主动清理重建**——build 阶段一次性清空 `message_vectors` 集合（`delete(where={})`），随慢 tick/扫描重新入库生成 per-message 向量，避免旧 day 键数据污染召回。

- **备选**：聚合每日消息摘要成一个向量——需维护窗口状态，复杂且召回粒度粗。放弃。

### D4: Web 端进程级 store 单例
FastAPI `create_app()` 用 lifespan 持有 `app.state.sqlite_store` 与 `app.state.chroma_store`（ChromaStore 由 embedding_fn 构造一次），路由改用 `request.app.state.*` 访问。lifespan 退出时关闭连接。`_store()` 保留为兼容测试用的便捷函数但改读单例。删除文档/上传等写路径与采集器进程分属不同进程，Chroma 双进程写锁问题通过 Chroma 自身 SQLite WAL + 事务 + 采集器低频写入缓解；同时上传/删除接口返回前确保向量操作完成。

- **备选**：仅 SqliteStore 单例、Chroma 仍每请求建——锁风险仍在。放弃。
- **备注**：双进程 Chroma 锁是既有架构约束，本 change 降低 Web 侧创建频率，完整解决需迁移数据存储，超出范围（记入 risks）。

### D5: reply/search/upload 错误降级
- `/api/reply` 与 `/api/reply/regenerate`：整体 try/except，`generate_reply`/`regenerate_reply` 抛错时渲染 `reply_result.html` 传 `error` 字段（模板已有 error 分支则复用，否则加一个）。
- `/api/knowledge/search`：嵌入失败时向量路跳过仅返回 BM25 结果 + `degraded` 提示；重排不在该接口。
- `/api/knowledge/upload`：见 D6。
- `OllamaReranker.rerank` 网络/HTTP 异常改为捕获后返回原序 candidates（不重排）并打日志。

### D6: 上传状态机 + 坏文件友好失败
`upload` 改写：先 parse 包 try/except（含空文件、未知后缀 `ValueError`、损坏文件），失败时 `UPDATE documents SET status='failed'` 并返回 JSON `{"error": msg}`（HTTP 4xx 或 200+error 字段，由前端模板展示）。成功路径：parse → RagIndex.index → `UPDATE documents SET status='done'`；WikiIndex 失败不影响 status（双索引互不阻塞，符合既有 spec）。`documents` 行在 parse 成功后保留，parse 前若失败则删除该行或置 failed（选置 failed，保留审计痕迹；但 spec 说"不残留处于处理中状态"，failed 不是 processing，满足）。空文本（切分后 0 chunk）跳过向量化直接 done。

### D7: `use_fp16` 按 CUDA 决定
新增 helper `_use_fp16()`：`import torch; return torch.cuda.is_available()`（CPU-only 回退 fp32）。BgeEmbedding 与 BgeReranker 的 `_ensure` 用该值。模型加载（同步、~1GB）在首次调用时仍可能阻塞事件循环——D8 处理。

### D8: 模型加载非阻塞
Web 侧模型初始化在 `lifespan` 启动时**后台线程（`run_in_executor`）预热** `app.state.embedding`/`app.state.reranker`；接口首次调用时若未就绪则等待（有超时），超时返回降级提示（见 D5）。采集器进程侧模型在 `__main__.py` 启动时同步加载一次（该进程无并发请求，阻塞可接受）。`get_embedding()`/`get_reranker()` 保持函数签名，内部在首次调用后走缓存。

### D9: backfill 清理
- 删除 `_drain_backfill_requests` 中的 `data` 死代码块（scanner.py:391-398）。
- `backfill_requests` 表定义移入 `schema.sql`（CREATE TABLE IF NOT EXISTS），`routes.py` 的 CREATE 改为依赖 schema（保留容错）。
- 表存在性探测只做一次：`Scanner.__init__` 中检查一次并缓存布尔。
- 失败不标 done：`backfill_history` 抛异常时置 `attempts+1`（新增列）或保持 done=0，下次轮询重试；成功后标 done=1。

### D10: IDB 分页读取
`idb_walk` 的页面 JS 从 `getAll()` 改为基于 IDBKeyRange 的游标分页：`st.openCursor` 循环收集直到 `max_records_per_store` 上限（`settings.max_records_per_store`，默认 20000），或按主键范围分段多次 `getAll` 合并后截断。为控制单次 CDP `Runtime.evaluate` 返回体大小，JS 内收集上限后 resolve。`walk_idb` 各 store 独立应用上限。

- **备选**：分页多次 `requestData`——该版本 requestData 恒返回 0 行（见 idb_walk.py 注释），不可用。放弃。

## Risks / Trade-offs

- **[双进程 Chroma 写锁未彻底解决]** → 本 change 降低 Web 侧创建频率与并发写窗口；若仍锁冲突，后续独立 change 迁移消息向量到 SQLite 或引入队列。记录为已知限制。
- **[模型预热增加启动时间]** → 仅首次启动；预热在后台线程不阻塞 uvicorn 起服务；未就绪时接口等待有超时，超时返回降级提示。
- **[向量键变更导致旧向量不可达]** → RAG 向量召回仅对新入库消息生效；旧数据可在维护窗口一次性清空重建。文档化。
- **[supervisor 误重启]** → 采集器正常退出需 exit code 0 或显式标志；异常退出 code 非 0 才重启。
- **[CDP 重连期间消息丢失]** → 重连后依赖 slow_tick IDB 全量校准补齐，幂等 upsert 保证不重复。
```

Full source: openspec/changes/collector-reliability-hardening/design.md

## openspec/changes/collector-reliability-hardening/tasks.md

- Source: openspec/changes/collector-reliability-hardening/tasks.md
- Lines: 1-56
- SHA256: a06b41a79af9319a7053e09999735e7665c622c0f25825e4fb626baac7563d47

```md
# collector-reliability-hardening 任务清单

## 1. 采集器自愈 (D1/D2)

- [ ] 1.1 `Scanner.run()` 主循环整体 try/except + 指数退避（1s→30s 上限），异常记录到 status/日志不退出
- [ ] 1.2 CDP 失效检测：区分可重试/致命异常，连续 3 次致命失败才重建浏览器（launch_browser + 重置会话状态）
- [ ] 1.3 `app/__main__.py` supervisor 循环：采集器进程异常退出自动拉起（正常退出/用户中断不重启）
- [ ] 1.4 采集器 `__main__.py` 异常时以非 0 exit code 退出，供 supervisor 判定

## 2. 消息向量语义修正 (D3)

- [ ] 2.1 scanner 向量键从 `f"{chat_id}:{day}"` 改为 `f"{chat_id}:{msg_id}"`（无 id 回退 day 键）
- [ ] 2.2 旧向量清理：一次性清空 `message_vectors` 集合（delete where={}），随扫描重建 per-message 向量
- [ ] 2.3 单测：同会话同日多条消息各自独立向量键，互不覆盖

## 3. Web 存储单例 (D4/D8)

- [ ] 3.1 `create_app()` 加 lifespan 持有 `app.state.sqlite_store`/`app.state.chroma_store`，退出时关闭
- [ ] 3.2 路由全部改为读 `request.app.state.*` 单例，删除每请求 `_store()`/`ChromaStore(...)` 新建
- [ ] 3.3 embedding/reranker 在 lifespan 后台线程预热；首次接口调用未就绪时有超时降级

## 4. 接口错误降级 (D5)

- [ ] 4.1 `/api/reply` 与 `/api/reply/regenerate` try/except，失败渲染 `reply_result.html` 带 error 字段
- [ ] 4.2 `/api/knowledge/search` 嵌入失败降级为 BM25-only + degraded 提示
- [ ] 4.3 `OllamaReranker.rerank` 网络/HTTP 失败回退原序候选并打日志
- [ ] 4.4 单测：reply 失败路径返回降级不抛 500；OllamaReranker 网络失败回退原序

## 5. 上传状态机与坏文件处理 (D6)

- [ ] 5.1 upload 包 try/except：parse 失败置 `status='failed'` 返回可读错误，不 500
- [ ] 5.2 成功路径 parse→index 后置 `status='done'`；空文本（0 chunk）跳过向量化直接 done
- [ ] 5.3 单测：坏文件/空文件/未知格式上传返回错误且 status 置 failed；正常上传置 done

## 6. 模型加载健壮性 (D7)

- [ ] 6.1 新增 `_use_fp16()` helper（按 `torch.cuda.is_available()`），BgeEmbedding/BgeReranker 改用
- [ ] 6.2 单测：CPU-only（mock cuda 不可用）时构造参数 use_fp16=False

## 7. backfill 清理 (D9)

- [ ] 7.1 删除 `_drain_backfill_requests` 中 `data` 死代码块
- [ ] 7.2 `backfill_requests` 表定义移入 schema.sql（含 attempts 列），路由 CREATE 保留容错
- [ ] 7.3 表存在性探测只做一次（__init__ 缓存）；失败任务 attempts+1 不标 done，成功后标 done
- [ ] 7.4 单测：表缺失轮询不抛错；失败任务不标 done 可重试

## 8. IDB 分页读取 (D10)

- [ ] 8.1 idb_walk 页面 JS 改游标分页，应用 `max_records_per_store` 上限
- [ ] 8.2 单测：超过上限的 store 只返回前 N 条

## 9. 回归验证

- [ ] 9.1 全量 `pytest -q` 通过（新增 + 既有）
- [ ] 9.2 `compileall -q app` 通过
- [ ] 9.3 代码走读确认无遗留每请求 store 新建与死代码
```

## openspec/changes/collector-reliability-hardening/specs/knowledge-base/spec.md

- Source: openspec/changes/collector-reliability-hardening/specs/knowledge-base/spec.md
- Lines: 1-39
- SHA256: ddfc231777ff1d6146838425e9f35aabfe0c71863981931552c27770343d26b5

```md
# knowledge-base Delta Specification

## MODIFIED Requirements

### Requirement: 知识管理
系统 SHALL 在 Web UI 提供本地知识管理（上传/列表/删除/检索测试）。

#### Scenario: 上传与列表
- **WHEN** 用户上传文档
- **THEN** 系统 SHALL 解析、切分、向量化入库（RAG 索引）并异步生成 Wiki 页面（若开启），在知识列表展示该文档及其 chunk/Wiki 页面状态

#### Scenario: 检索测试
- **WHEN** 用户在知识管理页输入测试查询
- **THEN** 系统 SHALL 返回检索结果（含来源文档与片段）供验证

#### Scenario: 上传成功状态流转
- **WHEN** 文档解析与索引全部成功
- **THEN** 系统 SHALL 将该文档状态置为成功（done）

#### Scenario: 上传失败状态流转
- **WHEN** 文档解析或索引过程中发生错误
- **THEN** 系统 SHALL 将该文档状态置为失败（failed）并返回可读错误信息，不中断其他文档上传

#### Scenario: 空或损坏文档
- **WHEN** 上传的文件为空、损坏或格式不支持
- **THEN** 系统 SHALL 返回友好错误信息，不产生 500 响应，也不残留处于处理中状态的文档记录

## ADDED Requirements

### Requirement: 检索错误降级
系统 SHALL 在检索（向量化或重排）失败时返回可读的降级结果，而非 500 错误。

#### Scenario: 嵌入失败降级
- **WHEN** 检索过程中嵌入模型不可用或调用失败
- **THEN** 系统 SHALL 返回可读错误信息或降级结果，不返回 500

#### Scenario: 重排失败降级
- **WHEN** 检索过程中重排器不可用或调用失败
- **THEN** 系统 SHALL 以未重排的召回结果返回，并提示重排不可用，不返回 500
```

## openspec/changes/collector-reliability-hardening/specs/reply-assist/spec.md

- Source: openspec/changes/collector-reliability-hardening/specs/reply-assist/spec.md
- Lines: 1-14
- SHA256: 8908969ada2fbc67ae32721f480974aa1bd7fe816735d916bed1d95a8eaef0c6

```md
# reply-assist Delta Specification

## ADDED Requirements

### Requirement: 回复生成失败降级
系统 SHALL 在回复生成（LLM 或检索）失败时返回可读的降级结果，而非 500 错误。

#### Scenario: LLM 生成失败
- **WHEN** 回复生成过程中 LLM 调用失败或不可用
- **THEN** 系统 SHALL 返回可读错误信息（含失败原因提示），不返回 500

#### Scenario: 检索失败仍可提示
- **WHEN** 回复生成的检索环节失败
- **THEN** 系统 SHALL 返回可读错误信息并提示检索不可用，不返回 500
```

## openspec/changes/collector-reliability-hardening/specs/web-app/spec.md

- Source: openspec/changes/collector-reliability-hardening/specs/web-app/spec.md
- Lines: 1-14
- SHA256: 42d3af1eaa384ac14b711f5fe44918bd3fff01fdb7c2f506da99616b83db3928

```md
# web-app Delta Specification

## ADDED Requirements

### Requirement: 接口错误反馈
系统 SHALL 在回复生成、知识检索或文档上传等接口返回降级错误时，在 Web UI 向用户呈现可读的错误信息，而非通用 500 页面。

#### Scenario: 显示可读错误
- **WHEN** 用户触发回复生成/检索/上传且后端返回降级错误
- **THEN** 系统 SHALL 在页面向用户展示该错误信息与原因提示，页面不崩溃

#### Scenario: 错误后页面可用
- **WHEN** 接口返回降级错误后
- **THEN** 系统 SHALL 保持页面其他功能可用，用户可重试或返回
```

## openspec/changes/collector-reliability-hardening/specs/whatsapp-sync/resilience/spec.md

- Source: openspec/changes/collector-reliability-hardening/specs/whatsapp-sync/resilience/spec.md
- Lines: 1-40
- SHA256: 8dca3083ca4d45afdeb73758e2609991312c178a52de64418e6e33aea197b16f

```md
# whatsapp-sync/resilience Specification

## Purpose

确保 WhatsApp 采集器在 Chrome 崩溃、CDP 会话失效或瞬时异常时能自动自愈并继续采集，同时限制 IndexedDB 全量读取的资源消耗，并可靠地处理按需回溯请求。

## ADDED Requirements

### Requirement: 采集器断线自愈
系统 SHALL 在采集主循环的任何异常下不退出进程：瞬时错误按指数退避重试，CDP 会话失效时自动重新建立浏览器连接，保证核心采集链路持续可用。

#### Scenario: 瞬时异常自动重试
- **WHEN** 采集 tick 中发生可重试的瞬时异常（如网络抖动、单次 CDP 调用失败）
- **THEN** 系统 SHALL 记录该失败并按其退避策略等待后重试，主循环不退出、不中断后续 tick

#### Scenario: CDP 会话失效自愈
- **WHEN** 检测到 CDP 会话失效或浏览器连接断开
- **THEN** 系统 SHALL 自动重新启动浏览器并恢复采集，无需人工干预

#### Scenario: 子进程守护
- **WHEN** 采集器子进程意外退出
- **THEN** 系统 SHALL 由守护进程自动重新拉起采集器，Web 服务不因此终止

### Requirement: IndexedDB 分页读取
系统 SHALL 以分页或上限方式读取 IndexedDB store，应用 `max_records_per_store` 上限，避免单次全量读取造成内存与带宽峰值。

#### Scenario: 分页读取生效
- **WHEN** 慢 tick 读取 IDB store
- **THEN** 系统 SHALL 按 `max_records_per_store` 限制单 store 读取数量，不一次拉取全量数据

### Requirement: 按需回溯请求可靠处理
系统 SHALL 可靠处理按需回溯请求队列：请求表在 schema 中定义、轮询不因表缺失而报错、失败任务可重试。

#### Scenario: 请求队列可靠轮询
- **WHEN** 回溯请求表不存在或为空
- **THEN** 系统 SHALL 不报错、不刷错误日志，静默等待新请求

#### Scenario: 失败回溯可重试
- **WHEN** 某回溯请求执行失败
- **THEN** 系统 SHALL 保留该请求以供重试，不将其错误地标记为完成
```

## openspec/changes/collector-reliability-hardening/specs/whatsapp-sync/spec.md

- Source: openspec/changes/collector-reliability-hardening/specs/whatsapp-sync/spec.md
- Lines: 1-18
- SHA256: 0ea6c6dff6d5e45a807dff992f9bd67fb1cdd16475d409ff8193de68d0880713

```md
# whatsapp-sync Delta Specification

## MODIFIED Requirements

### Requirement: 幂等 upsert
系统 SHALL 按 (account_id, chat_id, message_id) 幂等 upsert 消息到结构化存储，按消息 id 独立幂等 upsert 到向量库，保证可重试且不重复；同一会话同日不同消息的向量 SHALL 各自独立保存，互不覆盖。

#### Scenario: 重复采集去重
- **WHEN** 同一消息被多次采集
- **THEN** 系统 SHALL 仅保留一条记录，不产生重复

#### Scenario: 同日多消息独立入库
- **WHEN** 同一会话同一天采集多条不同消息
- **THEN** 系统 SHALL 为每条消息独立保存向量，后到的消息不覆盖先到的消息

#### Scenario: 向量语义保留
- **WHEN** 历史聊天被向量召回
- **THEN** 系统 SHALL 能召回该会话每条消息（而非仅每会话每天最后一条）
```

