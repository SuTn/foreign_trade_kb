## Context

全新 Python 项目，目标是一个本地外贸客户知识库。业务员已确认接受 WhatsApp 非官方接入的封号风险，并接受客户聊天内容经云端 LLM 处理。技术栈定为 Python，先个人本地单机，架构预留扩展点。

关键约束与已确认决策：
- WhatsApp 接入：参考 openhuman 的 `whatsapp_scanner`（CDP 读 IndexedDB `model-storage` 库的 message/chat/contact/group-metadata stores 拿元数据 + DOM 快照拿明文正文，按 id 合并），用 Python + Playwright 复刻。openhuman 是 Tauri(Rust)+CEF，本项目是独立 Python 进程驱动独立 Chrome。
- 知识库/RAG：用户明确不要 Docker、要抽取自建。复用 WeKnora `docreader/parser/` 的 Python 文档解析器；RAG 编排借鉴 WeKnora `chat_pipeline` 插件式管线设计，用 Python（LangChain 原语）自研。即 RAG 编排层自己写，但解析器复用、管线设计有参考蓝图。知识库同时支持 RAG 索引（文档切片向量化、面向问-答检索）与 Wiki 索引（Agent 从本地外贸资料抽取实体/概念生成互联 Markdown 页面、面向体系化浏览），两者可在同一知识库叠加、数据同源；本地外贸资料既进 RAG 供检索，也进 Wiki 供体系化浏览。Wiki 页面以 Obsidian vault 格式（Markdown + `[[wikilinks]]` + frontmatter）导出，用户用本地 Obsidian 打开即可查看知识图谱（Obsidian graph view 基于 wikilinks 自动构建，本地免费无需 Docker），项目自身不实现图谱可视化前端。
- 历史范围：实时同步新消息 + 按需回溯（不做首次全量回溯）。
- LLM：云端 API（Claude/OpenAI）。
- 交互：本地 Web 应用（FastAPI + 前端）。

## Goals / Non-Goals

**Goals:**
- WhatsApp 聊天数据持续同步入库，结构化可精确查询 + 向量化可语义检索（双写）。
- 每个客户有可查看、可编辑的用户画像，并能给出客户分析。
- 本地产品资料（PDF/Word/Excel/CSV/文本/网页）可导入、解析（含表格与扫描页 OCR 路由）、向量化、混合检索（RAG 索引）；同时由 Agent 抽取实体/概念生成互联 Markdown Wiki 页面（Wiki 索引），导出为 Obsidian vault 供体系化浏览与图谱查看。
- 辅助回复：基于客户画像 + 历史聊天 + 产品知识 RAG 生成建议回复，仅生成不自动发送。
- 纯 Python 本地运行，无需 Docker；架构分层清晰，存储与 LLM 可替换，预留多用户扩展点。

**Non-Goals:**
- 多用户/权限系统（仅预留扩展点，MVP 单机单用户）。
- 自动发送 WhatsApp 消息（降低封号风险）。
- WhatsApp Business Cloud API 接入。
- 语音/图片/视频消息的深度内容解析（先存元数据，正文以文本消息为主）。
- 首次全量历史回溯。
- 自研文档解析器与从零造 RAG 轮子（解析器复用 WeKnora，管线设计借鉴 WeKnora）。
- 自研知识图谱可视化前端；Wiki 图谱查看复用 Obsidian graph view（项目仅导出 Obsidian vault，不实现自带图谱可视化）。
- Neo4j 图数据库集成；MVP 知识图谱通过 Obsidian graph view 查看，Neo4j 作为未来可选增强。

## Decisions

### D1. 整体分层架构

