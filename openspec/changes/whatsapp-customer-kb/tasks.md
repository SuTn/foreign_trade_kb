## 1. 项目骨架与基础设施

- [x] 1.1 初始化 Python 项目结构（src/ 包结构、pyproject.toml、依赖锁定）
- [x] 1.2 添加核心依赖（fastapi/uvicorn/playwright/langchain/sqlite/chroma/embedding/llm sdk/文档解析库）
- [ ] 1.3 配置 Playwright 与独立 Chrome user-data-dir，验证可打开 WhatsApp Web
- [x] 1.4 建立配置层（DOM 选择器、IDB store 名、轮询间隔、LLM/embedding 配置集中管理）

## 2. 存储层

- [x] 2.1 定义存储层抽象接口（结构化存储 + 向量存储 + LLM 接口）
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
