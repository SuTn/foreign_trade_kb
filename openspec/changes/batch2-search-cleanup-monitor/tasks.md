# batch2-search-cleanup-monitor 任务清单

## 1. 全局搜索

- [x] 1.1 `SqliteStore` 增加客户检索（名称/电话/公司/国家 LIKE）与画像字段检索方法
- [x] 1.2 `SqliteStore.search_fts` 结果映射：消息 FTS 行 join 回 messages 取 chat_id/body/ts；知识库 FTS join 回 doc_chunks 取 doc_id
- [x] 1.3 新增 `GET /api/search?q=` 聚合四源返回分组结果
- [x] 1.4 新增 `/search` 页模板（分组展示，空查询友好提示）
- [x] 1.5 单测：四源各自命中与空查询行为

## 2. 手动数据清理

- [x] 2.1 `VectorStore.delete_message_vectors(chat_id)`（Chroma metadata 过滤）+ 接口声明
- [x] 2.2 `SqliteStore` 删除方法：按 chat_id 或按 ts 范围删 messages + FTS rebuild
- [ ] 2.3 新增 `POST /api/cleanup`（mode: chat|days，前端确认后调用）
- [ ] 2.4 管理入口：模板页/按钮触发清理
- [ ] 2.5 单测：按会话/按天数删除、画像与知识库保留断言

## 3. 采集器异常横幅

- [ ] 3.1 `base.html` 加全局横幅容器
- [ ] 3.2 `app.js` 定时轮询 `/api/collector/status`，alive=false 显示红色横幅
- [ ] 3.3 前端测试/走读确认横幅逻辑

## 4. 回归验证

- [ ] 4.1 全量 `pytest -q` 通过（新增 + 既有）
- [ ] 4.2 `compileall -q app` 通过
- [ ] 4.3 代码走读：清理保留画像/知识库、搜索各源正确、横幅轮询无泄漏
