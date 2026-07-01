# Comet Design Handoff

- Change: whatsapp-customer-kb
- Phase: design
- Mode: compact
- Context hash: e243c6d7275db9623eb5c02f91a867e10f384262f73e100ea1d6b236f78059de

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/whatsapp-customer-kb/proposal.md

- Source: openspec/changes/whatsapp-customer-kb/proposal.md
- Lines: 1-31
- SHA256: 6220aa76a7a757a72d88e20350588ca32fc1edde6e359d03c109e691f19946e5

```md
## Why

外贸业务员的客户沟通高度依赖 WhatsApp，但聊天记录散落在手机/网页端、难以检索和回顾，更无法跨客户做分析。业务员需要一个本地客户知识库：把 WhatsApp 聊天数据同步进来，自动形成每个客户的用户画像，辅助分析客户和回复消息，并能把本地产品资料（PDF/Word/Excel 等）导入丰富知识库，作为回复和检索的产品知识源。现在做是因为业务员已确认接受非官方 WhatsApp 接入方案的封号风险，且开源生态（WeKnora 的文档解析与 RAG 管线设计、LangChain 原语）已足够成熟，可以抽取复用而非从零自研整套 RAG。

## What Changes

- 新增 WhatsApp 聊天数据采集与同步能力：通过 CDP 驱动本地 Chrome 打开 WhatsApp Web，读取 IndexedDB 消息元数据 + DOM 快照明文正文并按消息 id 合并（参考 openhuman 的 `whatsapp_scanner` 方案），实时轮询同步新消息，支持对指定聊天手动触发滚动回溯历史；按 (chatId, day) 分组幂等 upsert。
- 新增客户画像能力：从聊天记录抽取并维护每个客户的画像字段（姓名/公司/国家/产品兴趣/询价历史/沟通偏好/语言/成交阶段等），支持手动编辑修正；基于画像 + 聊天摘要给出客户分析（兴趣点、活跃度、跟进建议）。
- 新增本地知识库能力：导入 PDF/Word/Excel/CSV/纯文本/网页，复用 WeKnora `docreader` 的 Python 解析器做文档解析（含表格识别、扫描页 OCR 路由）。知识库同时支持两种索引策略：**RAG 索引**（切分 + 向量化 + 混合检索 BM25/向量 + rerank，面向问-答检索，借鉴 WeKnora 插件式 RAG 管线设计，用 Python + LangChain 原语自研编排）与 **Wiki 索引**（Agent 从本地外贸资料抽取实体/概念，自动生成互联 Markdown Wiki 页面，面向体系化浏览）。两者可在同一知识库叠加，数据同源。Wiki 页面以 Obsidian vault 格式（Markdown + `[[wikilinks]]` + frontmatter）导出，用户用本地 Obsidian 打开即可查看知识图谱（Obsidian graph view 基于 wikilinks 自动构建，本地免费无需 Docker）。
- 新增辅助回复能力：RAG 生成回复建议——检索该客户画像 + 相关历史聊天 + 本地产品知识，结合当前消息生成建议回复（仅生成不自动发送）。
- 新增本地 Web 应用：FastAPI 后端 + 前端页面，本地浏览器访问，提供客户列表、客户画像页、聊天浏览、回复生成、本地知识管理。

## Capabilities

### New Capabilities
- `whatsapp-sync`: WhatsApp Web 聊天数据采集与同步（CDP 采集、IDB+DOM 合并、实时同步、按需回溯、幂等 upsert）
- `customer-profile`: 客户画像抽取与维护、客户分析
- `knowledge-base`: 本地知识导入、文档解析（复用 WeKnora docreader）、RAG 索引（切分向量化 + 混合检索，借鉴 WeKnora RAG 管线设计）+ Wiki 索引（Agent 生成互联 Markdown 页面，导出 Obsidian vault 供图谱查看），两者可叠加
- `reply-assist`: RAG 辅助回复生成（仅生成不自动发送）
- `web-app`: 本地 Web 应用（FastAPI + 前端，客户列表/画像/聊天浏览/回复/知识管理）

### Modified Capabilities
<!-- 无现有 spec，本项目为全新项目 -->

## Impact

- **新增代码**：全新 Python 项目，包含 WhatsApp 采集器、存储层、知识库（RAG + Wiki 双索引）/RAG 层、画像/分析/回复层、Web 应用。
- **外部依赖**：Playwright（CDP 驱动 Chrome）、FastAPI + Uvicorn、SQLite（结构化存储）、向量库（待定，Chroma/Qdrant）、LangChain（RAG 编排原语）、文档解析库（pypdfium2/openpyxl/python-docx/pandas 等，对齐 WeKnora docreader）、LLM SDK（anthropic/openai）。Wiki 图谱查看依赖用户本地安装 Obsidian（免费、无 Docker，仅作为外部查看器，非项目运行依赖）。
- **复用来源**：openhuman 的 `whatsapp_scanner`（CDP+IDB+DOM 采集逻辑，参考实现，Rust→Python 复刻）；WeKnora 的 `docreader/parser/`（Python 文档解析器，可直接复用）、`chat_pipeline` 插件式 RAG 管线（设计借鉴，Python 重写）与 Wiki 索引策略（Agent 抽取实体生成互联 Markdown 页面，设计借鉴，Python 重写；Wiki 页面以 Obsidian vault 格式导出，复用 Obsidian graph view 做图谱可视化）。
- **风险**：WhatsApp 非官方接入违反 ToS，有封号风险（单设备、拟人化使用可降低）；客户聊天内容经云端 LLM 处理（用户已确认接受）。
- **部署**：纯 Python 本地运行，无需 Docker；先个人单机，架构预留扩展点（存储层抽象）供未来小团队扩展。
```

