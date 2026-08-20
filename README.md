# 外贸客户知识库

本地外贸客户知识库 —— 通过 CDP 自动化同步 WhatsApp Web 聊天记录, 自动抽取/匹配客户画像, 结合本地知识库 (RAG + Wiki 双索引) 为业务员提供客户分析与辅助回复建议。所有数据本地存储, 不上云。

## 功能特性

- **WhatsApp 聊天同步**: 通过 CDP 只读访问 WhatsApp Web, 抓取 DOM 快照 + IndexedDB, 增量同步聊天记录 (全程只读, 不发送任何消息)
- **客户画像**: 自动抽取/匹配客户身份字段, 支持人工编辑 (manual 不被 auto 覆盖)
- **客户分析**: 基于画像 + 聊天摘要, LLM 生成兴趣点/活跃度/跟进建议
- **对话摘要**: 按客户聚合聊天, LLM 结构化输出意向车型/预算/目标国家/核心顾虑/待跟进事项, 客户详情页展示; 增量更新 (只处理新消息 + 旧摘要合并), 异步 worker 生成不阻塞页面
- **辅助回复**: RAG 召回 (画像 + 历史聊天 + 产品知识) + LLM 生成建议回复, 仅生成不自动发送
- **知识库导入**: 上传 PDF / Word / Excel / CSV / HTML / Markdown / 纯文本, 自动解析切分
- **RAG 索引**: 多路召回 (FTS5 + 向量) + 云重排 (阿里云 qwen3-rerank) + 父子块展开
- **Wiki 索引**: 两阶段实体抽取与全局去重 (嵌入聚类初筛 + LLM 精判), 生成 Markdown 页面
- **Obsidian Vault 导出**: 将 Wiki 页面导出为 Obsidian vault (frontmatter + wikilinks)
- **本地优先**: SQLite (WAL + FTS5) + ChromaDB, 全部数据落在 `data/` 目录
- **一键启动包**: `build.py` 产出可分发的 zip, 业务员解压 → 双击 exe → 页面填 Key → 扫码登录 WhatsApp → 用

## 环境要求

- Python ≥ 3.11,<3.13 (chromadb 0.4.x 不兼容 3.13)
- 无需 Docker, 纯本地运行
- 首次运行需可见的 Chrome (Playwright 拉起, 用于扫码登录 WhatsApp Web)
- **AI 全在线**: LLM / Embedding / Reranker 均走云端 API (阿里云 DashScope 等), 无需本地模型
  - LLM: Anthropic 或任意 OpenAI 兼容接口 (可配 `KB_LLM_API_BASE` 指向第三方/自建网关; DeepSeek 用 `KB_LLM_MODEL=deepseek-chat` + `KB_LLM_API_BASE=https://api.deepseek.com/v1`)
  - Embedding: OpenAI 兼容接口 (阿里云 `qwen3.7-text-embedding`)
  - Reranker: 阿里云 `qwen3-rerank`

## 安装

```bash
# 1. 创建虚拟环境 (推荐 uv)
uv venv .venv
source .venv/bin/activate

# 2. 安装依赖 (含 dev 可选组)
uv pip install -e ".[dev]"

# 3. 安装 Playwright 浏览器
playwright install chromium

# 4. 复制配置示例并按需修改
cp .env.example .env
#   编辑 .env: 至少配置 KB_LLM_API_KEY (或 OPENAI_API_KEY/ANTHROPIC_API_KEY)
```

## 配置

配置项使用 `KB_` 前缀 (见 `app/config.py`), 从项目根目录的 `.env` 读取。LLM 与嵌入**可分别配置 provider / api_base / api_key** —— 例如 LLM 走某网关、嵌入走另一网关或本地。

### LLM (生成)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `KB_LLM_PROVIDER` | `anthropic` | `anthropic` 或 `openai` (走 OpenAI 兼容接口) |
| `KB_LLM_MODEL` | `claude-sonnet-4-6` | 模型名 |
| `KB_LLM_API_BASE` | *(空=官方端点)* | OpenAI 兼容接口 base URL, 可指向第三方/自建网关 |
| `KB_LLM_API_KEY` | *(空=回退环境变量)* | 留空时: openai 回退 `OPENAI_API_KEY`, anthropic 回退 `ANTHROPIC_API_KEY` |

