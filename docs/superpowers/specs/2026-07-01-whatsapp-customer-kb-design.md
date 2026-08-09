---
comet_change: whatsapp-customer-kb
role: technical-design
canonical_spec: openspec
archived-with: 2026-08-09-whatsapp-customer-kb
status: final
---

# 外贸客户知识库 — 技术设计

本 Design Doc 基于 OpenSpec change `whatsapp-customer-kb` 的 proposal/design/specs/tasks 深化技术实现。OpenSpec artifacts 是上游事实来源，本文不重定义需求，只描述实现方式、技术风险、测试策略与边界条件。

## 1. 架构与进程模型

```
┌─────────────────────────────────────────────────────────────┐
│  浏览器 (127.0.0.1)                                          │
│  Jinja2+HTMX: 客户列表/画像/聊天/回复/知识管理/Wiki导出       │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP
┌──────────────────────────▼──────────────────────────────────┐
│  Web 进程 (FastAPI + Uvicorn)                                │
│  路由 + 业务编排 (customer/reply/knowledge/whatsapp API)     │
│  读写 SQLite + Chroma，调 LLM/Embedding                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ 共享 SQLite(WAL) + status.json
┌──────────────────────────▼──────────────────────────────────┐
│  采集器进程 (python -m app.collector, 独立)                  │
│  Playwright CDP → WhatsApp Web → IDB+DOM 采集 → 写 SQLite    │
│  状态 + last_heartbeat 写入 status.json                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ CDP
                     ┌─────▼─────┐
                     │  Chrome   │ (独立 user-data-dir)
                     │ WhatsApp  │
                     └───────────┘
```

**关键点**：
- **双进程**：采集器独立进程，崩溃不影响 Web；通过共享 SQLite（WAL 模式：并发读 + 写串行，双进程写靠 `busy_timeout` 重试避免 `SQLITE_BUSY`）+ `status.json` 状态文件通信。Web 进程读 `status.json` 展示采集状态。
- **两层抽象**：
  - 存储层：`StructuredStore`（SQLite 实现）、`VectorStore`（Chroma 实现）
  - 模型层：`LLM`（云端 Claude/OpenAI 实现）、`Embedding`（bge-m3 实现）
- **采集器存活判断**：`status.json` 含 `last_heartbeat` 时间戳，Web 判断超过阈值（30s 未更新）显示"采集器已停止"。
- **启动方式**：一个启动脚本（`python -m app`）同时拉起 Web + 采集器两个进程；也可单独 `python -m app.collector` 跑采集器。
- **包结构**：`app/collector`、`app/storage`、`app/knowledge`、`app/profile`、`app/reply`、`app/web`，各模块单向依赖。

## 2. 数据模型与存储

**SQLite 表（StructuredStore）**：

```
chats        (id, account_id, jid, display_name, kind, last_synced_at)
messages     (id, account_id, chat_id, from_me, sender_jid, ts, type, body, body_present, ingested_at)
             -- 索引: (chat_id, ts) 复合索引支撑"某客户最近N条"
             -- FTS5 虚拟表 messages_fts(body) 支撑关键词全文检索
contacts     (jid, account_id, display_name, phone, updated_at)
customers    (id, display_name, phone, company, country, created_at)
customer_chat_map (account_id, chat_id, customer_id, match_confidence, confirmed, updated_at)
             -- UNIQUE(account_id, chat_id): 一个 chat 当前只归属一个 customer
profiles     (customer_id, field, value, source, updated_at)
             -- UNIQUE(customer_id, field): 单行覆盖语义
             -- source: auto/manual; 自动抽取遇 source=manual 跳过, 人工编辑覆盖并置 manual
documents    (id, filename, format, parser, status, ingested_at)
doc_chunks   (id, doc_id, chunk_idx, text, parent_chunk_id, vector_id)
             -- UNIQUE(doc_id, chunk_idx)
             -- FTS5 虚拟表 doc_chunks_fts(text) 支撑产品知识 BM25 关键词召回
wiki_pages   (id, title, slug, body_md, frontmatter, source_doc_ids, entity_type, updated_at)
             -- UNIQUE(slug)
wiki_links   (from_page_id, to_page_id)  -- wikilink 边
wiki_log_entries (id, page_id, action, ts)  -- 生成/编辑日志
```