## openspec/changes/whatsapp-customer-kb/design.md

- Source: openspec/changes/whatsapp-customer-kb/design.md
- Lines: 1-151
- SHA256: b3635cf3e96892ac97b13f8ba1802bde8e08fbf5e5573430dcd5f43bd9a55b61

[TRUNCATED]

```md
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
```

Full source: openspec/changes/whatsapp-customer-kb/design.md

## openspec/changes/whatsapp-customer-kb/tasks.md

- Source: openspec/changes/whatsapp-customer-kb/tasks.md
- Lines: 1-79
- SHA256: 052d7b203b7ae25b167ad7dbde49f4df543bc7f821f944766e8ba71e162fbfad

```md
## 1. 项目骨架与基础设施

- [ ] 1.1 初始化 Python 项目结构（src/ 包结构、pyproject.toml、依赖锁定）
- [ ] 1.2 添加核心依赖（fastapi/uvicorn/playwright/langchain/sqlite/chroma/embedding/llm sdk/文档解析库）
- [ ] 1.3 配置 Playwright 与独立 Chrome user-data-dir，验证可打开 WhatsApp Web
- [ ] 1.4 建立配置层（DOM 选择器、IDB store 名、轮询间隔、LLM/embedding 配置集中管理）

## 2. 存储层

- [ ] 2.1 定义存储层抽象接口（结构化存储 + 向量存储 + LLM 接口）
- [ ] 2.2 实现 SQLite 结构化存储（chats/messages/contacts/profiles 表与 upsert/查询）
- [ ] 2.3 接入 Chroma 向量库（消息按 chatId+day 分组、知识 chunk 向量，幂等 upsert）
- [ ] 2.4 实现 LLM 抽象层（默认云端 Claude/OpenAI，可切本地）

## 3. WhatsApp 采集器

- [ ] 3.1 实现 CDP 连接与 WhatsApp Web 登录态持久化（扫码状态暴露给 UI）
- [ ] 3.2 实现 IDB walk（读 model-storage 的 message/chat/contact/group-metadata stores，分页拉元数据）
- [ ] 3.3 实现 DOM 快照明文正文抓取（DOMSnapshot.captureSnapshot 抓 [data-id] 行）
- [ ] 3.4 实现元数据与正文按消息 id 合并
- [ ] 3.5 实现快 tick（~2s）DOM 增量同步（可见行 hash 变化才产出）
- [ ] 3.6 实现慢 tick（~30s）IDB 全量校准
- [ ] 3.7 实现按需历史回溯（指定聊天手动触发滚动加载采集）
- [ ] 3.8 实现幂等 upsert（按 account_id+chat_id+message_id 与 chatId+day）
- [ ] 3.9 加入拟人化轮询抖动与只读约束（不发送消息）

## 4. 知识库构建（复用 WeKnora docreader，RAG + Wiki 双索引）

- [ ] 4.1 引入 WeKnora docreader 解析器（vendored 或子模块，保留版权声明）
- [ ] 4.2 适配 Excel/CSV 解析（多 sheet、合并单元格填充、行级 KV 化）
- [ ] 4.3 适配 PDF 解析（XY-cut 阅读顺序、标题识别、表格抽取）
- [ ] 4.4 适配扫描页 OCR 路由（可替换 OCR 接口，默认 PaddleOCR/云端 vision）
- [ ] 4.5 适配 Word/纯文本/网页解析
- [ ] 4.6 实现切分（chunk_size/overlap 可配 + 父子分块）与向量化入库（RAG 索引）
- [ ] 4.7 定义知识库索引策略抽象接口（可挂 RAG/Wiki 等多策略，可独立开关）
- [ ] 4.8 实现 Wiki 索引：Agent 异步抽取实体/概念生成互联 Markdown 页面（SQLite wiki_pages/wiki_log_entries/wiki_config 表，页面含 wikilinks 与 frontmatter）
- [ ] 4.9 实现 Wiki 页面 Obsidian vault 导出（每页一个 Markdown 文件 + YAML frontmatter + [[wikilinks]]，导出到文件夹）
- [ ] 4.10 实现 Wiki 页面人工编辑/管理（持久化编辑、保留生成来源标记，编辑后可重新导出）
- [ ] 4.11 验证 Wiki 生成失败不影响 RAG 索引（双索引互不阻塞）

## 5. RAG 管线（借鉴 WeKnora chat_pipeline，Python 自研）

- [ ] 5.1 设计可插拔插件接口与管线骨架（事件链式调用）
- [ ] 5.2 实现多路召回（客户画像 + 历史聊天向量 + 产品知识向量 + BM25）
- [ ] 5.3 实现 rerank（交叉编码器/云端 rerank）
- [ ] 5.4 实现上下文压缩/去重与父子块展开
- [ ] 5.5 预留查询理解/改写插件扩展点（MVP 可空实现）

## 6. 客户画像与分析

- [ ] 6.1 实现客户实体匹配（手机号 + 显示名启发式 + 人工确认合并）
- [ ] 6.2 实现 LLM 画像抽取/更新（从聊天摘要抽取字段，带时间戳与来源）
- [ ] 6.3 实现画像手动编辑（人工值优先，不被自动抽取覆盖）
- [ ] 6.4 实现客户分析生成（兴趣点/活跃度/跟进建议）

## 7. 辅助回复

- [ ] 7.1 实现回复生成（RAG 召回画像+历史+产品知识 + 当前消息 → 建议回复）
- [ ] 7.2 实现检索来源可追溯（展示支撑回复的片段）
- [ ] 7.3 实现多候选回复（重新生成获得不同候选）
- [ ] 7.4 确保仅生成不自动发送

## 8. Web 应用

- [ ] 8.1 搭建 FastAPI + Jinja2/HTMX 骨架与本地 127.0.0.1 启动
- [ ] 8.2 实现客户列表页与客户画像页（可编辑）
- [ ] 8.3 实现聊天浏览页（分页展示消息 + 触发回复生成）
- [ ] 8.4 实现本地知识管理页（上传/列表/删除/检索测试 + Wiki 页面浏览编辑 + Obsidian vault 导出）
- [ ] 8.5 实现采集器状态展示（连接/登录/最近同步时间）
- [ ] 8.6 采集器作为后台 asyncio 任务与 FastAPI 同进程运行

## 9. 集成与验证

- [ ] 9.1 端到端联调：WhatsApp 同步 → 画像 → 回复生成 全链路
- [ ] 9.2 端到端联调：本地知识导入 → RAG 检索 → 回复引用产品知识
- [ ] 9.3 端到端联调：本地知识导入 → Wiki 页面生成 → 导出 Obsidian vault → 图谱查看
- [ ] 9.4 验证幂等与去重（重复采集不产生重复记录）
- [ ] 9.5 验证只读约束（全程不发送任何 WhatsApp 消息）
- [ ] 9.6 文档化封号风险提示与使用说明
```

