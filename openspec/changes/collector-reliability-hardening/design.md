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

## Migration Plan

1. schema.sql 增加 `backfill_requests` 表（含 `attempts` 列）；既有库走 CREATE IF NOT EXISTS 幂等
2. 新功能走 feature 分支，build 阶段逐任务提交
3. 回归：`pytest -q` 全量；`compileall -q app`
4. 回滚：均为增量改动，无破坏性 schema 变更；向量键变更需清空 `message_vectors` 集合才能回退到 day 键语义

## Open Questions

- CDP session 失效在 Playwright 的精确异常类型（TargetClosed vs 其他）——build 阶段用真实/模拟验证，实现上按异常消息与类型宽匹配。