**Chroma 集合（VectorStore）**：
- `message_vectors`：按 (chatId, day) 分组的消息摘要向量，metadata 含 `chat_id/day`（`customer_id` 采集时未知，匹配确认后回填）
- `knowledge_chunks`：产品知识 chunk 向量，metadata 含 `doc_id/chunk_idx`
- bge-m3 1024 维

**关键设计**：
- **画像单行覆盖语义**：每个 `(customer_id, field)` 唯一一行，`source` 标记来源。自动抽取时若该字段 `source=manual` 则跳过（不覆盖）；人工编辑直接覆盖并置 `source=manual`。查询按 `(customer_id, field)` 取唯一行，无歧义。
- **客户-chat 映射唯一**：`customer_chat_map` 的 `(account_id, chat_id)` 唯一，一个 chat 当前只归属一个 customer；`match_confidence` + `confirmed` 标记待确认状态。
- **消息全文检索**：`messages` 表建 `(chat_id, ts)` 复合索引支撑分页；`body` 走 FTS5 虚拟表支撑关键词检索与 BM25 召回（不走 LIKE）。
- **向量 metadata 延迟回填**：消息向量化时先写 `chat_id`，`customer_id` 在客户匹配确认后回填；检索时若按客户过滤，通过 `chat_id → customer` 二次解析兜底。
- **幂等键**：messages 按 `(account_id, chat_id, message_id)` upsert；doc_chunks 按 `doc_id+chunk_idx`；wiki_pages 按 `slug`。
- **双写一致性**：采集器写 messages 后，异步触发该 (chatId,day) 摘要向量化入 Chroma；失败不阻塞采集，下次 tick 重试。

## 3. WhatsApp 采集器实现

**采集循环（双 tick）**：

```
collector 进程启动
  ├─ Playwright 启动 Chrome (独立 user-data-dir, 持久登录)
  ├─ 打开 WhatsApp Web, 等待登录 (扫码状态写 status.json)
  └─ 启动两个 asyncio 任务:
       ├─ fast_tick (2s + 随机抖动): DOM 增量
       └─ slow_tick (30s + 随机抖动): IDB 全量校准
```

**fast_tick（DOM 增量）**：
1. `DOMSnapshot.captureSnapshot` 抓取当前可见 `[data-id]` 消息行
2. 计算可见行集合 hash，与上次比较；不变则跳过（空闲不刷屏）
3. 变化则解析行 → 提取 (message_id, body, sender, ts) → 与 IDB 元数据按 id 合并 → upsert messages
4. 触发该 (chatId, day) 异步向量化

**slow_tick（IDB 全量校准）**：
1. CDP `IndexedDB.requestData` 分页读 `model-storage` 的 message/chat/contact/group-metadata stores
2. upsert chats/contacts/messages 元数据（body 留空，等 DOM 合并）
3. 补齐 fast_tick 遗漏的元数据

**按需回溯**（Web API 触发）：
1. 用户在 UI 对某 chat 点"回溯历史"
2. 采集器对该 chat 程序化滚动加载历史 DOM 行
3. 抓取 + 合并 + upsert，直到无更多历史或达上限

**降低封号风险（对齐 openhuman 实践 + 更保守）**：

| 措施 | 做法 | 对齐 |
|------|------|------|
| 只读 | 全程不调用任何发送/输入类 CDP 操作 | openhuman 事实 |
| 持久登录会话 | 独立 user-data-dir 复用 cookie/session，不重复扫码 | openhuman 事实 |
| 纯 CDP 采集 | 走 `DOMSnapshot`/`IndexedDB`，禁止 `Runtime.evaluate` 注入页面 JS | openhuman 事实 |
| 随机抖动 | 轮询间隔 2s±0.5s / 30s±5s | 比openhuman更保守 |
| 单设备单账号 | 不多开 | 基本约束 |
| 记录上限 | slow_tick 单 store 上限 20000 条 | openhuman 事实 |

**ReadOnlyCDP 门面（架构级只读约束）**：
- 采集器只能通过 `ReadOnlyCDP` 门面访问 CDP，该门面仅暴露 `captureSnapshot()` / `requestIndexedDB()` / `evalReadOnly()` 三个只读方法
- 禁止采集器直接持有裸 CDP session，从架构上保证不可能调用发送/输入类操作
- 测试验证"采集器所有 CDP 访问都经 ReadOnlyCDP 门面"

**封号风险定位（基于 openhuman 实践修正）**：
- 根本风险（非官方客户端访问）无法消除
- 实际概率：在"只读 + 持久登录 + 低频 + 抖动"下属较低但非零，参考 openhuman 零封号反馈的实践
- 本方案比 openhuman 更保守（多了随机抖动），不比它风险高