## openspec/changes/whatsapp-customer-kb/specs/customer-profile/spec.md

- Source: openspec/changes/whatsapp-customer-kb/specs/customer-profile/spec.md
- Lines: 1-37
- SHA256: b716acd0b7e1e061d43656e15bd5f0bca90d23db0059de8e59daa553f0927cd9

```md
## ADDED Requirements

### Requirement: 客户画像字段维护
系统 SHALL 为每个客户维护画像字段（姓名/公司/国家/产品兴趣/询价历史/沟通偏好/语言/成交阶段等），存储于结构化存储，并支持手动编辑修正。

#### Scenario: 自动抽取画像
- **WHEN** 某客户有新增聊天内容并触发画像更新
- **THEN** 系统 SHALL 由 LLM 从该客户近期聊天摘要中抽取/更新画像字段，带时间戳与来源标记

#### Scenario: 手动编辑优先
- **WHEN** 用户手动编辑某画像字段
- **THEN** 该字段 SHALL 以用户编辑值为准（标记为人工来源），不被后续自动抽取覆盖

### Requirement: 客户实体匹配
系统 SHALL 将 WhatsApp chatId/JID 关联到客户实体，MVP 采用手机号 + 显示名启发式匹配并支持人工确认合并。

#### Scenario: 启发式匹配
- **WHEN** 采集到新聊天
- **THEN** 系统 SHALL 用手机号 + 显示名启发式匹配现有客户实体，匹配不确定时标记为待确认

#### Scenario: 人工合并
- **WHEN** 用户确认两个聊天属于同一客户
- **THEN** 系统 SHALL 合并其消息与画像到同一客户实体

### Requirement: 客户分析
系统 SHALL 基于客户画像 + 聊天摘要给出客户分析，包括兴趣点、活跃度、跟进建议。

#### Scenario: 生成客户分析
- **WHEN** 用户在客户画像页请求分析
- **THEN** 系统 SHALL 基于该客户画像与近期聊天摘要生成分析（兴趣点/活跃度/跟进建议）并展示

### Requirement: 画像可查看
系统 SHALL 在 Web UI 提供客户画像页，展示画像字段、来源标记与最近更新时间。

#### Scenario: 查看画像
- **WHEN** 用户打开某客户画像页
- **THEN** 系统 SHALL 展示该客户全部画像字段、各字段来源（自动/人工）与最近更新时间
```

