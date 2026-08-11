# Brainstorm Summary

- Change: collector-reliability-hardening
- Date: 2026-08-11
- Status: 已确认 (2026-08-11)

## 确认的技术方案

修复 9 项 Blocker/High bug（范围已在 open 阶段确认）。OpenSpec design.md 定义 D1-D10 技术决策：

- D1/D2: Scanner.run() 主循环 try/except + 指数退避(1s→30s)；CDP 失效时重建浏览器；app/__main__.py supervisor 守护子进程
- D3: 消息向量键 (chatId, day) → (chatId, msgId)，per-message 独立入库
- D4/D8: FastAPI lifespan 持有 SqliteStore/ChromaStore 单例；embedding/reranker 后台线程预热
- D5: /api/reply 与 regenerate try/except 渲染错误；search 嵌入失败降级 BM25-only；OllamaReranker 网络失败回退原序
- D6: 上传状态机 processing→done/failed；坏文件友好失败不 500
- D7: use_fp16 按 torch.cuda.is_available() 决定
- D9: backfill 死代码删除；backfill_requests 表入 schema.sql；失败 attempts+1 不标 done
- D10: idb_walk 游标分页，max_records_per_store 生效

## 关键取舍与风险

- 双进程 Chroma 锁未彻底解决（超出范围，降频创建）
- 向量键变更后旧 day 键向量不可达，RAG 仅对新消息生效
- 模型预热增加首次启动时间（后台线程，不阻塞 uvicorn）
- CDP 异常类型宽匹配（build 阶段实测验证）

## 用户确认的决策

1. **CDP 重连**：失败计数阈值触发 —— fast_tick 异常先区分可重试/致命，连续 3 次失败才重建浏览器；瞬时抖动退避重试
2. **旧向量**：主动清理重建 —— build 任务加入一次性清空 message_vectors 集合
3. **模型预热**：后台预热 + 超时降级 —— lifespan 后台线程加载，未就绪时接口降级提示

## 测试策略

每修复组配单测：自愈(1)、向量键(2)、降级(4)、上传状态机(5)、use_fp16(6)、backfill(7)、IDB 分页(8)。回归 pytest -q + compileall。

## Spec Patch

无（open 阶段已生成 4 个 delta spec，覆盖所需行为）
