## 1. 云重排适配器

- [x] 1.1 新增 `app/rag/rerank_cloud.py`，实现 `CloudReranker`（阿里云 qwen3-rerank，失败回退原序）
- [x] 1.2 修改 `app/rag/reranker.py` 的 `get_reranker()`，增加 `aliyun` 分支，`local` 回退到 `CloudReranker`

## 2. 嵌入在线化

- [x] 2.1 修改 `app/llm/bge_embedding.py` 的 `OpenAIEmbedding.embed()`，支持 `dimensions` 参数（与 `dim()` 一致）

## 3. 配置与预热

- [x] 3.1 修改 `app/config.py`：默认 `embedding_provider="openai"`、`reranker_provider="aliyun"`，新增 `reranker_api_key`
- [x] 3.2 修改 `app/web/app.py` 的 `_warmup_models`，改在线预热（发最小请求验证），移除本地模型加载

## 4. 移除本地模型

- [x] 4.1 删除 `BgeEmbedding`、`BgeReranker`、`device_utils.py` 本地模型实现
- [x] 4.2 修改 `pyproject.toml`：移除 `FlagEmbedding`，`httpx` 提升为主依赖

## 5. 配置示例与测试

- [x] 5.1 更新 `.env.example` 为阿里云在线配置示例
- [x] 5.2 调整测试：移除本地模型测试，新增 `CloudReranker` 适配器测试（mock httpx）
- [x] 5.3 验证：通过代理实测阿里云 embedding/rerank API 均返回 200，参数与解析逻辑一致