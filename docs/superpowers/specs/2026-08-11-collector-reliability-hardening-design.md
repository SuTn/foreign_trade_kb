---
comet_change: collector-reliability-hardening
role: technical-design
canonical_spec: openspec
---

# collector-reliability-hardening 技术设计

> 需求与范围见 proposal.md，行为契约见 specs/。本文为 Superpowers 侧实施视角的技术设计。

## Context

双进程架构：采集器子进程经 `ReadOnlyCDP` 只读同步 WhatsApp（DOM 快照 + IDB walk），Web 主进程经 FastAPI 提供界面，共享同一 SQLite（WAL+FTS5）与 Chroma。审查发现 9 项 Blocker/High 缺陷（见 proposal），核心是：采集器一次 CDP 故障即永久停摆、消息向量按 `(chatId, day)` 覆盖写导致历史召回失真、Web 每请求重建 store 放大双进程锁竞争、接口无错误降级、上传状态卡死、模型加载阻塞且 CPU-only 失败、backfill 死代码、IDB 全量读取无上限。

## Goals / Non-Goals

**Goals:**
- 采集主循环自愈 + 子进程守护 + CDP 失效阈值重建
- 消息向量 per-message 独立入库并清理旧数据
- Web 进程级 store 单例 + 模型后台预热
- reply/search/upload 错误降级；上传状态机完整流转
- use_fp16 按 CUDA 决定；backfill 清理；IDB 分页

**Non-Goals:**
- Medium/Low 级问题（CSRF、Wiki 编辑、chat id 归一、客户匹配并发、父子块展开、检索性能重构）
- 不引入新外部依赖；不实现任何写 WhatsApp 能力

## 用户确认的决策

1. **CDP 重连**：失败计数阈值触发 —— 区分可重试/致命异常，连续 3 次致命失败才重建浏览器
2. **旧向量**：主动清理重建 —— 一次性清空 message_vectors 集合
3. **模型预热**：后台预热 + 超时降级

## 实施设计

### 1. 采集器自愈（B1）

**主循环防护**：`Scanner.run()` 整体包 try/except。异常时写 status（state=error + 错误信息），按指数退避 `min(2^n, 30s)` 后重试下一轮。slow_tick/scan_all_chats/_drain_* 维持各自 try/except 不变。

**CDP 失效阈值重建**：`fast_tick` 捕获异常时分类：
- 可重试（超时、瞬时）：仅退避，计数归零
- 致命（CDP 连接断开、Target closed、context 失效）：`self._cdp_failures += 1`；达 3 次时调用 `self._reconnect()` 重建浏览器

`_reconnect()`：关闭旧 pw/context（尽力），重新 `launch_browser()` 得到新 pw/context/page/cdp，更新 `self.page`/`self.cdp`，重置 `_current_chat_id`/`_last_dom_hash`/`_cdp_failures`。重连失败抛回主循环继续退避。

**分类判定**：Playwright/CDP 异常按消息与类型宽匹配（含 `Target`、`closed`、`Connection`、`session` 等关键词），build 阶段用模拟验证，误判时回退为可重试（不触发重建，仅退避）。

**supervisor 守护**：`app/__main__.py` 改为循环 `Popen`。采集器 exit code 0（正常退出）→ 不重启；非 0（异常）→ 退避 3s 后重启。用户 Ctrl+C（KeyboardInterrupt）终止整个进程组。

**采集器退出码**：`app/collector/__main__.py` 顶层捕获异常并 `sys.exit(1)`；正常结束 `sys.exit(0)`。

### 2. 消息向量 per-message 键（B2）

`scanner._upsert_one` 向量键改为 `f"{chat_id}:{msg.id}"`；`msg.id` 为空/含非法字符时回退原 day 键。Chroma metadata 保持 `{chat_id, day}`。

**旧数据清理**：`ChromaStore` 增加 `clear_message_vectors()`（`msg_col.delete(where={})`）。`scan_all_chats`/首次慢 tick 前调用一次（幂等，仅清空 message_vectors 集合不动 knowledge_chunks）。随扫描重新生成 per-message 向量。

### 3. Web 存储单例 + 模型预热（H1/H5）

**lifespan**：`create_app()` 用 `@asynccontextmanager` lifespan，启动时创建 `app.state.sqlite_store = SqliteStore()`、`app.state.chroma_store = ChromaStore(embedding_fn=...)`，并在 `run_in_executor` 后台预加载 embedding/reranker（`_ensure` 触发，存入进程级缓存）。关闭时 `.conn.close()`。

**路由改造**：`_store()` 改为读 `request.app.state.sqlite_store`（保留签名，参数改从 request 取）；Chroma 实例一律用 `request.app.state.chroma_store`。删除每请求 `ChromaStore(...)` 新建。