## openspec/changes/whatsapp-customer-kb/specs/knowledge-base/spec.md

- Source: openspec/changes/whatsapp-customer-kb/specs/knowledge-base/spec.md
- Lines: 1-113
- SHA256: 7955f71d4efdcf44b31e638014e0d7913d1379707b15574ced6977f1ede447d3

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: 多格式文档导入与解析
系统 SHALL 支持导入 PDF/Word/Excel/CSV/纯文本/网页，复用 WeKnora docreader 的 Python 解析器进行解析，含表格识别与扫描页 OCR 路由。

#### Scenario: Excel 表格解析
- **WHEN** 用户导入 Excel 文件
- **THEN** 系统 SHALL 解析多 sheet、填充合并单元格、按行 KV 化产出结构化文本

#### Scenario: PDF 版面感知解析
- **WHEN** 用户导入电子版 PDF
- **THEN** 系统 SHALL 按 XY-cut 重建阅读顺序、识别标题、抽取正文与表格

#### Scenario: 扫描页 OCR 路由
- **WHEN** 导入的 PDF 含扫描页（图像占比超阈值）
- **THEN** 系统 SHALL 将该页路由到 OCR 接口识别文本，与电子版文本合并

#### Scenario: Word/CSV/文本/网页解析
- **WHEN** 用户导入对应格式文件
- **THEN** 系统 SHALL 解析为文本并保留必要结构

