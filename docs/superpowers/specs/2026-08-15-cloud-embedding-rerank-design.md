---
comet_change: cloud-embedding-rerank
role: technical-design
canonical_spec: openspec
---

# 嵌入/重排全切线上 — 技术设计

## 背景

当前嵌入/重排默认走本地模型（bge-m3 / bge-reranker-v2-m3），依赖 torch + FlagEmbedding（~2GB）。阿里云提供 OpenAI 兼容的嵌入 API（`qwen3.7-text-embedding`）和重排 API（`qwen3-rerank`），成本极低（~5 元/月）。目标是把两者全部切到在线，彻底移除本地模型依赖，使打包体积从 ~3GB 降到 ~500MB、首次启动零模型下载。

## 目标 / 非目标

**目标：**
- 嵌入、重排全部走阿里云在线 API
- 移除 torch + FlagEmbedding 依赖
- 首次启动零模型下载
- 架构上支持切换其他云厂商（百度/字节）

**非目标：**
- 不做一键启动 exe 打包（后续独立 change）
- 不实现百度/字节适配器（仅预留 provider 分支）
- 不改变检索/重排/降级的行为语义

## 技术方案

### 1. 嵌入：复用 `OpenAIEmbedding`，支持 `dimensions` 参数

阿里 `qwen3.7-text-embedding` 兼容 OpenAI 接口，复用现有 `OpenAIEmbedding`。但需修复维度隐患：`embed()` 增加 `dimensions` 参数（从 `settings.embedding_dim` 读取），与 `dim()` 保持一致，避免 ChromaDB 维度不匹配。

### 2. 重排：新增 `CloudReranker` 适配器

新增 `app/rag/rerank_cloud.py`，实现 `Reranker` 接口，按 `provider` 分发。当前实现阿里云：
- 端点：`POST /compatible-api/v1/reranks`
- 请求体：`{model, documents, query, top_n}`（`instruct` 可选，不传默认问答检索）
- 响应：`{results: [{index, relevance_score}]}`，已按分数降序
- 失败回退：`candidates[:top_k]` 原序

### 3. 移除本地模型

删除 `BgeEmbedding`、`BgeReranker`、`device_utils.py`，从 `pyproject.toml` 移除 `FlagEmbedding`。`get_reranker()` 的 `local` 分支回退到 `CloudReranker`（兼容旧配置）。

### 4. 依赖修正

`httpx` 从 dev 依赖提升为主依赖（运行时实际依赖）。

### 5. 预热改在线

`_warmup_models` 改为在线预热（发最小请求验证 API 可用），不再加载本地模型。测试环境跳过。

## 关键取舍与风险

- [阿里 API 限流/超时] → 失败回退原序 + 超时 60s，不阻塞回复生成
- [网络不可用] → 回退原序，回复仍可生成（仅排序不优化）
- [维度不匹配] → `embed()` 传 `dimensions` 与 `dim()` 一致
- [切换厂商] → provider 可配置，新增适配器即可
- [移除本地模型后离线不可用] → 全切在线，接受此 trade-off（成本 ~5 元/月）

## 测试策略

- 新增 `CloudReranker` 适配器测试（mock httpx，验证请求体构造和响应解析）
- 移除本地模型测试（`BgeReranker`、`BgeEmbedding` 相关）
- 验证 `OpenAIEmbedding.embed()` 传 `dimensions` 参数
- 验证 `get_reranker()` 的 `aliyun` 分支

## Spec Patch

无（纯实现层改动，行为不变，`skip_specs: true`）。