```
┌─────────────────────────────────────────────────────────┐
│  Web UI (FastAPI + Jinja2/HTMX, 本地浏览器)              │
│  客户列表 / 画像 / 聊天浏览 / 回复生成 / 知识管理        │
└──────────────────────────┬──────────────────────────────┘
                           │ HTTP
┌──────────────────────────▼──────────────────────────────┐
│  Application 层 (FastAPI 路由 + 业务编排)                │
│  customer / reply / knowledge / whatsapp API            │
└──┬──────────────┬──────────────┬──────────────┬─────────┘
   │              │              │              │
┌──▼─────┐  ┌────▼─────┐  ┌────▼─────┐  ┌────▼─────────┐
│WhatsApp│  │ Profile  │  │ Knowledge│  │ Reply Assist │
│采集器  │  │ & 分析   │  │ Base/RAG │  │ (RAG 生成)   │
│(CDP)   │  │          │  │          │  │              │
└──┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────────┘
   │              │              │              │
   │   ┌──────────┴──────────────┴──────────────┘
   │   │
┌──▼───▼──────────────────────────────────────────────────┐
│  Storage 层 (抽象接口, 便于未来替换/扩展)                │
│  ├─ SQLite: chats/messages/contacts/profiles (精确查询) │
│  └─ 向量库: 消息向量 + 知识 chunk 向量 (语义检索)        │
└─────────────────────────────────────────────────────────┘
   │
┌──▼──────────────────────────────────────────────────────┐
│  LLM 层 (抽象接口: 默认云端 Claude/OpenAI, 可切本地)     │
└─────────────────────────────────────────────────────────┘
```

**理由**：分层 + 存储与 LLM 抽象接口，满足"先个人、未来扩展小团队"的预留诉求，且让 WhatsApp 采集器与 RAG 层解耦，各自可独立演进。

### D2. WhatsApp 采集器（CDP + IDB + DOM）

复刻 openhuman `whatsapp_scanner` 思路，Python 实现：
- 用 Playwright 以 CDP 模式启动/连接独立 Chrome，打开 WhatsApp Web，扫码登录（持久化 user-data-dir 保持登录态）。
- **IDB walk**：通过 CDP `IndexedDB` 域读取 `model-storage` 库的 message/chat/contact/group-metadata stores，分页拉取消息元数据（id/chatId/fromMe/from/timestamp/type，正文在 IDB 中加密，不取）。
- **DOM 快照**：通过 `DOMSnapshot.captureSnapshot` 抓取渲染态 `[data-id]` 消息行的明文正文，按消息 id 与 IDB 元数据合并。
- **双 tick**：快 tick（~2s）DOM 增量抓新消息；慢 tick（~30s）IDB 全量校准。仅当可见行 hash 变化时才发增量，避免空闲刷屏。
- **按需回溯**：对指定聊天，程序化滚动加载历史 DOM 行再抓取（MVP 手动触发，不做首次全量）。
- **幂等 upsert**：按 (account_id, chat_id, message_id) 去重写 SQLite；按 (chatId, day) 分组写向量库。
- **降低封号风险**：单设备、拟人化轮询间隔 + 随机抖动、不自动发消息、不高频全量扫描。

**备选**：Baileys（Node，更成熟但跨语言、ToS 风险类似）；官方 Business Cloud API（合规但读不到个人号历史、需换企业号）。选 CDP 因能读历史、可实时、与 Python 栈一致、有 openhuman 成熟参考。

### D3. 存储双路径（参考 openhuman 双写设计）

- **结构化路径（SQLite）**：`chats` / `messages` / `contacts` / `profiles` 表，支撑精确查询（"列出聊天""看与某客户最近 50 条""搜 invoice 关键词"）和画像字段存储。
- **语义路径（向量库）**：消息按 (chatId, day) 分组向量化；知识 chunk 向量化。支撑语义检索与 RAG 召回。
- 双写在每次采集 tick 触发，幂等键保证可重试。一条路径失败不影响另一条，下次 tick 收敛。

- 向量库 MVP 用 Chroma（嵌入式、零运维、pip 即用，最贴合个人本地）；存储层抽象接口，未来可迁 Qdrant/pgvector。结构化数据始终用 SQLite。
- 知识库索引策略：RAG（向量+BM25+rerank）+ Wiki（Agent 生成互联 Markdown 页面，导出 Obsidian vault 供图谱查看）双索引，可叠加；知识库层抽象为可挂多索引策略接口。

