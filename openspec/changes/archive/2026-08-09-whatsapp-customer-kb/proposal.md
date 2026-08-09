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