**数据可导出备份**：
- 知识库数据（messages/profiles/documents/wiki）支持导出为 SQLite + Chroma dump + Obsidian vault
- 万一封号不丢积累，可迁移到新账号继续

**登录态持久化**：
- 独立 user-data-dir，重启复用 cookie/session，无需重新扫码
- 首次扫码状态写 `status.json`，Web UI 展示二维码/登录进度

**测试策略**：
- fixture 测试：录制真实 IDB/DOM 快照存为 JSON fixture，采集器逻辑跑 fixture 不连真 WhatsApp
- 合并/去重/upsert 逻辑：单元测试覆盖
- 只读约束：测试验证采集器所有 CDP 访问经 ReadOnlyCDP 门面（白名单机制）

## 4. 知识库与 RAG 管线

**文档处理流水线**：

```
上传文档 → WeKnora docreader 解析 → 切分(chunk_size/overlap + 父子块)
         → 双索引:
              ├─ RAG 索引: bge-m3 向量化 → Chroma knowledge_chunks
              └─ Wiki 索引: 两阶段实体抽取(见下)
```

**RAG 管线（借鉴 WeKnora chat_pipeline 插件式，Python 自研）**：

```
query → 多路召回 → rerank → 上下文压缩/去重 → 父子块展开 → LLM 生成
        (MVP核心)  (MVP核心)      (MVP核心)        (MVP核心)    (MVP核心)

可选插件(默认不挂载, 管线骨架支持): [查询理解] [查询改写/扩展]
```

- **多路召回（MVP 核心）**：并行召回 4 路
  - 客户画像（StructuredStore 直查 profiles 表）
  - 历史聊天向量（Chroma message_vectors，按 chat_id 过滤）
  - 产品知识向量（Chroma knowledge_chunks）
  - BM25 关键词（SQLite FTS5：`messages_fts` + `doc_chunks_fts` 两路并行）
- **rerank（MVP 核心）**：交叉编码器重排（bge-reranker-v2-m3，本地）
- **上下文压缩/父子块展开（MVP 核心）**：去重 + 父子块展开补全上下文
- **查询理解/改写（可选插件）**：管线骨架支持挂载，MVP 默认不启用（非"空实现节点"），未来按需挂入
- 辅助回复与客户分析复用同一管线，不同 prompt + 召回源配置

**Wiki 索引（实体级，两阶段全局去重）**：

```
阶段1: 每文档 LLM 抽取实体候选 (名称/类型/摘要/出处)
阶段2: 全局去重合并
       ├─ 嵌入向量聚类初筛: 实体摘要向量化, 余弦相似度超阈值归为候选对 (O(N) 向量比较)
       ├─ LLM 精判: 仅对候选对判断是否同义 (大幅减少调用, 避免O(N²))
       ├─ 同名实体 → 合并为一个 wiki_page (slug 规范化)
       └─ 冲突处理: LLM 判断不一致时保守不合并
       记录 source_doc_ids (出现在哪些文档)
阶段3: 生成 Markdown 页面 (body_md + frontmatter + [[wikilinks]])
       → 存 wiki_pages/wiki_links → 导出 Obsidian vault
```

- **wikilink 建立**：页面正文引用其他实体时用 `[[slug]]`，导出时 Obsidian graph view 自动建图
- **frontmatter**：`source_docs`/`entity_type`/`updated` 等元数据
- **异步生成**：Wiki 生成不阻塞 RAG 索引，失败不影响 RAG
- **增量更新**：文档重新上传/编辑触发增量重抽取（仅重抽该文档实体候选再并入全局去重）；已有人工编辑的页面（`source=manual` 标记）不被自动重抽覆盖，仅追加新实体

**测试策略**：
- RAG 管线：合成聊天+产品资料 fixture，断言召回相关性 + rerank 排序
- Wiki：合成文档 fixture，断言实体抽取/去重/wikilink 正确性
- 导出：断言导出的 vault 文件结构 + wikilink 可被 Obsidian 解析

## 5. 客户画像与匹配

