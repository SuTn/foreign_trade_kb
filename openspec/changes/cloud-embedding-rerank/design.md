## Context

当前嵌入/重排默认走本地模型（bge-m3 / bge-reranker-v2-m3），依赖 torch + FlagEmbedding（~2GB）。阿里云提供 OpenAI 兼容的嵌入 API（`qwen3.7-text-embedding`）和重排 API（`qwen3-rerank`），成本极低。目标是把两者全部切到在线，彻底移除本地模型依赖。动机详见 proposal.md。

关键约束：
- 嵌入接口**兼容 OpenAI**（`client.embeddings.create`），可复用现有 `OpenAIEmbedding`
- 重排接口**非 OpenAI 兼容**（`POST /compatible-api/v1/reranks`），需新写适配器
- 重排失败需回退原序，不阻塞回复生成（现有 spec 已要求"重排失败降级"）

## Goals / Non-Goals

**Goals:**
- 嵌入、重排全部走阿里云在线 API
- 移除 torch + FlagEmbedding 依赖，包体积从 ~3GB 降到 ~500MB
- 首次启动零模型下载
- 架构上支持切换其他云厂商（百度/字节）

**Non-Goals:**
- 不做一键启动 exe 打包（后续独立 change）
- 不实现百度/字节适配器（仅预留 provider 分支）
- 不改变检索/重排/降级的行为语义

## Decisions

### 决策 1：嵌入复用 `OpenAIEmbedding`，但需支持 `dimensions` 参数

阿里 `qwen3.7-text-embedding` 支持维度参数（2560/2048/1536/1024/768/512/256），默认 1024。当前 `OpenAIEmbedding.embed()` 不传 `dimensions`，若配置 `KB_EMBEDDING_DIM` 非 1024，会与 `dim()` 不一致，导致 ChromaDB 维度不匹配。

**方案**：`embed()` 增加 `dimensions` 参数，从 `settings.embedding_dim` 读取，与 `dim()` 保持一致。

**备选**：固定 1024 维度，不传 `dimensions`。但限制了灵活性，且与 `dim()` 可能不一致。**不采用**。

### 决策 2：新增 `CloudReranker` 适配器，provider 可切换

新增 `app/rag/rerank_cloud.py`，实现 `Reranker` 接口，按 `provider` 分发到对应云厂商。当前实现阿里云，预留百度/字节分支。

**请求体**：`{model, documents, query, top_n}`（`instruct` 可选，不传默认问答检索）
**响应**：`{results: [{index, relevance_score}]}`，已按分数降序
**失败回退**：`candidates[:top_k]` 原序

**备选**：直接改 `OllamaReranker` 支持阿里。但阿里接口路径（`/compatible-api/v1/reranks`）与 Ollama（`/v1/rerank`）不同，且鉴权方式不同，混在一起会混乱。**独立适配器更清晰。

### 决策 3：移除本地模型类，彻底去 torch

删除 `BgeEmbedding`、`BgeReranker`、`device_utils.py`，从 `pyproject.toml` 移除 `FlagEmbedding`。`get_reranker()` 的 `local` 分支回退到 `CloudReranker`（兼容旧配置）。

**备选**：保留本地类作为离线回退。但全切线上后本地模型不再需要，保留增加维护负担。**彻底移除。

### 决策 4：`httpx` 提升为主依赖

当前 `httpx` 只在 dev 依赖，但 `OllamaReranker` 运行时 `import httpx`。全切线上后 `CloudReranker` 也用 httpx，必须提升为主依赖。

### 决策 5：预热改在线

`_warmup_models` 改为在线预热（发最小请求验证 API 可用），不再加载本地模型。测试环境（`_warmup_enabled()` 返回 False）跳过，不受影响。

## Risks / Trade-offs

- [阿里 API 限流/超时] → 失败回退原序 + 超时 60s，不阻塞回复生成
- [网络不可用] → 回退原序，回复仍可生成（仅排序不优化）
- [维度不匹配] → `embed()` 传 `dimensions` 与 `dim()` 一致
- [切换厂商] → provider 可配置，新增适配器即可
- [移除本地模型后离线不可用] → 全切在线，接受此 trade-off（成本 ~5 元/月）

## Migration Plan

1. 新增 `CloudReranker`，`get_reranker()` 加 `aliyun` 分支
2. 修改 `OpenAIEmbedding.embed()` 支持 `dimensions`
3. 修改 `config.py` 默认值改在线，新增 `reranker_api_key`
4. 修改 `_warmup_models` 改在线预热
5. 移除本地模型类 + 依赖
6. 更新 `.env.example`、测试
7. 验证：配置阿里云 key 后，嵌入/重排走在线 API，检索正常

**回滚**：保留 `local` 分支回退到 `CloudReranker`，若在线不可用可临时改回本地（需重新安装 FlagEmbedding）。

## Open Questions

无（接口细节已通过实际调用确认）。