### Requirement: 切分与向量化
系统 SHALL 对解析后的文本按可配置 chunk_size/overlap 切分（支持父子分块），并向量化入库。

#### Scenario: 切分入库
- **WHEN** 文档解析完成
- **THEN** 系统 SHALL 按配置切分为 chunk、向量化后写入向量库，记录来源文档与位置元数据

### Requirement: 混合检索
系统 SHALL 提供混合检索（BM25 关键词 + 稠密向量），借鉴 WeKnora 插件式 RAG 管线设计，支持多路召回与 rerank。

#### Scenario: 混合检索召回
- **WHEN** 应用层发起检索请求
- **THEN** 系统 SHALL 并行执行 BM25 与向量召回，融合后可选 rerank，返回 top_k 结果

#### Scenario: 多源召回
- **WHEN** 辅助回复或分析需要上下文
- **THEN** 系统 SHALL 支持从客户画像、历史聊天向量、产品知识向量多路召回并合并

### Requirement: RAG 管线可插拔
系统 SHALL 以可插拔插件形式实现 RAG 管线（查询理解/改写、多路召回、rerank、上下文压缩/去重、父子块展开），MVP 实现核心子集，其余作为扩展点。

#### Scenario: 核心管线可用
- **WHEN** 触发 RAG 生成
- **THEN** 系统 SHALL 至少经过多路召回 + rerank + 生成 三个核心环节

#### Scenario: 插件可扩展
- **WHEN** 需要新增查询改写或上下文压缩能力
- **THEN** 系统 SHALL 允许以插件形式接入而不改动管线主干

### Requirement: 双索引策略（RAG + Wiki）
系统 SHALL 在知识库层支持可叠加的两种索引策略：RAG 索引（向量 + BM25 + rerank，面向问-答检索）与 Wiki 索引（Agent 从文档抽取实体/概念生成互联 Markdown 页面，面向体系化浏览）。两者同源、可独立开关。知识库层抽象为可挂多种索引策略的接口。

#### Scenario: RAG 索引可用
- **WHEN** 文档导入并开启 RAG 索引
- **THEN** 系统 SHALL 切分向量化并支持问-答检索召回

#### Scenario: Wiki 索引可用
- **WHEN** 文档导入并开启 Wiki 索引
- **THEN** 系统 SHALL 异步由 Agent 抽取实体/概念生成互联 Markdown Wiki 页面，供体系化浏览

#### Scenario: 双索引叠加
- **WHEN** 同一知识库同时开启 RAG 与 Wiki 索引
- **THEN** 系统 SHALL 对同源文档同时产出 RAG chunk 与 Wiki 页面，两者数据互通、互不阻塞

#### Scenario: 索引策略可扩展
- **WHEN** 未来需要新增索引策略
- **THEN** 系统 SHALL 允许以索引策略插件形式接入而不重构知识库主干

### Requirement: Wiki Obsidian vault 导出
系统 SHALL 将 Wiki 页面导出为 Obsidian vault 格式（每个页面一个 Markdown 文件，含 YAML frontmatter 与 `[[wikilinks]]` 页面间链接），用户用本地 Obsidian 打开即可查看知识图谱（graph view 基于 wikilinks 自动构建）。项目自身不实现图谱可视化前端。

#### Scenario: 导出 Obsidian vault
- **WHEN** 用户请求导出 Wiki
- **THEN** 系统 SHALL 将所有 Wiki 页面导出为 Markdown 文件（含 frontmatter 与 wikilinks）到一个文件夹，构成可被 Obsidian 打开的 vault

#### Scenario: 图谱自动构建
- **WHEN** 用户用 Obsidian 打开导出的 vault
- **THEN** Obsidian graph view SHALL 基于 `[[wikilinks]]` 自动构建知识图谱节点与边