### D4. 知识库构建（复用 WeKnora docreader 解析器，RAG + Wiki 双索引）

- 直接复用 WeKnora `docreader/parser/` 的 Python 解析器：`excel_parser.py`（pandas+openpyxl，合并单元格填充、行级 KV 化）、`pdf_parser.py`（版面感知 XY-cut、扫描页检测路由 OCR）、Word/CSV/网页解析器。
- 扫描页 OCR：WeKnora 路由到外部 OCR/VLM（PaddleOCR-VL）；MVP 先接一个可替换的 OCR 接口（默认 PaddleOCR 或云端 vision），扫描件占比高时再增强。
- 切分：支持 chunk_size/overlap 配置 + 父子分块（借鉴 WeKnora 自适应切分）。
- 向量化：embedding 模型可配（默认一个多语言模型，外贸中英混合）。
- **双索引策略**（借鉴 WeKnora 可叠加索引策略）：
  - **RAG 索引**：文档 → 解析 → 切分 → 向量化 → chunk 检索（向量 + BM25 + rerank），面向问-答检索，供辅助回复/客户分析/产品检索召回。
  - **Wiki 索引**：Agent 从本地外贸资料异步抽取实体/核心概念，自动生成结构化、相互链接的 Markdown Wiki 页面，面向体系化浏览。Wiki 页面与 RAG chunk 同源（同一批解析后的文档），可独立开关。
  - 两者可在同一知识库叠加；知识库层抽象为可挂多种索引策略的接口。
- **Wiki 图谱查看（复用 Obsidian）**：Wiki 页面以 Obsidian vault 格式导出——每个页面一个 Markdown 文件，含 YAML frontmatter（来源文档、实体类型等元数据）与 `[[wikilinks]]`（页面间链接）。用户用本地 Obsidian 打开导出文件夹即可查看知识图谱（Obsidian graph view 基于 `[[wikilinks]]` 自动构建节点与边，本地免费无需 Docker）。项目自身不实现图谱可视化前端，仅负责生成与导出 vault。
- **Wiki 生成与导出流程**：上传文档 → 解析 → 异步触发 LLM 抽取实体/概念 → 生成 Markdown 页面（含 wikilinks 与 frontmatter）→ 存 SQLite（wiki_pages/wiki_log_entries/wiki_config 表）→ 一键导出为 Obsidian vault 文件夹。支持人工编辑/管理页面（编辑后可重新导出）。

**理由**：用户明确要复用而非自研解析器；WeKnora docreader 是 Python、MIT、解析质量在开源 RAG 中属上游，可直接 cherry-pick。本地外贸资料既需要"问-答检索"（RAG，辅助回复时召回产品知识），也需要"体系化浏览"（Wiki，把分散资料蒸馏成互联知识页面便于业务员查阅），故 RAG + Wiki 双索引都做。Wiki 图谱查看复用 Obsidian（本地免费、graph view 基于 wikilinks 自动构建、生态成熟），避免自研图谱可视化前端；Obsidian 仅作外部查看器，非项目运行依赖。Neo4j 作为未来可选增强（当需要更复杂图查询时再引入）。

### D5. RAG 编排（借鉴 WeKnora chat_pipeline 插件式设计，Python 自研）

借鉴 WeKnora 的插件式事件管线，用 Python + LangChain 原语实现一条可配置管线：
```
query → 查询理解 → 查询改写/扩展(召回不足时生成变体并发检索)
      → 多路召回(客户画像 + 历史聊天向量 + 产品知识向量 + BM25)
      → rerank(交叉编码器/云端 rerank)
      → 上下文压缩/去重 → 父子块展开
      → LLM 生成(回复建议 / 客户分析)
```
- 每个环节是可插拔插件，MVP 先实现核心子集（多路召回 + rerank + 生成），其余作为扩展点。
- 辅助回复与客户分析复用同一管线，不同 prompt 与召回源配置。
- LangChain 提供 Retriever/Splitter/Embeddings/VectorStore/Reranker 原语加速，管线骨架与插件接口自研。