### Embedding (向量化, 在线, 阿里云)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `KB_EMBEDDING_PROVIDER` | `openai` | `openai` (OpenAI 兼容接口, 阿里云等) |
| `KB_EMBEDDING_MODEL` | `qwen3.7-text-embedding` | 嵌入模型名 |
| `KB_EMBEDDING_API_BASE` | *(空=官方端点)* | 嵌入接口 base URL, **可与 `KB_LLM_API_BASE` 不同** |
| `KB_EMBEDDING_API_KEY` | *(空=回退环境变量)* | 留空回退 `OPENAI_API_KEY` |
| `KB_EMBEDDING_DIM` | `1024` | 嵌入维度 (qwen3.7-text-embedding 支持 2560/2048/1536/1024/768/512/256) |

> ⚠️ 切换嵌入 provider/模型后, 维度可能变化, 已入库的旧向量会失效 —— 建议清空 `data/chroma/` 重建索引。
> 💡 阿里云 OpenAI 兼容地址: `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`

### Reranker (重排, 在线, 阿里云)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `KB_RERANKER_PROVIDER` | `aliyun` | `aliyun` (阿里云 qwen3-rerank) |
| `KB_RERANKER_MODEL` | `qwen3-rerank` | 重排模型名 |
| `KB_RERANKER_API_BASE` | *(空=官方端点)* | 阿里云 base URL (不含 `/compatible-api`, 适配器自动拼接) |
| `KB_RERANKER_API_KEY` | *(空=回退环境变量)* | 重排 API Key (阿里云 DashScope Key) |

### 最小配置示例

```dotenv
# LLM 走 OpenAI 兼容接口 (DeepSeek / 阿里云 / 自建网关)
KB_LLM_PROVIDER=openai
KB_LLM_MODEL=deepseek-chat
KB_LLM_API_BASE=https://api.deepseek.com/v1
KB_LLM_API_KEY=sk-...

# Embedding: 阿里云 qwen3.7-text-embedding
KB_EMBEDDING_PROVIDER=openai
KB_EMBEDDING_MODEL=qwen3.7-text-embedding
KB_EMBEDDING_DIM=1024
KB_EMBEDDING_API_BASE=https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
KB_EMBEDDING_API_KEY=sk-...

# Reranker: 阿里云 qwen3-rerank
KB_RERANKER_PROVIDER=aliyun
KB_RERANKER_MODEL=qwen3-rerank
KB_RERANKER_API_BASE=https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com
KB_RERANKER_API_KEY=sk-...
```

> 💡 也可在 Web 设置页「模型配置」区块直接配置 (无需编辑 .env), 保存后立即生效。

### 其他可调项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `KB_DATA_DIR` | `data` | 数据目录 |
| `KB_SQLITE_PATH` | `data/kb.db` | SQLite 路径 |
| `KB_CHROMA_DIR` | `data/chroma` | ChromaDB 目录 |
| `KB_USER_DATA_DIR` | `data/user-data-dir` | Chrome 持久化登录目录 |
| `KB_VAULT_EXPORT_DIR` | `data/vault` | Obsidian vault 导出目录 |
| `KB_FAST_TICK_SEC` | `2.0` | DOM 增量轮询间隔 |
| `KB_SLOW_TICK_SEC` | `30.0` | IDB 全量校准间隔 |
| `KB_AUTO_SCAN_CHATS` | `true` | 自动逐会话打开扫描全部聊天正文（会把未读消息标记为已读） |
| `KB_AUTO_SCAN_INTERVAL_SEC` | `600.0` | 全量扫描周期 |
| `KB_AUTO_SCAN_MAX_CHATS` | `100` | 单次最多扫描的会话数 |
| `KB_AUTO_SCAN_SETTLE_SEC` | `1.5` | 打开每个会话后的等待秒数 |
| `KB_LLM_MAX_TOKENS` | `2048` | 回复生成最大 token 数（长回复不被截断；分层/同义判断等短任务自动用更小值） |

## 启动

### 开发环境

```bash
python -m app
```

启动后:

1. 采集器在同进程线程内运行, 拉起一个可见的 Chrome 窗口并打开 WhatsApp Web。首次需用手机扫码登录; 登录态通过 `user-data-dir` 持久化, 之后无需重复扫码。
2. Web 服务监听 `http://127.0.0.1:8000`。
3. 采集器开始低频轮询同步聊天记录 (DOM 增量 + IDB 全量校准)。

> 未配置模型 Key 时采集器不启动 (避免打开 WhatsApp); 在 Web 设置页「模型配置」填好 Key 后自动启动。

### 一键启动包 (业务员)

解压 → 双击 `外贸客户知识库.exe` → 浏览器自动打开 Web → 设置页填模型 Key → 采集器自动启动打开 WhatsApp → 扫码登录 → 用。

> ⚠️ 自动化访问 WhatsApp Web 有封号风险, 启动前请务必阅读 [docs/RISK.md](docs/RISK.md)。

## 使用流程

### 客户与回复

