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
