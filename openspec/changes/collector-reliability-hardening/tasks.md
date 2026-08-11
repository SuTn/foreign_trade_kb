# collector-reliability-hardening 任务清单

## 1. 采集器自愈 (D1/D2)

- [ ] 1.1 `Scanner.run()` 主循环整体 try/except + 指数退避（1s→30s 上限），异常记录到 status/日志不退出
- [ ] 1.2 CDP 失效检测：区分可重试/致命异常，连续 3 次致命失败才重建浏览器（launch_browser + 重置会话状态）
- [ ] 1.3 `app/__main__.py` supervisor 循环：采集器进程异常退出自动拉起（正常退出/用户中断不重启）
- [ ] 1.4 采集器 `__main__.py` 异常时以非 0 exit code 退出，供 supervisor 判定

## 2. 消息向量语义修正 (D3)

- [ ] 2.1 scanner 向量键从 `f"{chat_id}:{day}"` 改为 `f"{chat_id}:{msg_id}"`（无 id 回退 day 键）
- [ ] 2.2 旧向量清理：一次性清空 `message_vectors` 集合（delete where={}），随扫描重建 per-message 向量
- [ ] 2.3 单测：同会话同日多条消息各自独立向量键，互不覆盖

## 3. Web 存储单例 (D4/D8)

- [ ] 3.1 `create_app()` 加 lifespan 持有 `app.state.sqlite_store`/`app.state.chroma_store`，退出时关闭
- [ ] 3.2 路由全部改为读 `request.app.state.*` 单例，删除每请求 `_store()`/`ChromaStore(...)` 新建
- [ ] 3.3 embedding/reranker 在 lifespan 后台线程预热；首次接口调用未就绪时有超时降级

## 4. 接口错误降级 (D5)

- [ ] 4.1 `/api/reply` 与 `/api/reply/regenerate` try/except，失败渲染 `reply_result.html` 带 error 字段
- [ ] 4.2 `/api/knowledge/search` 嵌入失败降级为 BM25-only + degraded 提示
- [ ] 4.3 `OllamaReranker.rerank` 网络/HTTP 失败回退原序候选并打日志
- [ ] 4.4 单测：reply 失败路径返回降级不抛 500；OllamaReranker 网络失败回退原序

## 5. 上传状态机与坏文件处理 (D6)

- [ ] 5.1 upload 包 try/except：parse 失败置 `status='failed'` 返回可读错误，不 500
- [ ] 5.2 成功路径 parse→index 后置 `status='done'`；空文本（0 chunk）跳过向量化直接 done
- [ ] 5.3 单测：坏文件/空文件/未知格式上传返回错误且 status 置 failed；正常上传置 done

## 6. 模型加载健壮性 (D7)

- [ ] 6.1 新增 `_use_fp16()` helper（按 `torch.cuda.is_available()`），BgeEmbedding/BgeReranker 改用
- [ ] 6.2 单测：CPU-only（mock cuda 不可用）时构造参数 use_fp16=False

## 7. backfill 清理 (D9)

- [ ] 7.1 删除 `_drain_backfill_requests` 中 `data` 死代码块
- [ ] 7.2 `backfill_requests` 表定义移入 schema.sql（含 attempts 列），路由 CREATE 保留容错
- [ ] 7.3 表存在性探测只做一次（__init__ 缓存）；失败任务 attempts+1 不标 done，成功后标 done
- [ ] 7.4 单测：表缺失轮询不抛错；失败任务不标 done 可重试

## 8. IDB 分页读取 (D10)

- [ ] 8.1 idb_walk 页面 JS 改游标分页，应用 `max_records_per_store` 上限
- [ ] 8.2 单测：超过上限的 store 只返回前 N 条

## 9. 回归验证

- [ ] 9.1 全量 `pytest -q` 通过（新增 + 既有）
- [ ] 9.2 `compileall -q app` 通过
- [ ] 9.3 代码走读确认无遗留每请求 store 新建与死代码