1. 浏览器访问 `http://127.0.0.1:8000`。
2. 首页为仪表盘, 概览采集器状态、客户与知识库统计、近期活跃会话, 并提供「快速开始」引导; `/api/collector/status` 返回 JSON 状态。
3. `/workspace` 为三栏工作台: 左栏客户列表 (搜索 + 意向等级筛选 + 批量分层), 中栏聊天窗口 (实时刷新 + 加载更早消息 + 生成回复), 右栏画像/摘要/AI 建议。客户列表含 WhatsApp 自动抓取的头像 (无头像时为首字母占位)。
4. 在聊天窗口对某条消息点「回复」, 系统通过 RAG 召回画像/历史/产品知识后生成建议回复 (仅生成, 不自动发送, 业务员复制后手动发送)。
5. 右栏可一键生成「对话摘要」「跟进建议」「客户分析」, 结构化展示意向车型/预算/目标国家/核心顾虑/待跟进事项, 便于快速复盘。

### 知识库

1. 访问 `/knowledge`。
2. 上传文档 (支持 pdf / docx / doc / xlsx / xls / csv / html / md / txt), 系统自动解析、切分, 同时建立 RAG 向量索引与 Wiki 实体索引。
3. 点击「导出 Vault」将 Wiki 页面导出为 Obsidian vault (位于 `data/vault/`), 可用 Obsidian 打开浏览。

## 项目结构

```
app/
├── __main__.py          # 开发启动入口: 同进程线程启动采集器 + uvicorn Web
├── config.py            # Settings (KB_ 前缀, 读 .env)
├── collector/           # WhatsApp 采集器 (CDP 只读, DOM+IDB 同步)
│   ├── readonly_cdp.py  # ReadOnlyCDP 门面 (架构级只读保证)
│   ├── browser.py       # Playwright 启动持久化 Chrome
│   ├── scanner.py       # fast/slow tick 轮询
│   ├── dom_snapshot.py  # DOM 快照解析
│   ├── idb_walk.py      # IndexedDB 遍历
│   └── merger.py        # DOM/IDB 消息合并
├── storage/             # SQLite (WAL+FTS5) + ChromaDB
├── llm/                 # CloudLLM (anthropic/openai 兼容接口) + OpenAI 兼容嵌入
├── rag/                 # RAG 管线: 多路召回 + 云重排 + 父子块展开
├── knowledge/           # 文档解析 / RAG 索引 / Wiki 索引 / Vault 导出
├── profile/             # 客户匹配 / 字段抽取 / 分析
├── reply/               # 辅助回复生成 (仅生成不发送)
└── web/                 # FastAPI 应用 + 模板 + 路由
launcher/                # 一键启动包: 入口 / 路径 / 采集器同进程启动 / 托盘
vendor/docreader/        # WeKnora docreader (MIT, 功能性回退实现)
data/                    # 本地数据 (gitignored): kb.db, chroma/, user-data-dir/, vault/, status.json
```

## 架构

单进程架构: 采集器与 Web 在同一进程内运行 (采集器在独立线程 + 事件循环, 崩溃自动重启)。两者共享同一份 SQLite (`data/kb.db`) 与 ChromaDB (`data/chroma/`)。

- **采集器**: 通过 Playwright 启动可见 Chrome 访问 WhatsApp Web, 经 `ReadOnlyCDP` 门面只读调用 CDP (DOMSnapshot / IndexedDB / Runtime.evaluate), 抓取消息后写入 SQLite + 向量库。架构上禁止采集器发送任何 WhatsApp 消息 (发送功能默认关闭, 需手动开启)。
- **Web**: FastAPI + Jinja2 + HTMX, 提供客户列表/画像/聊天/回复/知识管理界面, 监听 127.0.0.1:8000。
- **一键启动包**: `launcher/` 编排启动流程 (路径处理 / 环境自检 / 采集器同进程启动 / 系统托盘 / 延迟打开浏览器), PyInstaller 打包为 exe。

## 第三方

- `vendor/docreader/` —— 文档解析器, 源自 [Tencent WeKnora](https://github.com/Tencent/WeKnora) (MIT 许可, 归属保留)。本项目采用功能性回退实现, 详见 `vendor/docreader/NOTICE`。
- 嵌入/重排: 阿里云 qwen3.7-text-embedding / qwen3-rerank (在线)。
- LLM: Anthropic Claude / 任意 OpenAI 兼容接口 (GPT / DeepSeek / 阿里云及第三方/自建网关)。

## ⚠️ 风险提示

本项目通过 CDP 自动化访问 WhatsApp Web, 属非官方客户端, **有封号风险**。详见 [docs/RISK.md](docs/RISK.md)。