```

Full source: openspec/changes/whatsapp-customer-kb/specs/knowledge-base/spec.md

## openspec/changes/whatsapp-customer-kb/specs/reply-assist/spec.md

- Source: openspec/changes/whatsapp-customer-kb/specs/reply-assist/spec.md
- Lines: 1-26
- SHA256: 29981f7f86e3588338458d9e605f56470cb3e73c0007293a511f63a17e4db2a8

```md
## ADDED Requirements

### Requirement: RAG 辅助回复生成
系统 SHALL 基于客户画像 + 相关历史聊天 + 本地产品知识，结合当前收到的消息，通过 RAG 生成建议回复。

#### Scenario: 生成建议回复
- **WHEN** 用户在聊天浏览页对某条消息请求"生成回复"
- **THEN** 系统 SHALL 检索该客户画像、相关历史聊天与产品知识，生成一条建议回复并展示

#### Scenario: 仅生成不自动发送
- **WHEN** 系统生成建议回复
- **THEN** 系统 SHALL 不自动发送该回复到 WhatsApp，仅展示供用户复制/编辑

### Requirement: 回复上下文可追溯
系统 SHALL 展示建议回复所依据的检索来源（画像字段/历史消息片段/产品知识片段）。

#### Scenario: 展示来源
- **WHEN** 系统返回建议回复
- **THEN** 系统 SHALL 同时展示支撑该回复的检索来源片段，供用户判断依据

### Requirement: 多回复候选
系统 SHALL 支持为同一条消息生成多个候选回复供用户选择。

#### Scenario: 生成多候选
- **WHEN** 用户请求生成回复
- **THEN** 系统 SHALL 提供至少一个候选回复，并支持用户请求重新生成获得不同候选
```

## openspec/changes/whatsapp-customer-kb/specs/web-app/spec.md

- Source: openspec/changes/whatsapp-customer-kb/specs/web-app/spec.md
- Lines: 1-44
- SHA256: 7b7e0c6f5c20d9fb38e7eecc285015743150d63e9dd061b440cc0dec40b05ba9

```md
## ADDED Requirements

### Requirement: 本地 Web 应用访问
系统 SHALL 提供 FastAPI 后端 + 前端页面的本地 Web 应用，通过本地浏览器访问（127.0.0.1）。

#### Scenario: 本地访问
- **WHEN** 用户启动应用并在浏览器打开本地地址
- **THEN** 系统 SHALL 展示 Web UI 主界面

### Requirement: 客户列表与画像页
系统 SHALL 提供客户列表页与客户画像页，画像页支持编辑。

#### Scenario: 浏览客户列表
- **WHEN** 用户打开客户列表
- **THEN** 系统 SHALL 展示所有客户及其关键画像摘要

#### Scenario: 编辑画像
- **WHEN** 用户在画像页编辑某字段并保存
- **THEN** 系统 SHALL 持久化该编辑值并标记为人工来源

### Requirement: 聊天浏览
系统 SHALL 提供聊天浏览页，按客户/聊天展示历史消息，并支持在消息上触发回复生成。

#### Scenario: 浏览聊天
- **WHEN** 用户打开某客户聊天
- **THEN** 系统 SHALL 分页展示该聊天的历史消息（含元数据与正文）

#### Scenario: 触发回复
- **WHEN** 用户在某条消息上请求生成回复
- **THEN** 系统 SHALL 在该消息上下文触发辅助回复生成并展示结果

### Requirement: 本地知识管理页
系统 SHALL 提供本地知识管理页，支持上传/列表/删除文档与检索测试。

#### Scenario: 管理知识
- **WHEN** 用户进入知识管理页
- **THEN** 系统 SHALL 展示已导入文档列表，支持上传新文档、删除文档、检索测试

### Requirement: 采集器状态可见
系统 SHALL 在 Web UI 暴露 WhatsApp 采集器的运行状态（连接/登录/最近同步时间）。

#### Scenario: 查看采集状态
- **WHEN** 用户查看应用状态
- **THEN** 系统 SHALL 展示采集器连接状态、登录状态与最近同步时间
```

## openspec/changes/whatsapp-customer-kb/specs/whatsapp-sync/spec.md

- Source: openspec/changes/whatsapp-customer-kb/specs/whatsapp-sync/spec.md
- Lines: 1-70
- SHA256: 02c82e059e6759fa3bf14efc2558c170a7040f384b7e86467d215779cce345e8

```md
## ADDED Requirements

