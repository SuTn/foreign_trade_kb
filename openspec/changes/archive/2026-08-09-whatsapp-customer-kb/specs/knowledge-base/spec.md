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

#### Scenario: 无 Obsidian 仍可查看
- **WHEN** 用户无 Obsidian
- **THEN** 导出的 Markdown 文件 SHALL 仍可用任意 Markdown 编辑器查看（wikilinks 为标准文本）

### Requirement: Wiki 页面生成与管理
系统 SHALL 异步触发 LLM 从本地外贸资料抽取实体/核心概念，生成结构化、相互链接的 Markdown Wiki 页面，存于结构化存储，并支持人工编辑/管理与导出。

#### Scenario: 异步生成 Wiki
- **WHEN** 文档导入并开启 Wiki 索引
- **THEN** 系统 SHALL 异步抽取实体/概念、生成含 wikilinks 与 frontmatter 的 Markdown 页面，存入结构化存储

#### Scenario: Wiki 页面可编辑
- **WHEN** 用户编辑某 Wiki 页面
- **THEN** 系统 SHALL 持久化该编辑，并保留生成来源标记，编辑后可重新导出

#### Scenario: Wiki 生成失败不影响 RAG
- **WHEN** Wiki 生成失败
- **THEN** 系统 SHALL 不影响该文档的 RAG 索引与检索

#### Scenario: 文档更新增量重抽取不覆盖人工编辑
- **WHEN** 文档重新上传或编辑触发 Wiki 增量更新
- **THEN** 系统 SHALL 仅重抽该文档的实体候选并入全局去重，已有人工编辑的页面（source=manual 标记）不被自动重抽覆盖，仅追加新实体

### Requirement: 知识管理
系统 SHALL 在 Web UI 提供本地知识管理（上传/列表/删除/检索测试）。

#### Scenario: 上传与列表
- **WHEN** 用户上传文档
- **THEN** 系统 SHALL 解析、切分、向量化入库（RAG 索引）并异步生成 Wiki 页面（若开启），在知识列表展示该文档及其 chunk/Wiki 页面状态

#### Scenario: 检索测试
- **WHEN** 用户在知识管理页输入测试查询
- **THEN** 系统 SHALL 返回检索结果（含来源文档与片段）供验证