**理由**：用户要"借鉴别人 RAG 流程不自己开发一套"，但不要 Docker、要抽取自建——解析器复用 + 管线设计借鉴 + LangChain 原语，是"不造轮子但全 Python 自控"的平衡点。备选纯 LangChain 自建（设计参考少）或 WeKnora sidecar（要 Docker，已排除）。

### D6. 客户画像与匹配

- 画像字段：姓名/公司/国家/产品兴趣/询价历史/沟通偏好/语言/成交阶段等，存 SQLite `profiles` 表。
- 抽取：LLM 从该客户近期聊天摘要中抽取/更新画像字段（增量，带时间戳）。
- 匹配：WhatsApp chatId/JID → 客户实体。MVP 用手机号 + 显示名启发式匹配，人工确认合并。
- 画像可手动编辑修正，编辑值优先于自动抽取值（带来源标记）。

### D7. Web 应用

- FastAPI + Uvicorn（单进程，本地 127.0.0.1）。
- 前端 MVP 用 Jinja2 + HTMX（轻量、无需前端构建链），未来可换 SPA。
- 页面：客户列表、客户画像（可编辑）、聊天浏览、回复生成面板、本地知识管理（上传/列表/检索测试）。
- WhatsApp 采集器作为后台任务（asyncio）与 FastAPI 同进程运行，状态通过 API 暴露。

## Risks / Trade-offs

- **[WhatsApp 封号风险]** → 单设备、拟人化轮询+随机抖动、不自动发消息、不高频全量扫描；登录态持久化减少扫码；文档化风险提示用户。
- **[CDP 采集脆弱：WhatsApp Web DOM/IDB 结构变更]** → 采集器与解析逻辑隔离，DOM 选择器与 IDB store 名集中配置、可快速修补；参考 openhuman 的持续维护经验。
- **[明文正文依赖 DOM 可见性]** → 慢 tick IDB 校准 + 按需回溯滚动加载；未渲染的历史消息正文可能缺失，作为已知限制。
- **[复用 WeKnora 解析器的许可证与维护]** → WeKnora MIT，保留原始版权声明；解析器作为 vendored 代码或子模块，跟随上游修复。
- **[RAG 管线自研工作量]** → MVP 先实现核心子集（多路召回+rerank+生成），插件接口预留扩展；借鉴 WeKnora 设计降低设计成本。
- **[Wiki 生成成本与质量]** → Wiki 异步生成、可人工编辑修正；图谱查看复用 Obsidian（无需自研前端）；Wiki 与 RAG 同源、可独立开关，Wiki 生成失败不影响 RAG 检索。
- **[Obsidian 依赖]** → Obsidian 仅作外部查看器（用户本地安装，免费无 Docker），非项目运行依赖；项目仅负责生成与导出 vault，导出格式为标准 Markdown + wikilinks，即使无 Obsidian 也可用任意 Markdown 编辑器查看。
- **[客户聊天内容经云端 LLM]** → 用户已确认接受；LLM 层抽象，未来可切本地模型；不在日志中持久化原始 prompt 明文。
- **[扫描件 OCR 质量]** → OCR 接口可替换，扫描件占比高时再增强；MVP 先保证电子版表格解析。

## Open Questions

- 向量库 MVP 用 Chroma 还是直接 sqlite-vec（更轻、与 SQLite 同栈）？倾向 Chroma（生态成熟），design 阶段可定。
- embedding 模型默认选型（多语言、可本地运行）？design 阶段定。
- 客户匹配策略是否需要导入现有客户清单（Excel）做种子匹配？MVP 可选。
- 知识图谱未来是否引入 Neo4j（当需要更复杂图查询、或 Obsidian graph view 不够用时）？延后到增强阶段决定。