**预热就绪等待**：接口首次调用时若模型未就绪，`await loop.run_in_executor` 同步等加载完成（带超时，如 30s）；超时按 D5 降级提示。

### 4. 接口错误降级（H2/H5/M9）

- `/api/reply`、`/api/reply/regenerate`：包 try/except，`generate_reply`/`regenerate_reply` 抛错时 `_render_reply_result(..., error=str(e))`，模板 `reply_result.html` 增加 error 分支显示错误文案，HTTP 200（保持 HTMX 局部刷新不跳 500 页）
- `/api/knowledge/search`：嵌入失败捕获后仅走 BM25 路，结果带 `degraded: "向量检索不可用"` 提示
- `/api/knowledge/upload`：见第 5 节
- `OllamaReranker.rerank`：httpx 异常捕获，返回原序 candidates（不重排）并打 `logging.warning`

### 5. 上传状态机与坏文件处理（H3/H4）

`upload` 重构：
1. 插 documents 行 status=`processing`（format 用 `Path(filename).suffix.lstrip(".")` 修正 L3 不一致）
2. `parse_document` 包 try/except：`ValueError`（未知格式）、空文件、损坏 → `UPDATE documents SET status='failed'`，返回 `{"error": msg}`（HTTP 422）
3. parse 成功但 `chunk_text` 产出 0 chunk → 置 `done`（空文本跳过向量化）
4. `RagIndex.index` 成功 → `UPDATE ... status='done'`；失败 → `status='failed'` + 返回错误
5. Wiki 索引仍 try/except 不阻塞（成功与否不影响 status，RAG 优先）

`RagIndex.index` 返回 chunk 数供状态判断；或 upload 内自行判断 `doc_chunks` 行数。

### 6. use_fp16 按 CUDA（H5）

新增 `app/llm/device.py`（或内联 helper）`use_fp16() -> bool`：`import torch; return torch.cuda.is_available()`。`BgeEmbedding._ensure` 与 `BgeReranker._ensure` 构造 `use_fp16=use_fp16()`。CPU-only 自动 fp32。

### 7. backfill 清理（H6）

- 删除 `_drain_backfill_requests` 中 `data` 死代码块（scanner.py:391-398）
- `backfill_requests` 表定义移入 `schema.sql`（`CREATE TABLE IF NOT EXISTS`，含 `attempts INTEGER DEFAULT 0` 列）；routes.py 的 CREATE 保留为容错（表已存在时 CREATE IF NOT EXISTS 无副作用）
- 表存在性探测：`Scanner.__init__` 检查一次缓存布尔，避免每 2s 轮询抛异常
- 失败不标 done：`backfill_history` 异常时 `attempts = attempts + 1`，`attempts < 3` 保持 `done=0` 可重试；成功后 `done=1`

### 8. IDB 分页（H7）

`_STORE_JS_TEMPLATE` 改游标分页：`st.openCursor()` 循环，累计超过 `settings.max_records_per_store`（默认 20000）即 resolve 停止。`walk_idb` 各 store 独立应用上限。返回体受控，避免单次 CDP 序列化过大。

## 风险 / 取舍

- [双进程 Chroma 锁] → 本 change 仅降频 Web 侧创建；彻底解决需迁移存储，记录为已知限制
- [旧向量清理丢失历史] → 一次性重建，慢 tick/扫描自动补；文档化
- [CDP 异常分类误判] → 宽匹配 + 误判回退可重试，不影响主循环
- [模型预热等待] → 后台线程 + 超时降级，首请求可接受
- [backfill attempts 列迁移] → CREATE IF NOT EXISTS 幂等，旧库自动获得新列

## 测试策略

| 任务组 | 测试 |
|-------|------|
| 自愈 | run 循环异常不退出 + 退避；连续 3 次致命触发重建（mock capture_snapshot 抛错） |
| 向量键 | 同日多消息独立键；无 id 回退 day |
| 单例 | lifespan 创建单例、路由复用 |
| 降级 | reply 失败渲染 error；search 嵌入失败 BM25-only；Ollama 网络失败回退原序 |
| 上传状态机 | 坏文件 failed；正常 done；空文本 done |
| use_fp16 | mock cuda 不可用 → False |
| backfill | 表缺失不抛；失败 attempts+1 不标 done |
| IDB 分页 | 超上限只返回前 N |

回归：`pytest -q` 全量 + `compileall -q app`。

## Migration Plan

1. schema.sql 加 backfill_requests（attempts 列）；幂等 CREATE
2. 逐任务提交（feature 分支）
3. 回归全量测试
4. 回滚：增量改动无破坏性；向量键变更需再清一次 message_vectors 才能回退 day 语义

## Open Questions

- 无（CDP 异常类型在 build 阶段模拟验证，已设计兜底）