### Requirement: WhatsApp Web 连接与登录态持久化
系统 SHALL 通过 CDP 驱动独立 Chrome 打开 WhatsApp Web，并持久化登录态（user-data-dir），使重启后无需重新扫码。

#### Scenario: 首次扫码登录
- **WHEN** 首次启动采集器且 WhatsApp Web 未登录
- **THEN** 系统 SHALL 在 Web UI 展示登录二维码/状态，登录成功后开始采集

#### Scenario: 重启复用登录态
- **WHEN** 采集器重启且 user-data-dir 中存在有效登录态
- **THEN** 系统 SHALL 跳过扫码直接进入采集，无需人工干预

### Requirement: 消息元数据与明文正文采集
系统 SHALL 通过 CDP 读取 WhatsApp Web IndexedDB `model-storage` 库的 message/chat/contact/group-metadata stores 获取消息元数据，并通过 DOM 快照获取明文正文，按消息 id 合并两者。

#### Scenario: 合并元数据与正文
- **WHEN** 一次采集 tick 完成
- **THEN** 每条消息 SHALL 同时具备 IDB 来源的元数据（id/chatId/fromMe/from/timestamp/type）与 DOM 来源的明文正文（若该消息已渲染）

#### Scenario: 正文缺失容忍
- **WHEN** 某历史消息未在当前 DOM 渲染
- **THEN** 系统 SHALL 保存其元数据，正文标记为缺失，不阻塞该批采集

### Requirement: 实时同步新消息
系统 SHALL 以快 tick（约 2s）DOM 增量抓取新消息，仅当可见行 hash 变化时才产出增量，避免空闲刷屏。

#### Scenario: 新消息增量同步
- **WHEN** 聊天窗口出现新消息且可见行 hash 变化
- **THEN** 系统 SHALL 在下一个快 tick 内采集并入库该新消息

#### Scenario: 空闲不刷屏
- **WHEN** 聊天窗口无新消息、可见行 hash 未变
- **THEN** 系统 SHALL 不产出增量事件

### Requirement: 全量校准
系统 SHALL 以慢 tick（约 30s）走 IDB 全量校准，补齐快 tick 遗漏的元数据。

#### Scenario: 慢 tick 校准
- **WHEN** 慢 tick 触发
- **THEN** 系统 SHALL 走 IDB walk 并与已入库消息 upsert 合并，补齐缺失元数据

### Requirement: 按需历史回溯
系统 SHALL 支持对指定聊天手动触发滚动加载历史 DOM 行并采集，不自动执行首次全量回溯。

#### Scenario: 手动触发回溯
- **WHEN** 用户在 Web UI 对某聊天点击"回溯历史"
- **THEN** 系统 SHALL 程序化滚动加载该聊天历史 DOM 行并采集入库，直到达到上限或无更多历史

#### Scenario: 不自动全量回溯
- **WHEN** 采集器首次启动
- **THEN** 系统 SHALL 仅同步当前可见与新消息，不自动回溯全部历史

### Requirement: 幂等 upsert
系统 SHALL 按 (account_id, chat_id, message_id) 幂等 upsert 消息到结构化存储，按 (chatId, day) 分组幂等 upsert 到向量库，保证可重试且不重复。

#### Scenario: 重复采集去重
- **WHEN** 同一消息被多次采集
- **THEN** 系统 SHALL 仅保留一条记录，不产生重复

### Requirement: 降低封号风险的采集行为
系统 SHALL 采取单设备、拟人化轮询间隔加随机抖动、不自动发送消息、不高频全量扫描的策略降低封号风险。

#### Scenario: 拟人化轮询
- **WHEN** 采集器运行中
- **THEN** 轮询间隔 SHALL 带随机抖动，不呈现机械固定频率

#### Scenario: 不自动发送
- **WHEN** 系统运行任意功能
- **THEN** 系统 SHALL 不向 WhatsApp 发送任何消息，仅采集与只读操作
```