- 画像字段：姓名/公司/国家/产品兴趣/询价历史/沟通偏好/语言/成交阶段等，存 SQLite `profiles` 表（纵向单行覆盖语义，见第 2 节）。
- 抽取：LLM 从该客户近期聊天摘要中抽取/更新画像字段（增量，带时间戳）；遇 `source=manual` 字段跳过。
- 匹配：WhatsApp chatId/JID → 客户实体。MVP 用手机号 + 显示名启发式匹配，人工确认合并；`customer_chat_map` 的 `(account_id, chat_id)` 唯一。预留"外部客户数据源"接口供未来导入客户清单/CRM。
- 画像可手动编辑修正，编辑值优先于自动抽取值（置 `source=manual`）。

## 6. Web 应用

- FastAPI + Uvicorn（单进程，本地 127.0.0.1）。
- 前端 MVP 用 Jinja2 + HTMX（轻量、无需前端构建链），未来可换 SPA。
- 页面：客户列表、客户画像（可编辑）、聊天浏览、回复生成面板、本地知识管理（上传/列表/删除/检索测试 + Wiki 页面浏览编辑 + Obsidian vault 导出）、采集器状态。
- 采集器作为独立进程运行（见第 1 节），Web 通过 `status.json` 读其状态。

## 7. 技术风险与缓解

- **[WhatsApp 封号风险]** → 只读 + 持久登录 + 低频 + 抖动（对齐 openhuman 实践）；ReadOnlyCDP 门面架构级保证只读；数据可导出备份；文档化风险提示用户用小号试跑。
- **[CDP 采集脆弱：WhatsApp Web DOM/IDB 结构变更]** → 采集器与解析逻辑隔离，DOM 选择器与 IDB store 名集中配置、可快速修补；参考 openhuman 的持续维护经验。
- **[明文正文依赖 DOM 可见性]** → 慢 tick IDB 校准 + 按需回溯滚动加载；未渲染的历史消息正文可能缺失，作为已知限制。
- **[复用 WeKnora 解析器的许可证与维护]** → WeKnora MIT，保留原始版权声明；解析器作为 vendored 代码或子模块，跟随上游修复。
- **[RAG 管线自研工作量]** → MVP 先实现核心子集（多路召回+rerank+生成），查询改写等作为可选插件预留；借鉴 WeKnora 设计降低设计成本。
- **[Wiki 生成成本与质量]** → Wiki 异步生成、可人工编辑修正；近义实体用嵌入聚类初筛+LLM 精判避免 O(N²) 调用；图谱查看复用 Obsidian（无需自研前端）；Wiki 与 RAG 同源、可独立开关，Wiki 生成失败不影响 RAG 检索。
- **[Obsidian 依赖]** → Obsidian 仅作外部查看器（用户本地安装，免费无 Docker），非项目运行依赖；项目仅负责生成与导出 vault，导出格式为标准 Markdown + wikilinks，即使无 Obsidian 也可用任意 Markdown 编辑器查看。
- **[客户聊天内容经云端 LLM]** → 用户已确认接受；LLM 层抽象，未来可切本地模型；不在日志中持久化原始 prompt 明文。
- **[扫描件 OCR 质量]** → OCR 接口可替换，扫描件占比高时再增强；MVP 先保证电子版表格解析。
- **[双进程 SQLite 写冲突]** → WAL 模式 + busy_timeout 重试；写串行但读并发。

## 8. 测试策略总览

- **单元测试**：合并/去重/upsert 逻辑、画像单行覆盖语义、客户-chat 映射唯一、Wiki 实体去重。
- **fixture 集成测试**：采集器跑录制 IDB/DOM fixture；RAG 管线跑合成聊天+产品资料 fixture；Wiki 跑合成文档 fixture。
- **只读约束测试**：采集器所有 CDP 访问经 ReadOnlyCDP 门面（白名单）。
- **导出测试**：Obsidian vault 文件结构 + wikilink 可解析。
- **不连真 WhatsApp**：所有测试可自动化、CI 可跑、无封号风险。

## 9. Spec Patch 说明

审查中发现 `knowledge-base` capability 的"Wiki 页面生成与管理"需求未明确"增量更新不覆盖人工编辑"这一边界。已在 OpenSpec delta spec `specs/knowledge-base/spec.md` 补充 scenario：文档更新触发增量重抽取，已有人工编辑页面不被自动覆盖。

## 10. 待定项（build 阶段细化）

- chunk_size/overlap 默认值、父子块策略参数
- Wiki 实体去重的嵌入相似度阈值
- rerank top_k、上下文压缩 token 上限
- OCR 接口默认实现（PaddleOCR vs 云端 vision）的最终选型
