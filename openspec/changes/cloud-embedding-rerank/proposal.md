## Why

当前系统的嵌入（Embedding）和重排（Reranker）默认使用本地模型（bge-m3 / bge-reranker-v2-m3），依赖 torch + FlagEmbedding（约 2GB）。这导致：打包体积巨大（~3GB）、首次启动需下载模型、对个人外贸业务员不友好。阿里云提供 OpenAI 兼容的嵌入 API 和 qwen3-rerank 重排 API，成本极低（约 5 元/月），可彻底移除本地模型依赖。

## What Changes

- **嵌入改在线**：默认 `KB_EMBEDDING_PROVIDER` 从 `local` 改为 `openai`，使用阿里云 `qwen3.7-text-embedding`（兼容 OpenAI 接口）
- **重排改在线**：新增 `CloudReranker` 适配器，默认 `KB_RERANKER_PROVIDER` 从 `local` 改为 `aliyun`，使用阿里云 `qwen3-rerank`
- **移除本地模型**：删除 `BgeEmbedding`、`BgeReranker`、`device_utils.py`，从 `pyproject.toml` 移除 `FlagEmbedding` 依赖
- **修复维度隐患**：`OpenAIEmbedding.embed()` 支持传 `dimensions` 参数，与 `dim()` 保持一致
- **修复依赖声明**：`httpx` 从 dev 依赖提升为主依赖（运行时实际依赖）
- **预热改在线**：`_warmup_models` 改为在线预热（发最小请求验证），不再加载本地模型

## Capabilities

### New Capabilities
- 无（纯实现层改动，行为不变）

### Modified Capabilities
- 无（检索、重排、降级行为均不变，仅底层实现从本地切到在线）

> 此 change 为纯实现层重构，行为层面无变化，故设 `skip_specs: true`。

## Impact

- **代码**：`app/rag/rerank_cloud.py`（新增）、`app/rag/reranker.py`、`app/llm/bge_embedding.py`、`app/web/app.py`、`app/config.py`
- **依赖**：移除 `FlagEmbedding`（含 torch 间接依赖），`httpx` 提升为主依赖
- **配置**：`.env` 需配置阿里云 `KB_EMBEDDING_API_KEY` / `KB_RERANKER_API_KEY` 等
- **测试**：移除本地模型测试，新增云适配器测试
- **打包**：包体积从 ~3GB 降到 ~500MB，首次启动零模型下载